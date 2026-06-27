"""Tests for the fs.move and fs.delete command handlers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import openclaw_node.commands.fs_move_delete as md_mod
import pytest
from openclaw_node.commands.fs_move_delete import (
    _trash_dir,
    _trash_file,
    handle_fs_delete,
    handle_fs_move,
    reset_store_for_testing,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolated backup store and allowed roots for every test."""
    reset_store_for_testing(tmp_path / "store")
    monkeypatch.setenv("OPENCLAW_ALLOWED_ROOTS", str(tmp_path / "fs"))
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.setenv("OPENCLAW_BACKUP_ROOT", str(tmp_path / "store"))
    monkeypatch.setenv("OPENCLAW_TRASH_DIR", str(tmp_path / "trash"))
    (tmp_path / "fs").mkdir(parents=True, exist_ok=True)


@pytest.fixture
def src_file(tmp_path: Path) -> Path:
    """A pre-existing file in the allowed roots."""
    f = tmp_path / "fs" / "src.yaml"
    f.write_text("src: content\n", encoding="utf-8")
    return f


@pytest.fixture
def dst_file(tmp_path: Path) -> Path:
    """Destination path (does not pre-exist)."""
    return tmp_path / "fs" / "dst.yaml"


# ---------------------------------------------------------------------------
# _trash_file helper
# ---------------------------------------------------------------------------


def test_trash_file_uses_fallback_when_send2trash_missing(
    tmp_path: Path,
) -> None:
    """Falls back to OPENCLAW_TRASH_DIR when send2trash is not importable."""
    target = tmp_path / "fs" / "del.txt"
    target.write_text("bye", encoding="utf-8")
    # Simulate send2trash not installed by raising ImportError on import.
    with patch.dict("sys.modules", {"send2trash": None}):
        result = _trash_file(target)
    assert not target.exists()
    assert str(tmp_path / "trash") in result


def test_trash_dir_uses_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom = tmp_path / "my-trash"
    monkeypatch.setenv("OPENCLAW_TRASH_DIR", str(custom))
    assert _trash_dir() == custom


def test_trash_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_TRASH_DIR", raising=False)
    assert _trash_dir() == Path("/share/openclaw-trash")


def test_trash_file_send2trash_non_import_error_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-ImportError from send2trash (e.g. TrashPermissionError) propagates."""
    target = tmp_path / "fs" / "del.txt"
    target.write_text("bye", encoding="utf-8")

    import types

    fake_mod = types.ModuleType("send2trash")

    def boom(p: str) -> None:
        raise OSError("permission denied by trash")

    fake_mod.send2trash = boom  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "send2trash", fake_mod)
    with pytest.raises(OSError, match="permission denied by trash"):
        _trash_file(target)


def test_trash_file_uses_send2trash_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uses send2trash when it is importable."""
    target = tmp_path / "fs" / "del.txt"
    target.write_text("bye", encoding="utf-8")
    trashed: list[str] = []

    import types

    fake_mod = types.ModuleType("send2trash")
    fake_mod.send2trash = lambda p: trashed.append(p)  # type: ignore[attr-defined]
    monkeypatch.setitem(
        __import__("sys").modules,
        "send2trash",
        fake_mod,
    )
    result = _trash_file(target)
    assert result == "system trash"
    assert str(target) in trashed


# ---------------------------------------------------------------------------
# fs.move — parameter validation
# ---------------------------------------------------------------------------


def test_fs_move_missing_src() -> None:
    result = handle_fs_move({})
    assert result["ok"] is False
    assert result["error"] == "MISSING_PARAM"
    assert "src" in result["message"]


def test_fs_move_missing_dst(tmp_path: Path) -> None:
    result = handle_fs_move({"src": str(tmp_path / "fs" / "x.yaml")})
    assert result["ok"] is False
    assert result["error"] == "MISSING_PARAM"
    assert "dst" in result["message"]


