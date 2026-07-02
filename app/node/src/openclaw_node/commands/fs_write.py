"""Proposal-gated filesystem write commands (P3.2.2 surface).

Implements:

- ``fs.write``   — overwrite a file atomically (with prior-byte capture).
- ``fs.restore`` — restore a prior version from the backup store.
- ``fs.history`` — list recorded versions for a path (read-only).
- ``fs.diff``    — unified diff between two stored versions (read-only).

Write commands (``fs.write``, ``fs.restore``) follow the mutation control
policy in ``docs/PLAN.md``:

- **Protected roots** (``/config``, ``/addons``, ``/ssl``): always
  proposal-gated.  These commands return a ``PROPOSAL_REQUIRED`` error when
  the target path falls under a protected root.  The gateway-side proposal
  bridge that will accept and apply such proposals ships in P3.3.
- **Unprotected paths** with ``agent_bridge=false``: applied immediately.
  The prior bytes are captured to the backup store before the write so every
  mutation is reversible via ``fs.restore``.

Read-only commands (``fs.history``, ``fs.diff``) do not gate on proposal
status; they are always available.

``fs.diff`` reads the **backup store** only; it does not open the live file
unless ``to_version`` is ``None`` (compare a stored version against disk).
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any, Final

from openclaw_node.backup_store import BackupStore, BackupStoreError, VersionNotFoundError
from openclaw_node.config import allowed_roots_for_env
from openclaw_node.safe_fd import atomic_write_safe, read_bytes_safe
from openclaw_node.safe_path import NoAllowedRootsError, OutOfBoundsError, resolve_safe

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

# Protected roots: writes here are always proposal-gated, no override.
_PROTECTED_ROOTS: Final[frozenset[str]] = frozenset({"/config", "/addons", "/ssl"})

# .storage/ writes are refused outright (not even proposal-gatable).
_STORAGE_SUFFIX: Final[str] = "/.storage/"


# ---------------------------------------------------------------------------
# Backup store singleton
# ---------------------------------------------------------------------------


def _backup_root() -> Path:
    """Return the backup store root for the current run mode.

    Returns:
        ``/share/openclaw-backups`` in add-on mode; the value of
        ``OPENCLAW_BACKUP_ROOT`` env var (or a home-dir default) in standalone.
    """
    if os.environ.get("SUPERVISOR_TOKEN"):
        return Path("/share/openclaw-backups")
    return Path(
        os.environ.get(
            "OPENCLAW_BACKUP_ROOT",
            str(Path.home() / ".openclaw" / "hass-node" / "backups"),
        )
    )


_store: BackupStore | None = None


def _get_store() -> BackupStore:
    """Return the module-level :class:`BackupStore` singleton.

    Initialises the store lazily on first call.

    Returns:
        The shared :class:`BackupStore` instance.
    """
    global _store
    if _store is None:
        _store = BackupStore(_backup_root())
    return _store


def _reset_store_for_testing(root: Path) -> None:
    """Re-initialise the module-level store against *root*.

    This helper is **only** for use in tests.  Production code never calls
    it.

    Args:
        root: Directory to use as the backup store root.
    """
    global _store
    _store = BackupStore(root)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Build a structured error payload.

    Args:
        code: Machine-readable error code.
        message: Human-readable description.
        **extra: Optional extra context fields.

    Returns:
        Dict with ``ok=False``, ``error``, ``message``, and any extras.
    """
    payload: dict[str, Any] = {"ok": False, "error": code, "message": message}
    payload.update(extra)
    return payload


def _resolve_write_target(path: str) -> Path | dict[str, Any]:
    """Resolve a write target path within the allowed roots.

    Args:
        path: Caller-supplied absolute path.

    Returns:
        The resolved :class:`Path` if valid, or an error dict if the path is
        outside the allowed roots or has no roots configured.
    """
    roots = allowed_roots_for_env()
    try:
        return resolve_safe(path, roots)
    except NoAllowedRootsError:
        return _error("NO_ALLOWED_ROOTS", "No filesystem roots configured")
    except OutOfBoundsError:
        return _error("PATH_NOT_ALLOWED", f"Path is outside the allowed roots: {path!r}")


