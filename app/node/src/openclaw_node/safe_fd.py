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
        return os.O_RDONLY | os.O_DIRECTORY | _O_COMMON
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
        return os.open(".", flags, dir_fd=root_fd)
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
    if "\x00" in path:
        raise OutOfBoundsError(path)
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


# --------------------------------------------------------------------------- #
# TOCTOU-safe mutation helpers (audit issue #48 item 12).
#
# The legacy pattern was: resolve_safe(path) -> Path; then operate on the path
# string. Between resolve and operate, an attacker with write access to any
# parent directory can swap the path target via symlink or rename. The fix is
# to open a stable dir fd for the parent, validate it once, and run every
# mutation (write, read, unlink, rename) relative to that dir fd. Even if the
# attacker swaps the parent or basename mid-flight, the kernel ops still
# operate on the originally-validated inodes.
#
# Helpers below all take ``(path, roots)`` and own the dir-fd lifecycle. Call
# sites in commands/fs_*.py just use the high-level functions.
# --------------------------------------------------------------------------- #


def open_safe_parent_dir(path: str, roots: tuple[Path, ...]) -> tuple[int, str]:
    """Open the parent dir of *path* beneath *roots* and return ``(dir_fd, basename)``.

    The returned ``dir_fd`` is an ``O_PATH`` directory fd whose path resolves
    under *roots* with no symlinks followed during traversal. Subsequent
    ``openat`` / ``renameat`` / ``unlinkat`` calls relative to ``dir_fd``
    cannot be redirected outside the originally-validated directory.

    Args:
        path: Caller-supplied absolute path.
        roots: Allowed roots.

    Returns:
        Tuple of (parent dir fd, basename).

    Raises:
        NoAllowedRootsError: If *roots* is empty.
        OutOfBoundsError: If *path* escapes roots, uses symlinks, or has an
            empty/dot basename.
    """
    p = Path(path)
    basename = p.name
    if not basename or basename in (".", ".."):
        raise OutOfBoundsError(path)
    dir_fd = open_safe_fd(str(p.parent), roots, dir_fd_only=True)
    return dir_fd, basename


def read_bytes_safe(path: str, roots: tuple[Path, ...], *, missing_ok: bool = False) -> bytes:
    """Read *path*'s bytes via a TOCTOU-safe fd open.

    Symlinks at the final component are rejected via ``O_NOFOLLOW``.

    Args:
        path: Caller-supplied absolute path.
        roots: Allowed roots.
        missing_ok: When ``True``, return ``b""`` if the file does not
            exist (the prior-bytes capture pattern used by write/restore
            and move). When ``False`` (default), ``FileNotFoundError``
            propagates so callers like ``fs.patch`` and ``fs.delete``
            don't have to re-check ``Path.exists()`` after the read
            (which would reintroduce a TOCTOU window).
    """
    try:
        fd = open_safe_fd(path, roots)
    except FileNotFoundError:
        if missing_ok:
            return b""
        raise
    with os.fdopen(fd, "rb") as fh:
        return fh.read()


def atomic_write_safe(
    path: str, roots: tuple[Path, ...], data: bytes, *, mode: int = 0o644
) -> None:
    """Atomically write *data* to *path* under *roots* with TOCTOU defense.

    Opens the parent dir as a stable fd, writes to a sibling temp file via
    ``openat(parent_fd, ...)`` with ``O_NOFOLLOW | O_EXCL``, ``fsync``s the
    data, and renames atomically via ``renameat(parent_fd, ...)``. The parent
    dir fd is also ``fsync``-ed so the rename is crash-durable.

    Even if an attacker rewrites the parent symlink or swaps the basename
    after the parent dir fd is opened, the writes still land in the validated
    directory (the dir fd points to the inode, not the path string).

    Args:
        path: Destination absolute path.
        roots: Allowed roots.
        data: Bytes to write.
        mode: POSIX mode for the new file (default ``0o644``).

    Raises:
        NoAllowedRootsError / OutOfBoundsError / OSError: As for safe_fd
            primitives.
    """
    import contextlib as _ctx
    import secrets as _secrets

    dir_fd, basename = open_safe_parent_dir(path, roots)
    tmp_name = f".oc_write.{_secrets.token_hex(8)}"
    fd: int | None = None
    try:
        fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            mode,
            dir_fd=dir_fd,
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                fd = None  # fdopen owns the fd from here on.
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            with _ctx.suppress(OSError):
                os.unlink(tmp_name, dir_fd=dir_fd)
            raise
        try:
            os.replace(tmp_name, basename, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            with _ctx.suppress(OSError):
                os.unlink(tmp_name, dir_fd=dir_fd)
            raise
        # Crash durability for the rename. ``dir_fd`` is opened ``O_PATH``,
        # which fsync rejects with EBADF — open a regular directory fd
        # against the already-validated parent (the openat is relative to
        # ``dir_fd`` so it cannot be redirected by a symlink swap).
        try:
            sync_fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=dir_fd)
        except OSError:
            pass
        else:
            try:
                with _ctx.suppress(OSError):
                    os.fsync(sync_fd)
            finally:
                os.close(sync_fd)
    finally:
        if fd is not None:
            with _ctx.suppress(OSError):
                os.close(fd)
        os.close(dir_fd)


def unlink_safe(path: str, roots: tuple[Path, ...]) -> bool:
    """Unlink *path* under *roots* via the parent dir fd.

    Refuses to follow a symlink at the final component.

    Args:
        path: Absolute path to remove.
        roots: Allowed roots.

    Returns:
        ``True`` if the file was removed, ``False`` if it did not exist.

    Raises:
        OutOfBoundsError: If *path* escapes roots or names a symlink at the
            final component.
        IsADirectoryError: If *path* is a directory (caller should use a
            directory-aware helper instead).
    """
    dir_fd, basename = open_safe_parent_dir(path, roots)
    try:
        try:
            os.unlink(basename, dir_fd=dir_fd)
        except FileNotFoundError:
            return False
        return True
    finally:
        os.close(dir_fd)


def replace_safe(src_path: str, dst_path: str, roots: tuple[Path, ...]) -> None:
    """Rename *src_path* to *dst_path*, both validated under *roots*.

    Uses ``os.replace`` with explicit ``src_dir_fd`` / ``dst_dir_fd`` so each
    side is validated independently and neither leg follows a symlink at the
    final component.

    Within a single filesystem the operation is atomic. Across filesystems
    the kernel returns ``EXDEV``; callers that need cross-fs moves should
    fall back to ``read_bytes_safe`` + ``atomic_write_safe`` + ``unlink_safe``.

    Args:
        src_path: Source absolute path.
        dst_path: Destination absolute path.
        roots: Allowed roots (both src and dst must resolve under them).

    Raises:
        OSError(EXDEV): On cross-filesystem rename.
        Other safe_fd errors as documented above.
    """
    src_dir_fd, src_name = open_safe_parent_dir(src_path, roots)
    try:
        dst_dir_fd, dst_name = open_safe_parent_dir(dst_path, roots)
        try:
            os.replace(src_name, dst_name, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
        finally:
            os.close(dst_dir_fd)
    finally:
        os.close(src_dir_fd)
