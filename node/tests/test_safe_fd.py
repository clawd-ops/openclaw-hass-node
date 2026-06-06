"""Tests for fd-rooted safe path opening."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openclaw_node.safe_fd import open_safe_fd
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
