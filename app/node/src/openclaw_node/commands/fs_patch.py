"""Proposal-gated patch command handler (P3.2.4 surface).

Implements ``fs.patch`` — apply a unified diff to a file within the allowed
roots, capturing prior bytes to the backup store before mutating.

Uses the system ``patch`` binary (``/usr/bin/patch`` or whatever is on
``PATH``) via :mod:`subprocess` so that hunk application is handled by a
well-tested, standards-conformant implementation.  The diff is applied to a
copy of the original in a temp directory; on success the result is written
to the live path atomically via :func:`~openclaw_node.commands.fs_write._atomic_write`.

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
import subprocess
import tempfile
from pathlib import Path
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

_PATCH_TIMEOUT_S: Final[int] = 30


def _get_store() -> BackupStore:
    from openclaw_node.commands.fs_write import _get_store as _fs_write_get_store

    return _fs_write_get_store()


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
        dry_run: If True, validate only; returned bytes are empty.

    Returns:
        ``(patched_bytes, hunks_applied)`` where *hunks_applied* is the
        number of hunks applied (estimated from ``patch`` stdout).

    Raises:
        FileNotFoundError: If the ``patch`` binary is not installed.
        subprocess.TimeoutExpired: If patch takes longer than the timeout.
        RuntimeError: If ``patch`` exits non-zero (hunk failures, etc.).
    """
    with tempfile.TemporaryDirectory(prefix="oc_patch_") as tmpdir:
        orig_file = Path(tmpdir) / "original"
        out_file = Path(tmpdir) / "patched"
        orig_file.write_bytes(original)

        cmd = [
            "patch",
            "--unified",
            "--forward",
            "--reject-file=-",  # discard reject files rather than writing to disk
        ]
        if dry_run:
            cmd.append("--dry-run")
        cmd += ["--output", str(out_file), str(orig_file)]

        result = subprocess.run(
            cmd,
            input=patch_text.encode(),
            capture_output=True,
            timeout=_PATCH_TIMEOUT_S,
        )

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            raise RuntimeError(stderr or "patch exited non-zero")

        # Count applied hunks from stdout ("Hunk #N succeeded" lines).
        stdout = result.stdout.decode(errors="replace")
        hunks = stdout.count("succeeded") or stdout.count("Hunk #")

        if dry_run:
            return b"", hunks

        patched = out_file.read_bytes()
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
    except FileNotFoundError:
        return _error("PATCH_BINARY_NOT_FOUND", "The 'patch' binary is not installed")
    except subprocess.TimeoutExpired:
        return _error("PATCH_TIMEOUT", f"Patch did not complete within {_PATCH_TIMEOUT_S}s")
    except RuntimeError as exc:
        _LOG.error("patch failed for %r: %s", path, exc)
        return _error("PATCH_FAILED", "Patch did not apply cleanly; check server logs for details")

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
