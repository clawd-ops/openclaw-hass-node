"""Tests for read-only filesystem command handlers."""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest

from openclaw_node.commands.fs import (
    handle_fs_glob,
    handle_fs_list,
    handle_fs_read,
    handle_fs_stat,
)


@pytest.fixture(autouse=True)
def _sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Configure the standalone allowlist to point at ``tmp_path``."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.setenv("OPENCLAW_ALLOWED_ROOTS", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# fs.read
# ---------------------------------------------------------------------------


def test_fs_read_missing_path() -> None:
    result = handle_fs_read({})
    assert result["ok"] is False
    assert result["error"] == "PATH_REQUIRED"


def test_fs_read_path_not_found(tmp_path: Path) -> None:
    result = handle_fs_read({"path": str(tmp_path / "missing.txt")})
    assert result["error"] == "PATH_NOT_FOUND"


def test_fs_read_is_directory(tmp_path: Path) -> None:
    result = handle_fs_read({"path": str(tmp_path)})
    assert result["error"] == "IS_DIRECTORY"


def test_fs_read_out_of_bounds() -> None:
    result = handle_fs_read({"path": "/etc/passwd"})
    assert result["error"] == "OUT_OF_BOUNDS"
    assert result["message"] == "Path is outside the allowed roots"
    assert result["path"] == "/etc/passwd"
    assert "pytest" not in result["message"]


def test_fs_read_no_allowed_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENCLAW_ALLOWED_ROOTS", raising=False)
    result = handle_fs_read({"path": str(tmp_path / "x")})
    assert result["error"] == "NO_ALLOWED_ROOTS"


def test_fs_read_text(tmp_path: Path) -> None:
    f = tmp_path / "hello.txt"
    f.write_text("hi", encoding="utf-8")
    result = handle_fs_read({"path": str(f)})
    assert result["ok"] is True
    assert result["content"] == "hi"
    assert result["encoding"] == "utf-8"
    assert result["size"] == 2
    assert len(result["sha256"]) == 64


def test_fs_read_binary(tmp_path: Path) -> None:
    f = tmp_path / "blob"
    f.write_bytes(b"\x00\x01\x02")
    result = handle_fs_read({"path": str(f), "encoding": "binary"})
    assert result["encoding"] == "binary"
    assert base64.b64decode(result["content"]) == b"\x00\x01\x02"


def test_fs_read_too_large(tmp_path: Path) -> None:
    f = tmp_path / "big"
    f.write_bytes(b"x" * 100)
    result = handle_fs_read({"path": str(f), "max_bytes": 10})
    assert result["error"] == "TOO_LARGE"
    assert result["size"] == 11


def test_fs_read_too_large_when_file_grows_midflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_fd = 987
    small = tmp_path / "small"
    small.write_text("x")
    st = os.stat(small)
    closes: list[int] = []

    monkeypatch.setattr("openclaw_node.commands.fs.open_safe_fd", lambda *_a, **_k: fake_fd)
    monkeypatch.setattr("openclaw_node.commands.fs.os.fstat", lambda _fd: st)
    monkeypatch.setattr(
        "openclaw_node.commands.fs.os.pread",
        lambda _fd, _n, _off: b"x" * 11,
    )
    monkeypatch.setattr("openclaw_node.commands.fs.os.close", lambda fd: closes.append(fd))

    result = handle_fs_read({"path": str(tmp_path / "small"), "max_bytes": 10})
    assert result["error"] == "TOO_LARGE"
    assert result["size"] == 11
    assert closes == [fake_fd]


def test_fs_read_max_bytes_clamped_to_default(tmp_path: Path) -> None:
    f = tmp_path / "small.txt"
    f.write_text("ok")
    result = handle_fs_read({"path": str(f), "max_bytes": -1})
    assert result["ok"] is True


def test_fs_read_max_bytes_non_int(tmp_path: Path) -> None:
    f = tmp_path / "small.txt"
    f.write_text("ok")
    result = handle_fs_read({"path": str(f), "max_bytes": "junk"})
    assert result["ok"] is True


def test_fs_read_decode_error(tmp_path: Path) -> None:
    f = tmp_path / "bin"
    f.write_bytes(b"\xff\xfe\xfd")
    result = handle_fs_read({"path": str(f), "encoding": "utf-8"})
    assert result["error"] == "DECODE_ERROR"


def test_fs_read_unknown_encoding(tmp_path: Path) -> None:
    f = tmp_path / "x"
    f.write_text("hi")
    result = handle_fs_read({"path": str(f), "encoding": "not-a-codec"})
    assert result["error"] == "DECODE_ERROR"