def _is_protected(path: str) -> bool:
    """Return ``True`` if *path* is inside a protected root.

    Args:
        path: Caller-supplied absolute path (may or may not be resolved).

    Returns:
        ``True`` when the path starts with ``/config/``, ``/addons/``, or
        ``/ssl/``, or equals one of those roots.
    """
    norm = path if path.endswith("/") else path + "/"
    return any(norm.startswith(root + "/") or path == root for root in _PROTECTED_ROOTS)


def _is_storage(path: str) -> bool:
    """Return ``True`` if *path* is inside ``/config/.storage/``.

    Args:
        path: Caller-supplied absolute path.

    Returns:
        ``True`` when the path falls under any ``.storage/`` directory within
        ``/config``.
    """
    return "/config/.storage/" in path or path.endswith("/config/.storage")


def _decode_content(content: str, encoding: str) -> bytes | dict[str, Any]:
    """Decode caller-supplied content to bytes.

    Args:
        content: The content string from the command params.
        encoding: One of ``"utf-8"`` or ``"base64"``.

    Returns:
        Decoded bytes, or an error dict if decoding fails.
    """
    if encoding == "base64":
        try:
            return base64.b64decode(content)
        except Exception as exc:
            return _error("INVALID_CONTENT", f"base64 decode failed: {exc}")
    if encoding == "utf-8":
        return content.encode("utf-8")
    return _error("INVALID_ENCODING", f"Unknown encoding: {encoding!r}; use utf-8 or base64")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def handle_fs_write(params: dict[str, Any]) -> dict[str, Any]:
    """Write content to a file, capturing prior bytes to the backup store.

    Params:
        path (str): Absolute path of the file to write.
        content (str): File content encoded as specified by *encoding*.
        encoding (str, optional): ``"utf-8"`` (default) or ``"base64"``.
        proposal_id (str, optional): Agent-bridge proposal ID to record.
            Defaults to ``"direct"`` for unproposalled writes.
        agent_bridge (bool, optional): When ``True`` (default for protected
            paths), emit a proposal instead of applying directly.  May only be
            set to ``False`` for paths outside the protected roots.
        actor (str, optional): Identity string recorded in the backup index.

    Returns:
        ``{ok: True, path, size, sha256, proposal_id}`` on success, or an
        error dict.
    """
    path = str(params.get("path", ""))
    if not path:
        return _error("MISSING_PARAM", "path is required")

    content_raw = params.get("content")
    if content_raw is None:
        return _error("MISSING_PARAM", "content is required")

    encoding = str(params.get("encoding", "utf-8"))
    proposal_id = str(params.get("proposal_id", "direct"))
    actor = str(params.get("actor", "agent"))
    # agent_bridge may gate additional unprotected paths; protected roots are
    # unconditionally gated regardless of this flag.
    agent_bridge = bool(params.get("agent_bridge", False))

    if _is_storage(path):
        return _error(
            "STORAGE_READONLY",
            "Writes to .storage/ are refused; use the HA REST config API instead",
        )

    # Protected roots are ALWAYS proposal-gated; caller cannot override with agent_bridge=False.
    if _is_protected(path) or agent_bridge:
        return _error(
            "PROPOSAL_REQUIRED",
            (f"Path {path!r} requires a proposal; gateway-side proposal bridge ships in P3.3"),
        )

    resolved = _resolve_write_target(path)
    if isinstance(resolved, dict):
        return resolved

    # Post-resolution: symlink or traversal may redirect into a protected zone.
    # (_is_storage paths are a subset of /config, so the protected check covers them.)
    if _is_protected(str(resolved)):
        return _error(
            "PROPOSAL_REQUIRED",
            f"Resolved path {resolved!r} is under a protected root",
        )

    content_bytes = _decode_content(str(content_raw), encoding)
    if isinstance(content_bytes, dict):
        return content_bytes

    roots = allowed_roots_for_env()

    # Capture prior bytes (empty if file does not yet exist) via a
    # TOCTOU-safe fd read so a symlink at the path can't redirect the read
    # between resolve and capture.
    try:
        prior_bytes = read_bytes_safe(path, roots, missing_ok=True)
    except OutOfBoundsError:
        return _error("PATH_NOT_ALLOWED", f"Path is outside the allowed roots: {path!r}")
    except OSError as exc:
        return _error("READ_ERROR", f"Cannot read prior bytes: {exc}")

    store = _get_store()
    try:
        version = store.capture(
            path,
            prior_bytes,
            proposal_id=proposal_id,
            op="write",
            actor=actor,
        )
    except BackupStoreError as exc:
        _LOG.error("backup capture failed for %r: %s", path, exc)
        return _error("BACKUP_ERROR", "Backup capture failed; write aborted")

    try:
        atomic_write_safe(path, roots, content_bytes)
    except OutOfBoundsError:
        return _error("PATH_NOT_ALLOWED", f"Path is outside the allowed roots: {path!r}")
    except OSError as exc:
        _LOG.error("write failed for %r: %s", path, exc)
        return _error("WRITE_ERROR", f"Write failed: {exc}")

    return {
        "ok": True,
        "path": path,
        "size": len(content_bytes),
        "sha256": version.sha256,
        "proposal_id": proposal_id,
    }


