"""Proposal-gated move and delete command handlers (P3.2.3 surface).

Implements:

- ``fs.move``   — atomically move a file within the allowed roots, capturing
  prior bytes (if the destination exists) to the backup store.
- ``fs.delete`` — move a file to the system trash (FreeDesktop.org spec via
  :mod:`send2trash` or an OpenClaw-managed trash directory fallback), capturing
  prior bytes to the backup store so ``fs.restore`` can recover the file.

Both commands follow the same mutation control policy as ``fs.write``:

- **Protected roots** (``/config``, ``/addons``, ``/ssl``): always
  ``PROPOSAL_REQUIRED``.
- **``.storage/`` paths**: always ``STORAGE_READONLY``.
- **Post-resolution symlink check**: resolved paths are re-validated after
  :func:`~openclaw_node.safe_path.resolve_safe` to catch traversal attacks.

The ``send2trash`` package is the primary trash backend.  When it is not
installed, a fallback moves the file to an OpenClaw-managed trash directory
(``OPENCLAW_TRASH_DIR`` or ``/share/openclaw-trash``).
"""

from __future__ import annotations

import datetime as _dt
import errno
import logging
import os
import shutil
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
from openclaw_node.safe_fd import read_bytes_safe, replace_safe
from openclaw_node.safe_path import OutOfBoundsError

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

# Re-export store helpers so this module's tests can use _get_store / reset.
_store: BackupStore | None = None


def _get_store() -> BackupStore:
    """Return the shared :class:`BackupStore` (same root as ``fs_write``)."""
    from openclaw_node.commands.fs_write import _get_store as _fs_write_get_store

    return _fs_write_get_store()


# Re-export for testing convenience.
reset_store_for_testing = _reset_store_for_testing


# ---------------------------------------------------------------------------
# Trash helpers
# ---------------------------------------------------------------------------


def _trash_dir() -> Path:
    """Return the fallback OpenClaw trash directory."""
    return Path(os.environ.get("OPENCLAW_TRASH_DIR", "/share/openclaw-trash"))


def purge_trash_entries_for(path: str) -> int:
    """Remove openclaw-managed trash entries for *path* (fallback trash only).

    Only touches the OpenClaw fallback trash directory (``_trash_dir()``),
    not the FreeDesktop system trash — ``send2trash`` handles system trash
    lifecycle independently.  Matches entries by basename prefix
    ``"<name>."`` since ``_trash_file`` appends a timestamp suffix.

    Args:
        path: Absolute path whose trash entries should be purged.

    Returns:
        Number of trash entries removed.
    """
    basename = os.path.basename(path)
    if not basename:
        return 0
    trash = _trash_dir()
    if not trash.exists():
        return 0
    removed = 0
    prefix = f"{basename}."
    for entry in trash.iterdir():
        if entry.name.startswith(prefix):
            try:
                if entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
            except OSError as exc:
                _LOG.warning("failed to purge trash entry %r: %s", entry, exc)
                continue
            removed += 1
    return removed


def _trash_file(path: Path) -> str:
    """Move *path* to the system trash or the OpenClaw fallback trash dir.

    Tries :mod:`send2trash` first (FreeDesktop.org spec).  Falls back to
    moving the file under :func:`_trash_dir` when ``send2trash`` is not
    installed.

    Args:
        path: The file to trash; must exist.

    Returns:
        A human-readable description of where the file was moved to.

    Raises:
        OSError: If the trash operation fails.
    """
    try:
        from send2trash import send2trash as _s2t  # type: ignore[import-untyped]

        _s2t(str(path))
    except ImportError:
        pass
    else:
        return "system trash"

    # Fallback: move to the openclaw trash directory.
    trash = _trash_dir()
    trash.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%S%f")
    dest = trash / f"{path.name}.{ts}"
    shutil.move(str(path), str(dest))
    return str(dest)


# ---------------------------------------------------------------------------
# Move helper (isolated so tests can patch without hitting the backup store)
# ---------------------------------------------------------------------------


def _move_file(src: Path, dst: Path) -> None:
    """Rename *src* to *dst* using ``os.replace`` (atomic within one filesystem).

    Raises ``OSError`` (including ``errno.EXDEV``) on failure; never falls back
    to copy-then-unlink so callers can distinguish "no mutation" from
    "partial mutation".

    Args:
        src: Existing source path.
        dst: Destination path (parent must already exist).

    Raises:
        OSError: On any rename failure, including cross-device (EXDEV).
    """
    os.replace(str(src), str(dst))


# ---------------------------------------------------------------------------
# Shared pre-flight for write operations
# ---------------------------------------------------------------------------