# ---------------------------------------------------------------------------
# fs.read - offset / length range semantics (#257)
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_fs_read_range_beginning(tmp_path: Path) -> None:
    p = _write(tmp_path, "abc.bin", b"abcdefghij")
    result = handle_fs_read({"path": str(p), "encoding": "binary", "offset": 0, "length": 3})
    assert result["ok"] is True
    assert base64.b64decode(result["content"]) == b"abc"
    assert result["size"] == 3
    assert result["file_size"] == 10
    assert result["offset"] == 0
    assert result["length"] == 3
    assert result["eof"] is False


def test_fs_read_range_middle(tmp_path: Path) -> None:
    p = _write(tmp_path, "mid.bin", b"abcdefghij")
    result = handle_fs_read({"path": str(p), "encoding": "binary", "offset": 3, "length": 4})
    assert result["ok"] is True
    assert base64.b64decode(result["content"]) == b"defg"
    assert result["offset"] == 3
    assert result["length"] == 4
    assert result["eof"] is False


def test_fs_read_range_to_eof(tmp_path: Path) -> None:
    p = _write(tmp_path, "eof.bin", b"abcdefghij")
    result = handle_fs_read({"path": str(p), "encoding": "binary", "offset": 7, "length": 100})
    # length was capped to max_bytes (default 1 MiB), so no TOO_LARGE and we
    # simply read what remains.
    assert result["ok"] is True
    assert base64.b64decode(result["content"]) == b"hij"
    assert result["size"] == 3
    assert result["eof"] is True


def test_fs_read_range_no_length_reads_to_eof_from_offset(tmp_path: Path) -> None:
    p = _write(tmp_path, "nolen.bin", b"abcdefghij")
    result = handle_fs_read({"path": str(p), "encoding": "binary", "offset": 4})
    assert result["ok"] is True
    assert base64.b64decode(result["content"]) == b"efghij"
    assert result["eof"] is True
    assert result["offset"] == 4


def test_fs_read_offset_beyond_eof_fails_closed(tmp_path: Path) -> None:
    """Regression for #257: offset past EOF must not return unrelated data."""
    p = _write(tmp_path, "small.bin", b"abcdefghij")
    result = handle_fs_read({"path": str(p), "offset": 1_000_000, "length": 1})
    assert result["ok"] is False
    assert result["error"] == "OFFSET_BEYOND_EOF"
    assert result["file_size"] == 10
    assert result["offset"] == 1_000_000


def test_fs_read_offset_at_eof_returns_empty(tmp_path: Path) -> None:
    p = _write(tmp_path, "eq.bin", b"abcdefghij")
    result = handle_fs_read({"path": str(p), "encoding": "binary", "offset": 10})
    assert result["ok"] is True
    assert result["size"] == 0
    assert result["eof"] is True
    assert result["content"] == ""


def test_fs_read_offset_negative_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "n.bin", b"abc")
    result = handle_fs_read({"path": str(p), "offset": -1})
    assert result["ok"] is False
    assert result["error"] == "BAD_OFFSET"


def test_fs_read_offset_non_int_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "n.bin", b"abc")
    result = handle_fs_read({"path": str(p), "offset": "0"})
    assert result["ok"] is False
    assert result["error"] == "BAD_OFFSET"


def test_fs_read_offset_bool_rejected(tmp_path: Path) -> None:
    """bool is a subclass of int; reject it explicitly."""
    p = _write(tmp_path, "n.bin", b"abc")
    result = handle_fs_read({"path": str(p), "offset": True})
    assert result["ok"] is False
    assert result["error"] == "BAD_OFFSET"


def test_fs_read_offset_float_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "n.bin", b"abc")
    result = handle_fs_read({"path": str(p), "offset": 1.5})
    assert result["ok"] is False
    assert result["error"] == "BAD_OFFSET"


def test_fs_read_length_zero_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "n.bin", b"abc")
    result = handle_fs_read({"path": str(p), "length": 0})
    assert result["ok"] is False
    assert result["error"] == "BAD_LENGTH"


def test_fs_read_length_negative_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "n.bin", b"abc")
    result = handle_fs_read({"path": str(p), "length": -5})
    assert result["ok"] is False
    assert result["error"] == "BAD_LENGTH"


def test_fs_read_length_non_int_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "n.bin", b"abc")
    result = handle_fs_read({"path": str(p), "length": "1"})
    assert result["ok"] is False
    assert result["error"] == "BAD_LENGTH"


