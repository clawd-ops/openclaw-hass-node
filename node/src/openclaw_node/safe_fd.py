"""File-descriptor rooted path opening helpers.

This module opens caller-supplied paths beneath configured roots without a
``resolve``-then-use race.  It prefers Linux ``openat2`` with symlink and
escape prevention, and falls back to per-component ``openat`` traversal using
``O_NOFOLLOW``.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import stat
from pathlib import Path
from typing import Final

from openclaw_node.safe_path import NoAllowedRootsError, OutOfBoundsError

_SYS_OPENAT2_BY_ARCH: Final[dict[str, int]] = {
    "x86_64": 437,
    "amd64": 437,
    "aarch64": 437,
    "arm64": 437,
    "armv7l": 437,
    "armv7": 437,
}
_RESOLVE_NO_MAGICLINKS: Final[int] = 0x02
_RESOLVE_NO_SYMLINKS: Final[int] = 0x04
_RESOLVE_BENEATH: Final[int] = 0x08
_O_PATH: Final[int] = getattr(os, "O_PATH", 0o10000000)
_O_COMMON: Final[int] = os.O_CLOEXEC | os.O_NOFOLLOW
_LIBC: Final[ctypes.CDLL] = ctypes.CDLL(None, use_errno=True)


class _OpenHow(ctypes.Structure):
    """ctypes representation of Linux ``struct open_how``."""

    _fields_ = [
        ("flags", ctypes.c_uint64),
        ("mode", ctypes.c_uint64),
        ("resolve", ctypes.c_uint64),
    ]


def _sys_openat2() -> int | None:
    """Return the platform ``openat2`` syscall number when known.

    Returns:
        The syscall number or ``None`` when this architecture is unsupported.
    """
    from_os = getattr(os, "SYS_openat2", None)
    if isinstance(from_os, int):
        return from_os
    return _SYS_OPENAT2_BY_ARCH.get(platform.machine().lower())


def _relative_to_root(path: str, roots: tuple[Path, ...]) -> tuple[Path, tuple[str, ...]]:
    """Return the matching allowed root and relative path components.

    Args:
        path: Caller-supplied absolute path.
        roots: Allowed root directories.

    Returns:
        The longest matching root and path components beneath it.

    Raises:
        NoAllowedRootsError: If no roots are configured.
        OutOfBoundsError: If *path* is not absolute or not under a root.
    """
    if not roots:
        raise NoAllowedRootsError
    raw = Path(path)
    if not path or not raw.is_absolute():
        raise OutOfBoundsError(path)
    matches: list[tuple[Path, tuple[str, ...]]] = []
    for root in roots:
        try:
            rel = raw.relative_to(root)
        except ValueError:
            continue
        parts = rel.parts
        if ".." in parts:
            raise OutOfBoundsError(path, str(raw))
        matches.append((root, parts))
    if not matches:
        raise OutOfBoundsError(path, str(raw))
    return max(matches, key=lambda item: len(item[0].parts))


def _open_root(root: Path) -> int:
    """Open an allowed root as an ``O_PATH`` directory fd.

    Args:
        root: Allowed root path.

    Returns:
        File descriptor for *root*.
    """
    return os.open(root, _O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)


def _final_flags(*, dir_fd_only: bool) -> int:
    """Return flags for the final path component.

    Args:
        dir_fd_only: Require the final component to be a directory fd.

    Returns:
        Flags suitable for ``open``/``openat2``.
    """
    if dir_fd_only:
        return _O_PATH | os.O_DIRECTORY | _O_COMMON
    return os.O_RDONLY | _O_COMMON


def _raise_if_symlink(fd: int, path: str) -> None:
    """Reject an fd that names a symlink rather than a real object.

    Args:
        fd: Open file descriptor.
        path: Original caller path for exception attributes.

    Raises:
        OutOfBoundsError: If *fd* points at a symlink.
    """
    if stat.S_ISLNK(os.fstat(fd).st_mode):
        raise OutOfBoundsError(path)


def _openat2_beneath(root_fd: int, rel_parts: tuple[str, ...], path: str, *, flags: int) -> int:
    """Open *rel_parts* beneath *root_fd* using Linux ``openat2``.

    Args:
        root_fd: Directory fd for the matched allowed root.
        rel_parts: Relative path components under *root_fd*.
        path: Original caller path for exception attributes.
        flags: Final open flags.

    Returns:
        Open file descriptor.

    Raises:
        OSError: If the syscall fails.
        OutOfBoundsError: If the kernel reports a symlink or escape attempt.
    """
    syscall_no = _sys_openat2()
    if syscall_no is None:
        raise OSError(errno.ENOSYS, os.strerror(errno.ENOSYS))
    rel = "." if not rel_parts else "/".join(rel_parts)
    how = _OpenHow(
        flags=flags,
        mode=0,
        resolve=_RESOLVE_BENEATH | _RESOLVE_NO_SYMLINKS | _RESOLVE_NO_MAGICLINKS,
    )
    fd = int(
        _LIBC.syscall(
            syscall_no,
            root_fd,
            rel.encode(),
            ctypes.byref(how),
            ctypes.sizeof(how),
        )
    )
    if fd >= 0:
        return fd
    err = ctypes.get_errno()
    if err in {errno.ELOOP, errno.EXDEV}:
        raise OutOfBoundsError(path) from OSError(err, os.strerror(err))
    raise OSError(err, os.strerror(err))


def _fallback_openat(root_fd: int, rel_parts: tuple[str, ...], path: str, *, flags: int) -> int:
    """Open *rel_parts* beneath *root_fd* with per-component ``openat``.

    Args:
        root_fd: Directory fd for the matched allowed root.
        rel_parts: Relative path components under *root_fd*.
        path: Original caller path for exception attributes.
        flags: Final open flags.

    Returns:
        Open file descriptor.

    Raises:
        OutOfBoundsError: If a symlink component is encountered.
        OSError: If opening any component fails.
    """
    if not rel_parts:
        return os.dup(root_fd)
    current = os.dup(root_fd)
    try:
        for index, part in enumerate(rel_parts):
            is_final = index == len(rel_parts) - 1
            component_flags = flags if is_final else (_O_PATH | os.O_DIRECTORY | _O_COMMON)
            try:
                next_fd = os.open(part, component_flags, dir_fd=current)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise OutOfBoundsError(path) from exc
                raise
            os.close(current)
            current = next_fd
            _raise_if_symlink(current, path)
    except Exception:
        os.close(current)
        raise
    return current


def open_safe_fd(path: str, roots: tuple[Path, ...], *, dir_fd_only: bool = False) -> int:
    """Open *path* beneath one of *roots* and return a stable fd.

    Args:
        path: Caller-supplied absolute path.
        roots: Allowed roots.
        dir_fd_only: When true, require the final object to be a directory and
            return an ``O_PATH`` directory fd.

    Returns:
        A file descriptor owned by the caller.

    Raises:
        NoAllowedRootsError: If *roots* is empty.
        OutOfBoundsError: If *path* escapes roots or uses symlinks.
        OSError: For normal filesystem errors such as missing paths.
    """
    root, rel_parts = _relative_to_root(path, roots)
    root_fd = _open_root(root)
    flags = _final_flags(dir_fd_only=dir_fd_only)
    try:
        try:
            return _openat2_beneath(root_fd, rel_parts, path, flags=flags)
        except OSError:
            return _fallback_openat(root_fd, rel_parts, path, flags=flags)
    finally:
        os.close(root_fd)