def test_fs_move_src_protected() -> None:
    result = handle_fs_move(
        {"src": "/config/automations.yaml", "dst": "/tmp/x.yaml", "agent_bridge": False}
    )
    assert result["ok"] is False
    assert result["error"] == "PROPOSAL_REQUIRED"


def test_fs_move_dst_protected(tmp_path: Path) -> None:
    result = handle_fs_move(
        {"src": str(tmp_path / "fs" / "x.yaml"), "dst": "/config/x.yaml", "agent_bridge": False}
    )
    assert result["ok"] is False
    assert result["error"] == "PROPOSAL_REQUIRED"


def test_fs_move_src_storage() -> None:
    result = handle_fs_move(
        {"src": "/config/.storage/x", "dst": "/tmp/x.yaml", "agent_bridge": False}
    )
    assert result["ok"] is False
    assert result["error"] == "STORAGE_READONLY"


def test_fs_move_out_of_bounds(tmp_path: Path) -> None:
    result = handle_fs_move(
        {"src": "/var/secret", "dst": str(tmp_path / "fs" / "x.yaml"), "agent_bridge": False}
    )
    assert result["ok"] is False
    assert result["error"] == "PATH_NOT_ALLOWED"


def test_fs_move_src_not_found(tmp_path: Path) -> None:
    result = handle_fs_move(
        {
            "src": str(tmp_path / "fs" / "missing.yaml"),
            "dst": str(tmp_path / "fs" / "dst.yaml"),
            "agent_bridge": False,
        }
    )
    assert result["ok"] is False
    assert result["error"] == "NOT_FOUND"


