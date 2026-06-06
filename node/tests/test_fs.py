"""Tests for read-only filesystem command handlers."""

from __future__ import annotations

import base64
from pathlib import Path

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
    assert result["size"] == 100


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
    # resolve_safe follows symlinks; but stat is called on the resolved path.
    # Verify by calling stat on the *target* via a symlink whose resolve stays inside roots.
    result = handle_fs_stat({"path": str(link)})
    assert result["exists"] is True
    # After resolve_safe, link points at target so is_symlink should be False.
    assert result["is_symlink"] is False


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