def handle_fs_restore(params: dict[str, Any]) -> dict[str, Any]:
    """Restore a prior version of a file from the backup store.

    Params:
        path (str): Absolute path of the file to restore.
        version (int, optional): 1-indexed version number (``-1`` = latest).
        proposal_id (str, optional): Restore the version associated with this
            proposal ID.
        at (str, optional): Restore the latest version captured at or before
            this ISO-8601 timestamp.
        actor (str, optional): Identity string recorded on the restore capture.
        agent_bridge (bool, optional): When ``True``, emit a proposal instead
            of applying directly.  Defaults to ``True`` for protected paths.

    Returns:
        ``{ok: True, path, size, sha256, restored_from_proposal}`` on
        success, or an error dict.
    """
    path = str(params.get("path", ""))
    if not path:
        return _error("MISSING_PARAM", "path is required")

    version_param = params.get("version")
    proposal_param = params.get("proposal_id")
    at_param = params.get("at")
    actor = str(params.get("actor", "agent"))
    agent_bridge = bool(params.get("agent_bridge", False))

    if _is_storage(path):
        return _error(
            "STORAGE_READONLY",
            "Writes to .storage/ are refused; use the HA REST config API instead",
        )

    # Protected roots are ALWAYS proposal-gated; caller cannot override with agent_bridge=False.
    if _is_protected(path) or agent_bridge:
        return _error(
            "PROPOSAL_REQUIRED",
            (
                f"Path {path!r} requires a proposal for restore; "
                "gateway-side proposal bridge ships in P3.3"
            ),
        )

    resolved = _resolve_write_target(path)
    if isinstance(resolved, dict):
        return resolved

    # Post-resolution: symlink or traversal may redirect into a protected zone.
    # (_is_storage paths are a subset of /config, so the protected check covers them.)
    if _is_protected(str(resolved)):
        return _error(
            "PROPOSAL_REQUIRED",
            f"Resolved path {resolved!r} is under a protected root",
        )

    store = _get_store()

    # Build kwargs for resolve_version from whichever selector was supplied.
    sel_kwargs: dict[str, Any] = {}
    sel_count = 0
    if version_param is not None:
        try:
            sel_kwargs["version"] = int(version_param)
            sel_count += 1
        except (TypeError, ValueError):
            return _error("INVALID_PARAM", "version must be an integer")
    if proposal_param is not None:
        sel_kwargs["proposal_id"] = str(proposal_param)
        sel_count += 1
    if at_param is not None:
        sel_kwargs["at"] = str(at_param)
        sel_count += 1

    if sel_count != 1:
        return _error(
            "INVALID_PARAM",
            "Exactly one of version, proposal_id, or at is required",
        )

    try:
        stored_version = store.resolve_version(path, **sel_kwargs)
    except (VersionNotFoundError, ValueError, BackupStoreError) as exc:
        return _error("VERSION_NOT_FOUND", str(exc))

    try:
        restore_bytes = store.fetch_object(stored_version.sha256)
    except BackupStoreError as exc:
        return _error("OBJECT_MISSING", str(exc))

    roots = allowed_roots_for_env()

    # Capture current bytes as a restore entry before overwriting.
    try:
        prior_bytes = read_bytes_safe(path, roots, missing_ok=True)
    except OutOfBoundsError:
        return _error("PATH_NOT_ALLOWED", f"Path is outside the allowed roots: {path!r}")
    except OSError as exc:
        return _error("READ_ERROR", f"Cannot read prior bytes: {exc}")

    try:
        store.capture(
            path,
            prior_bytes,
            proposal_id=f"restore-from-{stored_version.proposal_id}",
            op="restore",
            actor=actor,
        )
    except BackupStoreError as exc:
        _LOG.error("backup capture before restore failed for %r: %s", path, exc)
        return _error("BACKUP_ERROR", "Pre-restore capture failed; restore aborted")

    try:
        atomic_write_safe(path, roots, restore_bytes)
    except OutOfBoundsError:
        return _error("PATH_NOT_ALLOWED", f"Path is outside the allowed roots: {path!r}")
    except OSError as exc:
        _LOG.error("restore write failed for %r: %s", path, exc)
        return _error("WRITE_ERROR", f"Restore write failed: {exc}")

    return {
        "ok": True,
        "path": path,
        "size": len(restore_bytes),
        "sha256": stored_version.sha256,
        "restored_from_proposal": stored_version.proposal_id,
    }


