"""Read-only filesystem command handlers (P3.1 surface).

Implements the four read-only filesystem commands:

- ``fs.read`` - read a single file's bytes (text or base64).
- ``fs.list`` - list a directory's immediate children.
- ``fs.stat`` - stat a single path.
- ``fs.glob`` - glob within an allowed root.

All paths flow through :func:`openclaw_node.safe_path.resolve_safe` so the
node can only read under the configured roots.  Writes are NOT in this
module; they arrive in P3.2 as proposal-gated commands.

Note:
    ``.storage/`` reads are permitted here for diagnostics.  Writes to
    ``.storage/`` are blocked at the dispatcher level when the write
    commands ship.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat as stat_mod
from pathlib import Path
from typing import Any, Final

from openclaw_node.config import allowed_roots_for_env
from openclaw_node.safe_path import NoAllowedRootsError, OutOfBoundsError, resolve_safe

_DEFAULT_READ_MAX_BYTES: Final[int] = 1 * 1024 * 1024
_HARD_READ_MAX_BYTES: Final[int] = 16 * 1024 * 1024
_DEFAULT_LIST_MAX_ENTRIES: Final[int] = 1000
_HARD_LIST_MAX_ENTRIES: Final[int] = 5000
_DEFAULT_GLOB_MAX_MATCHES: Final[int] = 1000
_HARD_GLOB_MAX_MATCHES: Final[int] = 5000


def _clamp(value: int, default: int, hard_cap: int) -> int:
    """Clamp *value* between ``1`` and *hard_cap*, falling back to *default*.

    Args:
        value: Caller-supplied integer (may be 0 or negative).
        default: Value used when *value* is non-positive.
        hard_cap: Inclusive upper bound.

    Returns:
        A positive integer no larger than *hard_cap*.
    """
    if value <= 0:
        return default
    return min(value, hard_cap)


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Build a structured error result dict.

    Args:
        code: Stable machine-readable error code (e.g. ``PATH_NOT_FOUND``).
        message: Human-readable description.
        **extra: Optional context fields included verbatim.

    Returns:
        Dict suitable for returning to the caller.
    """
    payload: dict[str, Any] = {"ok": False, "error": code, "message": message}
    payload.update(extra)
    return payload


def _kind(st: os.stat_result, *, is_symlink: bool) -> str:
    """Classify a stat result into a kind tag.

    Args:
        st: ``stat`` (or ``lstat``) result for the path.
        is_symlink: Whether the original entry was a symbolic link.

    Returns:
        One of ``"file"``, ``"dir"``, ``"symlink"``, or ``"other"``.
    """
    if is_symlink:
        return "symlink"
    if stat_mod.S_ISDIR(st.st_mode):
        return "dir"
    if stat_mod.S_ISREG(st.st_mode):
        return "file"
    return "other"  # pragma: no cover - FIFOs/sockets are not feasible to create in CI sandbox


def _resolve(path: str) -> Path:
    """Resolve *path* against the current process's allowed roots.

    Args:
        path: Caller-supplied absolute path.

    Returns:
        The resolved :class:`pathlib.Path`.

    Raises:
        OutOfBoundsError: If *path* escapes the allowed roots.
        NoAllowedRootsError: If no roots are configured.
    """
    roots = allowed_roots_for_env()
    return resolve_safe(path, roots)