def test_fs_move_src_eq_dst(tmp_path: Path, src_file: Path) -> None:
    result = handle_fs_move({"src": str(src_file), "dst": str(src_file), "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"


# ---------------------------------------------------------------------------
# fs.move — happy paths
# ---------------------------------------------------------------------------


def test_fs_move_simple(tmp_path: Path, src_file: Path, dst_file: Path) -> None:
    result = handle_fs_move({"src": str(src_file), "dst": str(dst_file), "agent_bridge": False})
    assert result["ok"] is True
    assert not src_file.exists()
    assert dst_file.read_text(encoding="utf-8") == "src: content\n"
    assert result["size"] == len(b"src: content\n")


def test_fs_move_captures_src_to_store(tmp_path: Path, src_file: Path, dst_file: Path) -> None:
    handle_fs_move(
        {
            "src": str(src_file),
            "dst": str(dst_file),
            "agent_bridge": False,
            "proposal_id": "p1",
        }
    )
    store = md_mod._get_store()
    versions = store.history(str(src_file))
    assert len(versions) == 1
    assert store.fetch_object(versions[0].sha256) == b"src: content\n"


def test_fs_move_overwrites_dst_and_captures(tmp_path: Path, src_file: Path) -> None:
    dst = tmp_path / "fs" / "dst.yaml"
    dst.write_text("old: data\n", encoding="utf-8")
    result = handle_fs_move(
        {"src": str(src_file), "dst": str(dst), "agent_bridge": False, "proposal_id": "p2"}
    )
    assert result["ok"] is True
    assert dst.read_text(encoding="utf-8") == "src: content\n"
    store = md_mod._get_store()
    dst_versions = store.history(str(dst))
    assert any(store.fetch_object(v.sha256) == b"old: data\n" for v in dst_versions)


def test_fs_move_default_agent_bridge_for_protected(tmp_path: Path, src_file: Path) -> None:
    """agent_bridge defaults to True when either path is protected."""
    result = handle_fs_move({"src": "/config/x.yaml", "dst": str(tmp_path / "fs" / "y.yaml")})
    assert result["ok"] is False
    assert result["error"] == "PROPOSAL_REQUIRED"


def test_fs_move_dst_out_of_bounds(tmp_path: Path, src_file: Path) -> None:
    result = handle_fs_move({"src": str(src_file), "dst": "/var/secret", "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "PATH_NOT_ALLOWED"


def test_fs_move_dst_post_resolution_check(
    tmp_path: Path, src_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dst symlink resolved into protected root is blocked."""
    call_count = 0
    original = __import__("openclaw_node.commands.fs_write", fromlist=["resolve_safe"]).resolve_safe

    def patched(path: str, roots: object) -> object:
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # second call is for dst
            return Path("/config/sneaky.yaml")
        return original(path, roots)

    monkeypatch.setattr("openclaw_node.commands.fs_write.resolve_safe", patched)
    result = handle_fs_move(
        {"src": str(src_file), "dst": str(tmp_path / "fs" / "other.yaml"), "agent_bridge": False}
    )
    assert result["ok"] is False
    assert result["error"] == "PROPOSAL_REQUIRED"


# ---------------------------------------------------------------------------
# fs.move — error paths
# ---------------------------------------------------------------------------


def test_fs_move_dst_read_error(
    tmp_path: Path, src_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dst = tmp_path / "fs" / "dst.yaml"
    dst.write_text("existing\n", encoding="utf-8")

    def bad_read(*a: object, **kw: object) -> bytes:
        raise OSError("permission denied")

    monkeypatch.setattr("openclaw_node.commands.fs_move_delete.read_bytes_safe", bad_read)
    result = handle_fs_move({"src": str(src_file), "dst": str(dst), "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "READ_ERROR"


def test_fs_move_src_read_error(
    tmp_path: Path, src_file: Path, dst_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bad_read(*a: object, **kw: object) -> bytes:
        raise OSError("permission denied")

    monkeypatch.setattr("openclaw_node.commands.fs_move_delete.read_bytes_safe", bad_read)
    result = handle_fs_move({"src": str(src_file), "dst": str(dst_file), "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "READ_ERROR"


def test_fs_move_backup_dst_error(
    tmp_path: Path, src_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openclaw_node.backup_store import BackupStoreError

    dst = tmp_path / "fs" / "dst.yaml"
    dst.write_text("existing\n", encoding="utf-8")

    call_count = 0

    def boom_capture(*a: object, **kw: object) -> object:
        nonlocal call_count
        call_count += 1
        raise BackupStoreError("store full")

    monkeypatch.setattr(md_mod._get_store(), "capture", boom_capture)
    result = handle_fs_move({"src": str(src_file), "dst": str(dst), "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "BACKUP_ERROR"


def test_fs_move_backup_src_error(
    tmp_path: Path, src_file: Path, dst_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openclaw_node.backup_store import BackupStoreError

    call_count = 0

    def boom_capture(*a: object, **kw: object) -> object:
        nonlocal call_count
        call_count += 1
        raise BackupStoreError("store full")

    monkeypatch.setattr(md_mod._get_store(), "capture", boom_capture)
    result = handle_fs_move({"src": str(src_file), "dst": str(dst_file), "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "BACKUP_ERROR"


def test_fs_move_operation_fails(
    tmp_path: Path, src_file: Path, dst_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: object, **kw: object) -> None:
        raise OSError("busy")

    monkeypatch.setattr("openclaw_node.commands.fs_move_delete.replace_safe", boom)
    result = handle_fs_move({"src": str(src_file), "dst": str(dst_file), "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "MOVE_ERROR"


def test_fs_move_cross_device_rejected(
    tmp_path: Path, src_file: Path, dst_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cross-device moves (EXDEV) return CROSS_DEVICE, not MOVE_ERROR."""
    import errno as _errno

    def exdev(*a: object, **kw: object) -> None:
        raise OSError(_errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr("openclaw_node.commands.fs_move_delete.replace_safe", exdev)
    result = handle_fs_move({"src": str(src_file), "dst": str(dst_file), "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "CROSS_DEVICE"


def test_fs_move_post_resolution_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symlink resolved into protected root is blocked on src."""
    # _resolve_write_target calls resolve_safe from fs_write, so patch there.
    monkeypatch.setattr(
        "openclaw_node.commands.fs_write.resolve_safe",
        lambda path, roots: Path("/config/sneaky.yaml"),
    )
    result = handle_fs_move(
        {"src": "/tmp/link.yaml", "dst": "/tmp/other.yaml", "agent_bridge": False}
    )
    assert result["ok"] is False
    assert result["error"] == "PROPOSAL_REQUIRED"


# ---------------------------------------------------------------------------
# fs.delete — parameter validation
# ---------------------------------------------------------------------------


def test_fs_delete_missing_path() -> None:
    result = handle_fs_delete({})
    assert result["ok"] is False
    assert result["error"] == "MISSING_PARAM"


def test_fs_delete_protected_path() -> None:
    result = handle_fs_delete({"path": "/config/automations.yaml"})
    assert result["ok"] is False
    assert result["error"] == "PROPOSAL_REQUIRED"


def test_fs_delete_protected_not_bypassed(tmp_path: Path) -> None:
    """agent_bridge=False cannot bypass protected roots."""
    result = handle_fs_delete({"path": "/config/automations.yaml", "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "PROPOSAL_REQUIRED"


def test_fs_delete_storage_refused() -> None:
    result = handle_fs_delete({"path": "/config/.storage/x"})
    assert result["ok"] is False
    assert result["error"] == "STORAGE_READONLY"


def test_fs_delete_out_of_bounds(tmp_path: Path) -> None:
    result = handle_fs_delete({"path": "/var/secret", "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "PATH_NOT_ALLOWED"


def test_fs_delete_not_found(tmp_path: Path) -> None:
    result = handle_fs_delete(
        {"path": str(tmp_path / "fs" / "missing.yaml"), "agent_bridge": False}
    )
    assert result["ok"] is False
    assert result["error"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# fs.delete — happy path
# ---------------------------------------------------------------------------


def test_fs_delete_trashes_file(tmp_path: Path, src_file: Path) -> None:
    result = handle_fs_delete({"path": str(src_file), "agent_bridge": False, "proposal_id": "del1"})
    assert result["ok"] is True
    assert not src_file.exists()
    assert result["path"] == str(src_file)
    # File must be in trash dir, not deleted permanently.
    trash = tmp_path / "trash"
    assert any(trash.iterdir())


def test_fs_delete_captures_to_store(tmp_path: Path, src_file: Path) -> None:
    handle_fs_delete({"path": str(src_file), "agent_bridge": False, "proposal_id": "del1"})
    store = md_mod._get_store()
    versions = store.history(str(src_file))
    assert len(versions) == 1
    assert store.fetch_object(versions[0].sha256) == b"src: content\n"
    assert versions[0].op == "delete"


# ---------------------------------------------------------------------------
# fs.delete — error paths
# ---------------------------------------------------------------------------


def test_fs_delete_read_error(
    tmp_path: Path, src_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "openclaw_node.commands.fs_move_delete.read_bytes_safe",
        lambda p, r: (_ for _ in ()).throw(OSError("denied")),
    )
    result = handle_fs_delete({"path": str(src_file), "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "READ_ERROR"


def test_fs_delete_backup_error(
    tmp_path: Path, src_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openclaw_node.backup_store import BackupStoreError

    def boom(*a: object, **kw: object) -> object:
        raise BackupStoreError("store full")

    monkeypatch.setattr(md_mod._get_store(), "capture", boom)
    result = handle_fs_delete({"path": str(src_file), "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "BACKUP_ERROR"


def test_fs_delete_trash_error(
    tmp_path: Path, src_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(path: Path) -> str:
        raise OSError("trash full")

    monkeypatch.setattr("openclaw_node.commands.fs_move_delete._trash_file", boom)
    result = handle_fs_delete({"path": str(src_file), "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "TRASH_ERROR"


def test_fs_delete_post_resolution_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symlink resolved into protected root is blocked."""
    monkeypatch.setattr(
        "openclaw_node.commands.fs_write.resolve_safe",
        lambda path, roots: Path("/config/sneaky.yaml"),
    )
    result = handle_fs_delete({"path": "/tmp/link.yaml", "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "PROPOSAL_REQUIRED"
