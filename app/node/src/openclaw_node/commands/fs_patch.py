"""Proposal-gated patch command handler (P3.2.4 surface).

Implements ``fs.patch`` — apply a unified diff to a file within the allowed
roots, capturing prior bytes to the backup store before mutating.

Uses a **pure-Python** unified-diff applier so the command works on any
add-on image without depending on the ``patch`` binary being installed.
The applier accepts single-file unified diffs (any number of hunks),
validates context lines, and applies hunks in order.

Mutation policy is identical to ``fs.write``:

- **Protected roots** (``/config``, ``/addons``, ``/ssl``): always
  ``PROPOSAL_REQUIRED``.
- **``.storage/`` paths**: always ``STORAGE_READONLY``.
- **Post-resolution symlink check**: resolved paths are re-validated after
  :func:`~openclaw_node.safe_path.resolve_safe`.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Final

from openclaw_node.backup_store import BackupStore, BackupStoreError
from openclaw_node.commands.fs_write import (
    _error,
    _is_protected,
    _is_storage,
    _reset_store_for_testing,
    _resolve_write_target,
)
from openclaw_node.config import allowed_roots_for_env
from openclaw_node.safe_fd import atomic_write_safe, read_bytes_safe
from openclaw_node.safe_path import OutOfBoundsError

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

# Re-export for test convenience.
reset_store_for_testing = _reset_store_for_testing


def _get_store() -> BackupStore:
    from openclaw_node.commands.fs_write import _get_store as _fs_write_get_store

    return _fs_write_get_store()


class PatchApplyError(RuntimeError):
    """Raised when a unified diff cannot be applied to the source bytes."""


_HUNK_HEADER_RE: Final[re.Pattern[str]] = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


def _split_lines_keep_ends(data: bytes) -> list[bytes]:
    """Split *data* on newline boundaries, keeping trailing newlines.

    Unlike :func:`bytes.splitlines`, this preserves the terminator on each
    line so we can round-trip through ``b"".join(...)`` without loss.  The
    final chunk may be empty (when the input ends with a newline) or
    unterminated (when it does not).
    """
    lines: list[bytes] = []
    start = 0
    length = len(data)
    while start < length:
        idx = data.find(b"\n", start)
        if idx == -1:
            lines.append(data[start:])
            break
        lines.append(data[start : idx + 1])
        start = idx + 1
    return lines


def _apply_unified_diff(original: bytes, patch_text: str) -> tuple[bytes, int]:
    r"""Apply a unified diff to *original*, returning ``(patched, hunks)``.

    The parser recognises the ``@@ -o,c +n,c @@`` hunk-header form and the
    per-line prefixes ``" "`` (context), ``"-"`` (delete), ``"+"`` (add),
    and ``"\\ No newline at end of file"`` (missing-final-newline marker).
    File-header lines (``---``/``+++``) and any lines before the first
    ``@@`` are ignored so callers can include or omit them freely.

    Args:
        original: Bytes to patch.
        patch_text: Unified diff string.

    Returns:
        Tuple of ``(patched_bytes, hunks_applied)``.

    Raises:
        PatchApplyError: If the diff is malformed, or a hunk's context/delete
            lines do not match *original* at the expected position.
    """
    src_lines = _split_lines_keep_ends(original)
    # Split on "\n" only (not str.splitlines) so any trailing "\r" stays on
    # the body — required to correctly round-trip patches produced from
    # CRLF/mixed-newline source files (Codex-review #242 finding).
    patch_lines = patch_text.split("\n")
    # A trailing "\n" on the patch text produces a spurious empty element;
    # drop it so it isn't misread as a blank context line.
    if patch_lines and patch_lines[-1] == "":
        patch_lines.pop()

    # Fast-forward past file headers to the first hunk header.  Strip a
    # trailing "\r" on control lines so ``startswith`` comparisons don't
    # care about the patch file's own line endings.
    def _control(line: str) -> str:
        return line[:-1] if line.endswith("\r") else line

    i = 0
    while i < len(patch_lines) and not _control(patch_lines[i]).startswith("@@"):
        i += 1

    out_lines: list[bytes] = []
    src_cursor = 0
    hunks_applied = 0

    while i < len(patch_lines):
        header = _control(patch_lines[i])
        match = _HUNK_HEADER_RE.match(header)
        if not match:
            raise PatchApplyError(f"malformed hunk header: {header!r}")  # noqa: TRY003
        old_start = int(match.group("old_start"))
        old_count_raw = match.group("old_count")
        new_count_raw = match.group("new_count")
        # Per unified-diff spec: omitted count means 1.  A count of 0 means
        # the range is empty (pure add / pure delete).
        declared_old = int(old_count_raw) if old_count_raw is not None else 1
        declared_new = int(new_count_raw) if new_count_raw is not None else 1

        # Copy through any source lines between the previous hunk and this one.
        # Per unified-diff spec, when the old range is empty (pure add), old_start
        # names the source line *after which* the insertion goes, so the cursor is
        # `old_start` rather than `old_start - 1`.  old_start == 0 with an empty
        # old range is the empty-file / prepend-to-empty case, cursor 0.
        if declared_old == 0:
            hunk_src_index = old_start if old_start > 0 else 0
            if hunk_src_index > len(src_lines):
                raise PatchApplyError(  # noqa: TRY003
                    f"pure-add hunk at old_start={old_start} is beyond end of source "
                    f"({len(src_lines)} lines)"
                )
        else:
            hunk_src_index = (old_start - 1) if old_start > 0 else 0
        if hunk_src_index < src_cursor:
            raise PatchApplyError(f"overlapping or out-of-order hunk at line {old_start}")  # noqa: TRY003
        out_lines.extend(src_lines[src_cursor:hunk_src_index])
        src_cursor = hunk_src_index

        # Track actual old/new counts walked by the hunk body so we can
        # reject truncated / malformed hunks whose declared counts do not
        # match the body (Codex-review #242 blocking finding).
        actual_old = 0
        actual_new = 0

        i += 1
        while i < len(patch_lines) and not _control(patch_lines[i]).startswith("@@"):
            line = patch_lines[i]
            i += 1
            if not line:
                # An empty line inside a hunk is treated as a context blank
                # line: a single "\n" that must be present in the source.
                expected = b"\n"
                if src_cursor >= len(src_lines) or src_lines[src_cursor] != expected:
                    raise PatchApplyError(  # noqa: TRY003
                        f"context mismatch at src line {src_cursor + 1}: expected blank line"
                    )
                out_lines.append(expected)
                src_cursor += 1
                actual_old += 1
                actual_new += 1
                continue
            tag = line[0]
            body = line[1:]
            # Lookahead: if the next line is "\ No newline at end of file",
            # the current body has no trailing newline; otherwise append "\n".
            no_newline = i < len(patch_lines) and _control(patch_lines[i]).startswith(
                "\\ No newline"
            )
            if no_newline:
                i += 1
                body_bytes = body.encode("utf-8")
            else:
                body_bytes = (body + "\n").encode("utf-8")
            if tag == " ":
                if src_cursor >= len(src_lines) or src_lines[src_cursor] != body_bytes:
                    raise PatchApplyError(  # noqa: TRY003
                        f"context mismatch at src line {src_cursor + 1}: "
                        f"expected {body_bytes!r}, "
                        f"got {src_lines[src_cursor] if src_cursor < len(src_lines) else b''!r}"
                    )
                out_lines.append(body_bytes)
                src_cursor += 1
                actual_old += 1
                actual_new += 1
            elif tag == "-":
                if src_cursor >= len(src_lines) or src_lines[src_cursor] != body_bytes:
                    raise PatchApplyError(  # noqa: TRY003
                        f"delete mismatch at src line {src_cursor + 1}: "
                        f"expected {body_bytes!r}, "
                        f"got {src_lines[src_cursor] if src_cursor < len(src_lines) else b''!r}"
                    )
                src_cursor += 1
                actual_old += 1
            elif tag == "+":
                out_lines.append(body_bytes)
                actual_new += 1
            elif tag == "\\":
                # Bare "\ No newline" marker without an associated body line
                # (should not happen with the lookahead above, but treated as
                # a no-op for robustness).
                if not line.startswith("\\ "):
                    raise PatchApplyError(f"unrecognised marker line: {line!r}")  # noqa: TRY003
            else:
                raise PatchApplyError(f"unrecognised hunk line prefix: {line!r}")  # noqa: TRY003

        # Verify the body matched the declared hunk counts.  Rejects
        # truncated hunks (fewer body lines than declared) and hunks with
        # inconsistent counts vs. body prefixes.
        if actual_old != declared_old or actual_new != declared_new:
            raise PatchApplyError(  # noqa: TRY003
                f"hunk count mismatch at old_start={old_start}: "
                f"header declared -{declared_old} +{declared_new}, "
                f"body walked -{actual_old} +{actual_new}"
            )
        hunks_applied += 1

    # Reject patches that carry no hunks at all (empty or headers-only).
    # Without this, ``fs.patch`` silently no-ops on garbage input and
    # returns ``hunks_applied=0``, which callers cannot distinguish from
    # "diff applied cleanly with no changes."
    if hunks_applied == 0:
        raise PatchApplyError("no hunks found in patch")  # noqa: TRY003

    # Copy any tail lines after the last hunk.
    out_lines.extend(src_lines[src_cursor:])
    return b"".join(out_lines), hunks_applied


def _run_patch(
    original: bytes,
    patch_text: str,
    *,
    dry_run: bool = False,
) -> tuple[bytes, int]:
    """Apply *patch_text* (unified diff) to *original* bytes.

    Args:
        original: The original file bytes.
        patch_text: Unified diff string.
        dry_run: If True, still validates the diff by applying it in-memory,
            but returned bytes are empty so the caller does not overwrite.

    Returns:
        ``(patched_bytes, hunks_applied)`` where *hunks_applied* is the
        number of hunks applied.

    Raises:
        PatchApplyError: If the diff is malformed or does not apply cleanly.
    """
    patched, hunks = _apply_unified_diff(original, patch_text)
    if dry_run:
        return b"", hunks
    return patched, hunks


def handle_fs_patch(params: dict[str, Any]) -> dict[str, Any]:
    """Apply a unified diff to a file within the allowed roots.

    Captures the current file bytes to the backup store before patching so
    the file can be restored via ``fs.restore``.

    Params:
        path (str): Absolute path of the file to patch.
        patch (str): Unified diff to apply.
        dry_run (bool, optional): If True, validate the patch but do not
            write.  Returns ``hunks_applicable`` instead of ``sha256``.
        agent_bridge (bool, optional): Proposal-gate the operation.
            Defaults to ``True`` when the path is under a protected root.
        proposal_id (str, optional): Proposal ID for the backup index.
        actor (str, optional): Identity for the backup index.

    Returns:
        ``{ok: True, path, sha256, size, hunks_applied}`` on success,
        ``{ok: True, path, dry_run: True, hunks_applicable: N}`` for dry
        runs, or an error dict.
    """
    path = str(params.get("path", ""))
    patch_text = str(params.get("patch", ""))
    if not path:
        return _error("MISSING_PARAM", "path is required")
    if not patch_text:
        return _error("MISSING_PARAM", "patch is required")

    dry_run = bool(params.get("dry_run", False))
    proposal_id = str(params.get("proposal_id", "direct"))
    actor = str(params.get("actor", "agent"))
    agent_bridge = bool(params.get("agent_bridge", False))

    if _is_storage(path):
        return _error(
            "STORAGE_READONLY",
            "Writes to .storage/ are refused; use the HA REST config API instead",
        )
    if _is_protected(path) or agent_bridge:
        return _error(
            "PROPOSAL_REQUIRED",
            f"Path {path!r} requires a proposal; gateway-side proposal bridge ships in P3.3",
        )

    resolved = _resolve_write_target(path)
    if isinstance(resolved, dict):
        return resolved
    if _is_protected(str(resolved)):
        return _error(
            "PROPOSAL_REQUIRED",
            f"Resolved path {resolved!r} is under a protected root",
        )

    roots = allowed_roots_for_env()
    try:
        original_bytes = read_bytes_safe(path, roots)
    except OutOfBoundsError:
        return _error("PATH_NOT_ALLOWED", f"Path is outside the allowed roots: {path!r}")
    except FileNotFoundError:
        return _error("NOT_FOUND", f"Path does not exist: {path!r}")
    except OSError as exc:
        return _error("READ_ERROR", f"Cannot read file: {exc}")

    # Apply (or dry-run) the patch before capturing backup, so we don't
    # pollute the store when the diff is malformed.
    try:
        patched_bytes, hunks = _run_patch(original_bytes, patch_text, dry_run=dry_run)
    except PatchApplyError as exc:
        _LOG.error("patch failed for %r: %s", path, exc)
        return _error("PATCH_FAILED", f"Patch did not apply cleanly: {exc}")

    if dry_run:
        return {"ok": True, "path": path, "dry_run": True, "hunks_applicable": hunks}

    # Capture prior bytes to backup store.
    store = _get_store()
    try:
        store.capture(
            path,
            original_bytes,
            proposal_id=proposal_id,
            op="write",
            actor=actor,
        )
    except BackupStoreError as exc:
        _LOG.error("backup capture before patch failed for %r: %s", path, exc)
        return _error("BACKUP_ERROR", "Backup capture failed; patch aborted")

    try:
        atomic_write_safe(path, roots, patched_bytes)
    except OutOfBoundsError:
        return _error("PATH_NOT_ALLOWED", f"Path is outside the allowed roots: {path!r}")
    except OSError as exc:
        return _error("WRITE_ERROR", f"Atomic write failed: {exc}")

    sha256 = hashlib.sha256(patched_bytes).hexdigest()
    return {
        "ok": True,
        "path": path,
        "sha256": sha256,
        "size": len(patched_bytes),
        "hunks_applied": hunks,
    }
