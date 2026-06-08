"""Tests for fd-rooted safe path opening."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from openclaw_node.safe_fd import (
    atomic_write_safe,
    open_safe_fd,
    open_safe_parent_dir,
    read_bytes_safe,
    unlink_safe,
)
from openclaw_node.safe_path import NoAllowedRootsError, OutOfBoundsError


def test_open_safe_fd_no_roots_raises(tmp_path: Path) -> None:
    with pytest.raises(NoAllowedRootsError):
        open_safe_fd(str(tmp_path), ())


def test_open_safe_fd_rejects_relative_path(tmp_path: Path) -> None:
    with pytest.raises(OutOfBoundsError):
        open_safe_fd("relative", (tmp_path,))


def test_open_safe_fd_rejects_embedded_nul(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_text("nope", encoding="utf-8")
    with pytest.raises(OutOfBoundsError):
        open_safe_fd(f"{secret}\x00/suffix", (tmp_path,))


def test_open_safe_fd_reads_regular_file(tmp_path: Path) -> None:
    file_path = tmp_path / "x.txt"
    file_path.write_text("hello", encoding="utf-8")
    fd = open_safe_fd(str(file_path), (tmp_path,))
    try:
        assert os.read(fd, 16) == b"hello"
    finally:
        os.close(fd)


def test_open_safe_fd_dir_fd_only_requires_directory(tmp_path: Path) -> None:
    fd = open_safe_fd(str(tmp_path), (tmp_path,), dir_fd_only=True)
    try:
        assert os.fstat(fd).st_mode
    finally:
        os.close(fd)
    file_path = tmp_path / "x.txt"
    file_path.write_text("hello", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        open_safe_fd(str(file_path), (tmp_path,), dir_fd_only=True)


def test_open_safe_fd_rejects_out_of_root_symlink(tmp_path: Path) -> None:
    link = tmp_path / "escape"
    link.symlink_to("/etc")
    with pytest.raises(OutOfBoundsError):
        open_safe_fd(str(link / "passwd"), (tmp_path,))


def test_open_safe_fd_rejects_final_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real.txt"
    target.write_text("hello", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    with pytest.raises(OutOfBoundsError):
        open_safe_fd(str(link), (tmp_path,))


def test_open_safe_fd_rejects_dotdot(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    with pytest.raises(OutOfBoundsError):
        open_safe_fd(f"{sub}/../sub", (tmp_path,))


def test_open_safe_fd_opens_root_dir_directly(tmp_path: Path) -> None:
    """Opening an allowed root itself returns a valid fd."""
    fd = open_safe_fd(str(tmp_path), (tmp_path,), dir_fd_only=True)
    try:
        stat = os.fstat(fd)
        assert stat.st_ino == tmp_path.stat().st_ino
    finally:
        os.close(fd)


def test_open_safe_parent_dir_rejects_empty_basename(tmp_path: Path) -> None:
    """Path ending in '.' normalizes to empty basename via Path()."""
    with pytest.raises(OutOfBoundsError):
        open_safe_parent_dir(str(tmp_path / "."), (tmp_path,))


def test_open_safe_parent_dir_rejects_dotdot_basename(tmp_path: Path) -> None:
    """A '..' basename is explicitly rejected by open_safe_parent_dir."""
    sub = tmp_path / "sub"
    sub.mkdir()
    with pytest.raises(OutOfBoundsError):
        open_safe_parent_dir(f"{sub}/..", (tmp_path,))


def test_read_bytes_safe_missing_ok(tmp_path: Path) -> None:
    """missing_ok=True returns empty bytes for nonexistent file."""
    result = read_bytes_safe(str(tmp_path / "nope.txt"), (tmp_path,), missing_ok=True)
    assert result == b""


def test_read_bytes_safe_missing_not_ok(tmp_path: Path) -> None:
    """missing_ok=False raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        read_bytes_safe(str(tmp_path / "nope.txt"), (tmp_path,))


def test_atomic_write_safe_writes_and_reads_back(tmp_path: Path) -> None:
    """Round-trip write and read through safe primitives."""
    target = tmp_path / "out.txt"
    atomic_write_safe(str(target), (tmp_path,), b"hello safe")
    assert read_bytes_safe(str(target), (tmp_path,)) == b"hello safe"


def test_atomic_write_safe_cleans_up_on_write_failure(tmp_path: Path) -> None:
    """If the write phase fails, the temp file is cleaned up."""
    target = tmp_path / "out.txt"
    with (
        patch("os.fdopen", side_effect=OSError("write failure")),
        pytest.raises(OSError, match="write failure"),
    ):
        atomic_write_safe(str(target), (tmp_path,), b"data")
    assert not target.exists()
    leftover = list(tmp_path.glob(".oc_write.*"))
    assert leftover == []


def test_atomic_write_safe_cleans_up_on_replace_failure(tmp_path: Path) -> None:
    """If the replace phase fails, the temp file is cleaned up."""
    target = tmp_path / "out.txt"
    with (
        patch("os.replace", side_effect=OSError("replace failure")),
        pytest.raises(OSError, match="replace failure"),
    ):
        atomic_write_safe(str(target), (tmp_path,), b"data")
    assert not target.exists()
    leftover = list(tmp_path.glob(".oc_write.*"))
    assert leftover == []


def test_unlink_safe_removes_file(tmp_path: Path) -> None:
    """unlink_safe removes the target file."""
    target = tmp_path / "deleteme.txt"
    target.write_text("bye")
    result = unlink_safe(str(target), (tmp_path,))
    assert result is True
    assert not target.exists()


def test_unlink_safe_missing_returns_false(tmp_path: Path) -> None:
    """unlink_safe returns False for a nonexistent file."""
    result = unlink_safe(str(tmp_path / "nope.txt"), (tmp_path,))
    assert result is False


# ---- openat2 / fallback branch coverage ----


def test_open_safe_fd_falls_back_when_openat2_unavailable(tmp_path: Path) -> None:
    """When openat2 returns ENOSYS, open_safe_fd retries via _fallback_openat."""
    file_path = tmp_path / "x.txt"
    file_path.write_text("hi", encoding="utf-8")
    with patch("openclaw_node.safe_fd._sys_openat2", return_value=None):
        fd = open_safe_fd(str(file_path), (tmp_path,))
        try:
            assert os.read(fd, 16) == b"hi"
        finally:
            os.close(fd)


def test_open_safe_fd_intermediate_symlink_rejected(tmp_path: Path) -> None:
    """A symlink in a non-final path component must raise OutOfBoundsError."""
    sub = tmp_path / "sub"
    sub.mkdir()
    link = tmp_path / "link"
    link.symlink_to(sub)
    target = link / "x.txt"
    (sub / "x.txt").write_text("hi", encoding="utf-8")
    with pytest.raises(OutOfBoundsError):
        open_safe_fd(str(target), (tmp_path,))


def test_open_safe_fd_traversal_component_rejected(tmp_path: Path) -> None:
    """A ``..`` mid-path component must escape detection and raise."""
    sub = tmp_path / "sub"
    sub.mkdir()
    nested = sub / "nested"
    nested.mkdir()
    with pytest.raises(OutOfBoundsError):
        open_safe_fd(f"{nested}/../../sub", (tmp_path,))