def handle_fs_read(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ``fs.read`` - return a file's contents.

    Args:
        params: Command parameters. Recognised keys:

            - ``path`` (str, required): Absolute path inside an allowed root.
            - ``encoding`` (str, optional): ``"utf-8"`` (default) for text,
              ``"binary"`` for base64-encoded bytes.
            - ``max_bytes`` (int, optional): Maximum bytes to read; default
              1 MiB, hard cap 16 MiB.

    Returns:
        On success, dict with keys ``ok``, ``path``, ``size``, ``encoding``,
        ``content``, ``sha256``. On failure, dict with ``ok=False`` and an
        ``error`` code (``PATH_REQUIRED``, ``PATH_NOT_FOUND``,
        ``IS_DIRECTORY``, ``OUT_OF_BOUNDS``, ``NO_ALLOWED_ROOTS``,
        ``TOO_LARGE``, ``DECODE_ERROR``).

    Example:
        >>> # In tests OPENCLAW_ALLOWED_ROOTS is monkeypatched to tmp_path.
        >>> # handle_fs_read({"path": "/tmp/x"})  # doctest: +SKIP
    """
    raw_path = params.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return _error("PATH_REQUIRED", "Missing required 'path' parameter")
    encoding = str(params.get("encoding", "utf-8"))
    max_bytes_raw = params.get("max_bytes", _DEFAULT_READ_MAX_BYTES)
    if not isinstance(max_bytes_raw, int):
        max_bytes_raw = _DEFAULT_READ_MAX_BYTES
    max_bytes = _clamp(max_bytes_raw, _DEFAULT_READ_MAX_BYTES, _HARD_READ_MAX_BYTES)

    try:
        resolved = _resolve(raw_path)
    except NoAllowedRootsError as exc:
        return _error("NO_ALLOWED_ROOTS", str(exc))
    except OutOfBoundsError as exc:
        return _error("OUT_OF_BOUNDS", str(exc), path=raw_path)

    if not resolved.exists():
        return _error("PATH_NOT_FOUND", f"No such file: {raw_path}", path=raw_path)
    if resolved.is_dir():
        return _error("IS_DIRECTORY", f"Path is a directory: {raw_path}", path=raw_path)

    size = resolved.stat().st_size
    if size > max_bytes:
        return _error(
            "TOO_LARGE",
            f"File is {size} bytes, exceeds limit of {max_bytes}",
            path=raw_path,
            size=size,
            max_bytes=max_bytes,
        )

    data = resolved.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if encoding == "binary":
        content: str = base64.b64encode(data).decode("ascii")
        out_encoding = "binary"
    else:
        try:
            content = data.decode(encoding)
        except (UnicodeDecodeError, LookupError) as exc:
            return _error(
                "DECODE_ERROR",
                f"Cannot decode file as {encoding}: {exc}",
                path=raw_path,
                encoding=encoding,
            )
        out_encoding = encoding

    return {
        "ok": True,
        "path": str(resolved),
        "size": size,
        "encoding": out_encoding,
        "content": content,
        "sha256": sha,
    }


def _entry(child: Path) -> dict[str, Any]:
    """Build a single ``fs.list`` entry dict for *child*.

    Args:
        child: A direct child :class:`pathlib.Path` returned by ``iterdir``.

    Returns:
        Dict with ``name``, ``kind``, ``size``, and ``mtime`` keys.  On
        ``OSError`` (broken symlinks, races), size/mtime fall back to 0.
    """
    is_symlink = child.is_symlink()
    try:
        st = child.lstat()
    except OSError:  # pragma: no cover - lstat rarely fails on iterdir entries
        return {"name": child.name, "kind": "other", "size": 0, "mtime": 0.0}
    return {
        "name": child.name,
        "kind": _kind(st, is_symlink=is_symlink),
        "size": int(st.st_size),
        "mtime": float(st.st_mtime),
    }


def handle_fs_list(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ``fs.list`` - list a directory's immediate children.

    Args:
        params: Command parameters. Recognised keys:

            - ``path`` (str, required): Absolute path of the directory.
            - ``hidden`` (bool, optional): Include dotfiles. Default ``False``.
            - ``max_entries`` (int, optional): Cap on number of entries;
              default 1000, hard cap 5000.

    Returns:
        On success, dict with ``ok``, ``path``, ``entries`` (list of
        ``{name, kind, size, mtime}``), and ``truncated`` flag. On
        failure, an error dict with codes ``PATH_REQUIRED``,
        ``PATH_NOT_FOUND``, ``NOT_A_DIRECTORY``, ``OUT_OF_BOUNDS``,
        ``NO_ALLOWED_ROOTS``.
    """
    raw_path = params.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return _error("PATH_REQUIRED", "Missing required 'path' parameter")
    hidden = bool(params.get("hidden", False))
    max_entries_raw = params.get("max_entries", _DEFAULT_LIST_MAX_ENTRIES)
    if not isinstance(max_entries_raw, int):
        max_entries_raw = _DEFAULT_LIST_MAX_ENTRIES
    max_entries = _clamp(max_entries_raw, _DEFAULT_LIST_MAX_ENTRIES, _HARD_LIST_MAX_ENTRIES)

    try:
        resolved = _resolve(raw_path)
    except NoAllowedRootsError as exc:
        return _error("NO_ALLOWED_ROOTS", str(exc))
    except OutOfBoundsError as exc:
        return _error("OUT_OF_BOUNDS", str(exc), path=raw_path)

    if not resolved.exists():
        return _error("PATH_NOT_FOUND", f"No such path: {raw_path}", path=raw_path)
    if not resolved.is_dir():
        return _error("NOT_A_DIRECTORY", f"Path is not a directory: {raw_path}", path=raw_path)

    entries: list[dict[str, Any]] = []
    truncated = False
    for child in sorted(resolved.iterdir(), key=lambda c: c.name):
        if not hidden and child.name.startswith("."):
            continue
        if len(entries) >= max_entries:
            truncated = True
            break
        entries.append(_entry(child))

    return {
        "ok": True,
        "path": str(resolved),
        "entries": entries,
        "truncated": truncated,
    }


def handle_fs_stat(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ``fs.stat`` - stat one path.

    Returns a structured dict even when the path does not exist (with
    ``exists=False``); only path-resolution errors raise.

    Args:
        params: Command parameters. Recognised keys:

            - ``path`` (str, required): Absolute path inside an allowed root.

    Returns:
        Success dict with keys ``ok``, ``path``, ``exists``, and when the
        path exists also ``kind``, ``size``, ``mtime``, ``ctime``, ``mode``,
        ``owner_uid``, ``group_gid``, ``is_symlink``, and optional
        ``link_target``. Error dict on path/config issues.
    """
    raw_path = params.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return _error("PATH_REQUIRED", "Missing required 'path' parameter")

    try:
        resolved = _resolve(raw_path)
    except NoAllowedRootsError as exc:
        return _error("NO_ALLOWED_ROOTS", str(exc))
    except OutOfBoundsError as exc:
        return _error("OUT_OF_BOUNDS", str(exc), path=raw_path)

    if not resolved.exists():
        return {"ok": True, "path": str(resolved), "exists": False}

    # ``resolved`` already has symlinks followed by ``Path.resolve``; ``is_symlink``
    # is therefore always False here and ``link_target`` is always None. Both
    # fields are preserved for forward compatibility with future stat-without-follow
    # variants.
    is_symlink = False
    link_target: str | None = None
    st = resolved.lstat()
    return {
        "ok": True,
        "path": str(resolved),
        "exists": True,
        "kind": _kind(st, is_symlink=is_symlink),
        "size": int(st.st_size),
        "mtime": float(st.st_mtime),
        "ctime": float(st.st_ctime),
        "mode": stat_mod.S_IMODE(st.st_mode),
        "owner_uid": int(st.st_uid),
        "group_gid": int(st.st_gid),
        "is_symlink": is_symlink,
        "link_target": link_target,
    }


def _iter_matches(root: Path, pattern: str, *, hidden: bool) -> list[str]:
    """Walk *root* and return matches of *pattern* relative to root.

    Uses ``Path.rglob`` for ``**`` patterns and ``Path.glob`` otherwise.
    Hidden entries (any path component starting with ``.``) are excluded
    unless *hidden* is ``True``.

    Args:
        root: Resolved root directory.
        pattern: Glob pattern, e.g. ``"**/*.yaml"`` or ``"*.txt"``.
        hidden: Whether to include hidden files/directories.

    Returns:
        Sorted list of match paths relative to *root*.
    """
    candidates = root.rglob(pattern.removeprefix("**/")) if "**" in pattern else root.glob(pattern)
    out: list[str] = []
    for match in candidates:
        rel = match.relative_to(root)
        if not hidden and any(part.startswith(".") for part in rel.parts):
            continue
        out.append(rel.as_posix())
    out.sort()
    return out


def handle_fs_glob(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ``fs.glob`` - glob within an allowed root.

    Args:
        params: Command parameters. Recognised keys:

            - ``root`` (str, required): Absolute root path; must resolve
              under one of the configured allowed roots.
            - ``pattern`` (str, required): Glob pattern (e.g. ``"**/*.yaml"``).
            - ``hidden`` (bool, optional): Include hidden entries. Default
              ``False``.
            - ``max_matches`` (int, optional): Cap on number of matches;
              default 1000, hard cap 5000.

    Returns:
        Success dict with ``ok``, ``root``, ``pattern``, ``matches`` (list of
        strings relative to *root*), and ``truncated`` flag. Error dict on
        validation failure.
    """
    raw_root = params.get("root")
    pattern = params.get("pattern")
    if not isinstance(raw_root, str) or not raw_root:
        return _error("ROOT_REQUIRED", "Missing required 'root' parameter")
    if not isinstance(pattern, str) or not pattern:
        return _error("PATTERN_REQUIRED", "Missing required 'pattern' parameter")
    hidden = bool(params.get("hidden", False))
    max_matches_raw = params.get("max_matches", _DEFAULT_GLOB_MAX_MATCHES)
    if not isinstance(max_matches_raw, int):
        max_matches_raw = _DEFAULT_GLOB_MAX_MATCHES
    max_matches = _clamp(max_matches_raw, _DEFAULT_GLOB_MAX_MATCHES, _HARD_GLOB_MAX_MATCHES)

    try:
        resolved_root = _resolve(raw_root)
    except NoAllowedRootsError as exc:
        return _error("NO_ALLOWED_ROOTS", str(exc))
    except OutOfBoundsError as exc:
        return _error("OUT_OF_BOUNDS", str(exc), root=raw_root)

    if not resolved_root.is_dir():
        return _error("NOT_A_DIRECTORY", f"Root is not a directory: {raw_root}", root=raw_root)

    matches = _iter_matches(resolved_root, pattern, hidden=hidden)
    truncated = len(matches) > max_matches
    if truncated:
        matches = matches[:max_matches]

    return {
        "ok": True,
        "root": str(resolved_root),
        "pattern": pattern,
        "matches": matches,
        "truncated": truncated,
    }