def test_fs_read_length_bool_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "n.bin", b"abc")
    result = handle_fs_read({"path": str(p), "length": True})
    assert result["ok"] is False
    assert result["error"] == "BAD_LENGTH"


def test_fs_read_length_greater_than_max_bytes_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "n.bin", b"a" * 100)
    result = handle_fs_read({"path": str(p), "length": 50, "max_bytes": 10})
    assert result["ok"] is False
    assert result["error"] == "TOO_LARGE"
    assert result["length"] == 50
    assert result["max_bytes"] == 10


def test_fs_read_length_within_max_bytes_wins(tmp_path: Path) -> None:
    """length <= max_bytes → range read succeeds with exactly length bytes."""
    p = _write(tmp_path, "n.bin", b"abcdefghij")
    result = handle_fs_read({"path": str(p), "encoding": "binary", "length": 4, "max_bytes": 4})
    assert result["ok"] is True
    assert base64.b64decode(result["content"]) == b"abcd"
    assert result["size"] == 4


def test_fs_read_explicit_length_never_reads_extra_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = _write(tmp_path, "bounded.bin", b"abcdefghij")
    calls: list[tuple[int, int]] = []
    real_pread = os.pread

    def recording_pread(fd: int, size: int, offset: int) -> bytes:
        calls.append((size, offset))
        return real_pread(fd, size, offset)

    monkeypatch.setattr("openclaw_node.commands.fs.os.pread", recording_pread)
    result = handle_fs_read({"path": str(p), "encoding": "binary", "offset": 2, "length": 3})

    assert result["ok"] is True
    assert base64.b64decode(result["content"]) == b"cde"
    assert calls == [(3, 2)]


def test_fs_read_range_sha256_is_of_slice(tmp_path: Path) -> None:
    import hashlib as _h

    payload = b"abcdefghij"
    p = _write(tmp_path, "sha.bin", payload)
    result = handle_fs_read({"path": str(p), "encoding": "binary", "offset": 2, "length": 5})
    assert result["ok"] is True
    assert result["sha256"] == _h.sha256(payload[2:7]).hexdigest()


def test_fs_read_range_splits_utf8_boundary_returns_decode_error(
    tmp_path: Path,
) -> None:
    # "café" in UTF-8 is 5 bytes: b'caf\xc3\xa9'. Read length=4 splits the
    # é in the middle -> must return DECODE_ERROR, not fabricate a
    # replacement character or claim ok.
    p = _write(tmp_path, "utf8.txt", "café".encode())
    result = handle_fs_read({"path": str(p), "length": 4})
    assert result["ok"] is False
    assert result["error"] == "DECODE_ERROR"
    assert result["encoding"] == "utf-8"


def test_fs_read_range_splits_utf8_boundary_ok_as_binary(tmp_path: Path) -> None:
    p = _write(tmp_path, "utf8.txt", "café".encode())
    result = handle_fs_read({"path": str(p), "encoding": "binary", "length": 4})
    assert result["ok"] is True
    assert base64.b64decode(result["content"]) == b"caf\xc3"


def test_fs_read_range_preserves_path_containment(tmp_path: Path) -> None:
    """Range parameters must not weaken the allowed-root gate."""
    result = handle_fs_read({"path": "/etc/passwd", "offset": 0, "length": 1})
    assert result["ok"] is False
    assert result["error"] == "OUT_OF_BOUNDS"


def test_fs_read_default_no_range_still_returns_new_fields(tmp_path: Path) -> None:
    p = _write(tmp_path, "d.bin", b"hello")
    result = handle_fs_read({"path": str(p)})
    assert result["ok"] is True
    assert result["file_size"] == 5
    assert result["offset"] == 0
    assert result["length"] == 5
    assert result["eof"] is True


# ---------------------------------------------------------------------------
# fs.list
# ---------------------------------------------------------------------------


def test_fs_list_missing_path() -> None:
    assert handle_fs_list({})["error"] == "PATH_REQUIRED"


def test_fs_list_not_found(tmp_path: Path) -> None:
    assert handle_fs_list({"path": str(tmp_path / "no")})["error"] == "PATH_NOT_FOUND"