def _write_preflight(path: str, agent_bridge: bool) -> dict[str, Any] | None:
    """Apply storage and protected-root policy checks.

    Args:
        path: Raw caller-supplied path.
        agent_bridge: Whether the call is routed through the proposal bridge.

    Returns:
        An error dict if the path is blocked, ``None`` if clear to proceed.
    """
    if _is_storage(path):
        return _error(
            "STORAGE_READONLY",
            "Writes to .storage/ are refused; use the HA REST config API instead",
        )
    if _is_protected(path) or agent_bridge:
        return _error(
            "PROPOSAL_REQUIRED",
            (f"Path {path!r} requires a proposal; gateway-side proposal bridge ships in P3.3"),
        )
    return None


def _post_resolution_check(resolved: Path) -> dict[str, Any] | None:
    """Check the resolved path after symlink/traversal expansion.

    Args:
        resolved: The resolved :class:`Path` returned by ``_resolve_write_target``.

    Returns:
        An error dict if the resolved path is blocked, ``None`` if clear.
    """
    if _is_protected(str(resolved)):
        return _error(
            "PROPOSAL_REQUIRED",
            f"Resolved path {resolved!r} is under a protected root",
        )
    return None


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def handle_fs_move(params: dict[str, Any]) -> dict[str, Any]:
    """Move a file atomically within the allowed roots.

    Captures the current bytes of the *destination* file (if it exists) to the
    backup store before overwriting it (``op="move-dst"``), and captures the
    source bytes under ``op="move-src"`` to preserve the source content.

    Params:
        src (str): Source absolute path.
        dst (str): Destination absolute path.
        agent_bridge (bool, optional): Proposal-gate the operation. Defaults
            to ``True`` when either path is under a protected root.
        proposal_id (str, optional): Proposal ID recorded in the backup index.
            Defaults to ``"direct"``.
        actor (str, optional): Identity recorded in the backup index.

    Returns:
        ``{ok: True, src, dst, size}`` on success, or an error dict.
    """
    src_raw = str(params.get("src", ""))
    dst_raw = str(params.get("dst", ""))
    if not src_raw:
        return _error("MISSING_PARAM", "src is required")
    if not dst_raw:
        return _error("MISSING_PARAM", "dst is required")

    proposal_id = str(params.get("proposal_id", "direct"))
    actor = str(params.get("actor", "agent"))
    agent_bridge = bool(
        params.get("agent_bridge", _is_protected(src_raw) or _is_protected(dst_raw))
    )

    err = _write_preflight(src_raw, agent_bridge)
    if err:
        return err
    err = _write_preflight(dst_raw, agent_bridge)
    if err:
        return err

    src = _resolve_write_target(src_raw)
    if isinstance(src, dict):
        return src
    err = _post_resolution_check(src)
    if err:
        return err

    dst = _resolve_write_target(dst_raw)
    if isinstance(dst, dict):
        return dst
    err = _post_resolution_check(dst)
    if err:
        return err

    if not src.exists():
        return _error("NOT_FOUND", f"Source path does not exist: {src_raw!r}")

    if src == dst:
        return _error("INVALID_PARAM", "src and dst must differ")

    store = _get_store()
    roots = allowed_roots_for_env()

    # Capture destination prior bytes if the destination exists (safe read).
    if dst.exists():
        try:
            dst_prior = read_bytes_safe(dst_raw, roots, missing_ok=True)
        except OutOfBoundsError:
            return _error("PATH_NOT_ALLOWED", f"Path is outside the allowed roots: {dst_raw!r}")
        except OSError as exc:
            return _error("READ_ERROR", f"Cannot read destination prior bytes: {exc}")
        try:
            store.capture(
                dst_raw,
                dst_prior,
                proposal_id=f"pre-move-{proposal_id}",
                op="move-dst",
                actor=actor,
            )
        except BackupStoreError as exc:
            _LOG.error("backup capture of dst failed for %r: %s", dst_raw, exc)
            return _error("BACKUP_ERROR", "Backup capture of destination failed; move aborted")

    # Capture source bytes under the source path as a move-src record (safe read).
    try:
        src_bytes = read_bytes_safe(src_raw, roots)
    except OutOfBoundsError:
        return _error("PATH_NOT_ALLOWED", f"Path is outside the allowed roots: {src_raw!r}")
    except OSError as exc:
        return _error("READ_ERROR", f"Cannot read source: {exc}")
    try:
        store.capture(
            src_raw,
            src_bytes,
            proposal_id=proposal_id,
            op="move-src",
            actor=actor,
        )
    except BackupStoreError as exc:
        _LOG.error("backup capture of src failed for %r: %s", src_raw, exc)
        return _error("BACKUP_ERROR", "Backup capture of source failed; move aborted")

    # Use os.replace which is atomic within a single filesystem on POSIX.
    # Cross-device moves (errno.EXDEV) are rejected; callers should use
    # fs.write + fs.delete for that pattern.  shutil.move's copy-then-unlink
    # fallback is intentionally NOT used here because a partial copy could
    # mutate dst while the command returns MOVE_ERROR, leaving the caller
    # unable to distinguish "no mutation" from "partial mutation".
    #
    # Auto-creating dst.parent was removed in audit bundle 6: a path-string
    # mkdir(parents=True) after path resolution reintroduces a TOCTOU window
    # where a missing parent component can be swapped to a symlink between
    # validation and mkdir. Callers must create the parent themselves (e.g.
    # via a future fs.mkdir command) before invoking fs.move.
    try:
        replace_safe(src_raw, dst_raw, roots)
    except OutOfBoundsError:
        return _error("PATH_NOT_ALLOWED", "Source or destination escapes the allowed roots")
    except OSError as exc:
        _LOG.error("move failed %r → %r: %s", src_raw, dst_raw, exc)
        if exc.errno == errno.EXDEV:
            return _error(
                "CROSS_DEVICE",
                "Cross-device moves are not supported; use fs.write + fs.delete instead",
            )
        return _error("MOVE_ERROR", f"Move failed: {exc}")

    # Carry the version history log to the new path so fs.history/fs.restore
    # against the destination sees the pre-move versions.  Failure to rename
    # is logged but does not fail the move — the file has already been
    # atomically renamed on disk.
    try:
        history_moved = store.rename_history(src_raw, dst_raw)
    except BackupStoreError as exc:
        _LOG.warning("history rename failed %r → %r: %s", src_raw, dst_raw, exc)
        history_moved = False

    return {
        "ok": True,
        "src": src_raw,
        "dst": dst_raw,
        "size": len(src_bytes),
        "history_moved": history_moved,
    }