def handle_fs_history(params: dict[str, Any]) -> dict[str, Any]:
    """Return the recorded version history for a path.

    This command is read-only and does not require a proposal.

    Params:
        path (str): Absolute path to query.

    Returns:
        ``{ok: True, path, versions: [{ts, proposal_id, sha256, size, op,
        actor, prev_sha256, evicted}, ...]}`` sorted oldest-first.
    """
    path = str(params.get("path", ""))
    if not path:
        return _error("MISSING_PARAM", "path is required")

    store = _get_store()
    try:
        versions = store.history(path)
    except BackupStoreError as exc:
        return _error("STORE_ERROR", str(exc))

    return {
        "ok": True,
        "path": path,
        "versions": [
            {
                "ts": v.ts,
                "proposal_id": v.proposal_id,
                "sha256": v.sha256,
                "size": v.size,
                "op": v.op,
                "actor": v.actor,
                "prev_sha256": v.prev_sha256,
                "evicted": v.evicted,
            }
            for v in versions
        ],
    }


def handle_fs_diff(params: dict[str, Any]) -> dict[str, Any]:
    """Return a unified diff between two stored versions.

    This command is read-only.

    Params:
        path (str): Absolute path.
        from_version (int | str): 1-indexed version position or sha256 hex.
        to_version (int | str | None, optional): 1-indexed version position,
            sha256 hex, or ``None`` to compare *from_version* against the
            current live bytes on disk.

    Returns:
        ``{ok: True, path, diff: "<unified diff text>"}`` or an error dict.
    """
    path = str(params.get("path", ""))
    if not path:
        return _error("MISSING_PARAM", "path is required")

    from_raw = params.get("from_version")
    if from_raw is None:
        return _error("MISSING_PARAM", "from_version is required")
    to_raw = params.get("to_version")

    def _coerce(v: Any) -> int | str:
        if isinstance(v, int):
            return v
        s = str(v)
        # 64-char hex → sha256 digest; do not coerce to int even if all-digit.
        if len(s) == 64 and all(c in "0123456789abcdef" for c in s.lower()):
            return s
        try:
            return int(s)
        except ValueError:
            return s

    from_version = _coerce(from_raw)
    to_version = None if to_raw is None else _coerce(to_raw)

    store = _get_store()
    try:
        diff_text = store.diff(path, from_version=from_version, to_version=to_version)
    except (BackupStoreError, VersionNotFoundError) as exc:
        return _error("DIFF_ERROR", str(exc))

    return {"ok": True, "path": path, "diff": diff_text}