def test_fs_list_not_a_directory(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_text("x")
    assert handle_fs_list({"path": str(f)})["error"] == "NOT_A_DIRECTORY"


def test_fs_list_out_of_bounds() -> None:
    assert handle_fs_list({"path": "/etc"})["error"] == "OUT_OF_BOUNDS"


def test_fs_list_no_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENCLAW_ALLOWED_ROOTS", raising=False)
    assert handle_fs_list({"path": str(tmp_path)})["error"] == "NO_ALLOWED_ROOTS"


def test_fs_list_basic(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / ".hidden").write_text("h")
    result = handle_fs_list({"path": str(tmp_path)})
    names = [e["name"] for e in result["entries"]]
    assert "a.txt" in names
    assert "sub" in names
    assert ".hidden" not in names
    kinds = {e["name"]: e["kind"] for e in result["entries"]}
    assert kinds["a.txt"] == "file"
    assert kinds["sub"] == "dir"
    assert result["truncated"] is False


def test_fs_list_hidden(tmp_path: Path) -> None:
    (tmp_path / ".secret").write_text("x")
    result = handle_fs_list({"path": str(tmp_path), "hidden": True})
    names = [e["name"] for e in result["entries"]]
    assert ".secret" in names


def test_fs_list_truncated(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"f{i}").write_text("x")
    result = handle_fs_list({"path": str(tmp_path), "max_entries": 2})
    assert len(result["entries"]) == 2
    assert result["truncated"] is True


def test_fs_list_stops_after_bounded_scan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_fd = 456
    next_calls = 0
    closes: list[int] = []

    class _Entry:
        def __init__(self, name: str) -> None:
            self.name = name

        def is_symlink(self) -> bool:
            return False

        def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
            return os.stat_result((stat.S_IFREG | 0o644, 0, 0, 1, 1, 1, 1, 0, 0, 0))

    class _Scan:
        def __init__(self) -> None:
            self._index = 0

        def __enter__(self) -> Self:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> None:
            return None

        def __iter__(self) -> Self:
            return self

        def __next__(self) -> _Entry:
            nonlocal next_calls
            next_calls += 1
            self._index += 1
            return _Entry(f"f{self._index}")

    monkeypatch.setattr("openclaw_node.commands.fs.open_safe_fd", lambda *_a, **_k: fake_fd)
    monkeypatch.setattr("openclaw_node.commands.fs.os.scandir", lambda _fd: _Scan())
    monkeypatch.setattr("openclaw_node.commands.fs.os.close", lambda fd: closes.append(fd))

    result = handle_fs_list({"path": str(tmp_path), "max_entries": 2})
    assert len(result["entries"]) == 2
    assert result["truncated"] is True
    assert next_calls == 3
    assert closes == [fake_fd]


def test_fs_list_max_entries_non_int(tmp_path: Path) -> None:
    (tmp_path / "f").write_text("x")
    result = handle_fs_list({"path": str(tmp_path), "max_entries": "junk"})
    assert result["ok"] is True


def test_fs_list_symlink_entry(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.write_text("x")
    (tmp_path / "link").symlink_to(target)
    result = handle_fs_list({"path": str(tmp_path)})
    kinds = {e["name"]: e["kind"] for e in result["entries"]}
    assert kinds["link"] == "symlink"


def test_fs_list_broken_symlink_handled(tmp_path: Path) -> None:
    (tmp_path / "broken").symlink_to(tmp_path / "nowhere")
    result = handle_fs_list({"path": str(tmp_path)})
    names = [e["name"] for e in result["entries"]]
    assert "broken" in names


# ---------------------------------------------------------------------------
# fs.stat
# ---------------------------------------------------------------------------


def test_fs_stat_missing_path() -> None:
    assert handle_fs_stat({})["error"] == "PATH_REQUIRED"


def test_fs_stat_out_of_bounds() -> None:
    assert handle_fs_stat({"path": "/etc/passwd"})["error"] == "OUT_OF_BOUNDS"


def test_fs_stat_no_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENCLAW_ALLOWED_ROOTS", raising=False)
    assert handle_fs_stat({"path": str(tmp_path)})["error"] == "NO_ALLOWED_ROOTS"


def test_fs_stat_not_exists(tmp_path: Path) -> None:
    result = handle_fs_stat({"path": str(tmp_path / "nope")})
    assert result["ok"] is True
    assert result["exists"] is False


def test_fs_stat_file(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_text("hi")
    result = handle_fs_stat({"path": str(f)})
    assert result["exists"] is True
    assert result["kind"] == "file"
    assert result["size"] == 2
    assert result["is_symlink"] is False
    assert result["link_target"] is None
    assert isinstance(result["mode"], int)


def test_fs_stat_dir(tmp_path: Path) -> None:
    result = handle_fs_stat({"path": str(tmp_path)})
    assert result["kind"] == "dir"


def test_fs_stat_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.write_text("x")
    link = tmp_path / "link"
    link.symlink_to(target)
    result = handle_fs_stat({"path": str(link)})
    assert result["error"] == "OUT_OF_BOUNDS"


# ---------------------------------------------------------------------------
# fs.glob
# ---------------------------------------------------------------------------


def test_fs_glob_missing_root() -> None:
    assert handle_fs_glob({"pattern": "*"})["error"] == "ROOT_REQUIRED"


def test_fs_glob_missing_pattern(tmp_path: Path) -> None:
    assert handle_fs_glob({"root": str(tmp_path)})["error"] == "PATTERN_REQUIRED"


def test_fs_glob_out_of_bounds() -> None:
    assert handle_fs_glob({"root": "/etc", "pattern": "*"})["error"] == "OUT_OF_BOUNDS"


def test_fs_glob_no_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENCLAW_ALLOWED_ROOTS", raising=False)
    assert handle_fs_glob({"root": str(tmp_path), "pattern": "*"})["error"] == "NO_ALLOWED_ROOTS"


def test_fs_glob_not_a_directory(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_text("x")
    assert handle_fs_glob({"root": str(f), "pattern": "*"})["error"] == "NOT_A_DIRECTORY"


def test_fs_glob_rejects_dotdot_pattern(tmp_path: Path) -> None:
    result = handle_fs_glob({"root": str(tmp_path), "pattern": "../foo"})
    assert result["error"] == "BAD_PATTERN"


def test_fs_glob_rejects_absolute_pattern(tmp_path: Path) -> None:
    result = handle_fs_glob({"root": str(tmp_path), "pattern": "/abs/foo"})
    assert result["error"] == "BAD_PATTERN"


def test_fs_glob_rejects_null_pattern(tmp_path: Path) -> None:
    result = handle_fs_glob({"root": str(tmp_path), "pattern": "bad\x00pattern"})
    assert result["error"] == "BAD_PATTERN"


def test_fs_glob_basic(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("x")
    (tmp_path / "b.yaml").write_text("x")
    (tmp_path / "c.txt").write_text("x")
    result = handle_fs_glob({"root": str(tmp_path), "pattern": "*.yaml"})
    assert sorted(result["matches"]) == ["a.yaml", "b.yaml"]


def test_fs_glob_recursive(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "x.yaml").write_text("x")
    (tmp_path / "y.yaml").write_text("x")
    result = handle_fs_glob({"root": str(tmp_path), "pattern": "**/*.yaml"})
    assert "y.yaml" in result["matches"]
    assert "sub/x.yaml" in result["matches"]


def test_fs_glob_hidden_excluded(tmp_path: Path) -> None:
    (tmp_path / ".hidden.yaml").write_text("x")
    (tmp_path / "v.yaml").write_text("x")
    result = handle_fs_glob({"root": str(tmp_path), "pattern": "*.yaml"})
    assert ".hidden.yaml" not in result["matches"]
    assert "v.yaml" in result["matches"]


def test_fs_glob_hidden_included(tmp_path: Path) -> None:
    (tmp_path / ".hidden.yaml").write_text("x")
    result = handle_fs_glob({"root": str(tmp_path), "pattern": "*.yaml", "hidden": True})
    assert ".hidden.yaml" in result["matches"]


def test_fs_glob_truncated(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"f{i}.yaml").write_text("x")
    result = handle_fs_glob({"root": str(tmp_path), "pattern": "*.yaml", "max_matches": 2})
    assert len(result["matches"]) == 2
    assert result["truncated"] is True


def test_fs_glob_max_matches_non_int(tmp_path: Path) -> None:
    (tmp_path / "f.yaml").write_text("x")
    result = handle_fs_glob({"root": str(tmp_path), "pattern": "*.yaml", "max_matches": "junk"})
    assert result["ok"] is True


def test_fs_glob_recursive_hidden_excluded(tmp_path: Path) -> None:
    hidden_dir = tmp_path / ".hidden"
    hidden_dir.mkdir()
    (hidden_dir / "x.yaml").write_text("x")
    (tmp_path / "visible.yaml").write_text("x")
    result = handle_fs_glob({"root": str(tmp_path), "pattern": "**/*.yaml"})
    assert "visible.yaml" in result["matches"]
    assert ".hidden/x.yaml" not in result["matches"]