def handle_fs_delete(params: dict[str, Any]) -> dict[str, Any]:
    """Delete a file by moving it to trash (recoverable via ``fs.restore``).

    Captures prior bytes to the backup store before trashing so the file can
    be recovered even after the system trash is emptied.

    Params:
        path (str): Absolute path of the file to delete.
        agent_bridge (bool, optional): Proposal-gate the operation. Defaults
            to ``True`` when the path is under a protected root.
        proposal_id (str, optional): Proposal ID recorded in the backup index.
        actor (str, optional): Identity recorded in the backup index.

    Returns:
        ``{ok: True, path, sha256, trashed_to}`` on success, or an error dict.
    """
    path = str(params.get("path", ""))
    if not path:
        return _error("MISSING_PARAM", "path is required")

    proposal_id = str(params.get("proposal_id", "direct"))
    actor = str(params.get("actor", "agent"))
    agent_bridge = bool(params.get("agent_bridge", False))

    err = _write_preflight(path, agent_bridge)
    if err:
        return err

    resolved = _resolve_write_target(path)
    if isinstance(resolved, dict):
        return resolved
    err = _post_resolution_check(resolved)
    if err:
        return err

    if not resolved.exists():
        return _error("NOT_FOUND", f"Path does not exist: {path!r}")

    # Capture prior bytes before trashing (safe read defeats symlink swap
    # between resolve and read).
    roots = allowed_roots_for_env()
    try:
        prior_bytes = read_bytes_safe(path, roots)
    except OutOfBoundsError:
        return _error("PATH_NOT_ALLOWED", f"Path is outside the allowed roots: {path!r}")
    except OSError as exc:
        return _error("READ_ERROR", f"Cannot read file: {exc}")

    store = _get_store()
    try:
        version = store.capture(
            path,
            prior_bytes,
            proposal_id=proposal_id,
            op="delete",
            actor=actor,
        )
    except BackupStoreError as exc:
        _LOG.error("backup capture before delete failed for %r: %s", path, exc)
        return _error("BACKUP_ERROR", "Backup capture failed; delete aborted")

    # Trash the file (recoverable).
    try:
        trashed_to = _trash_file(resolved)
    except OSError as exc:
        _LOG.error("trash failed for %r: %s", path, exc)
        return _error("TRASH_ERROR", f"Trash operation failed: {exc}")

    return {
        "ok": True,
        "path": path,
        "sha256": version.sha256,
        "trashed_to": trashed_to,
    }
