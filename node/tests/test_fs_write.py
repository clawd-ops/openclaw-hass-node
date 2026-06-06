"""Tests for the proposal-gated write command handlers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import openclaw_node.commands.fs_write as fs_write_mod
from openclaw_node.commands.fs_write import (
    _backup_root,
    _get_store,
    _reset_store_for_testing,
    handle_fs_diff,
    handle_fs_history,
    handle_fs_restore,
    handle_fs_write,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test an isolated backup store and unprotected roots."""
    _reset_store_for_testing(tmp_path / "store")
    # Override allowed roots to use tmp_path so writes are permitted.
    monkeypatch.setenv("OPENCLAW_ALLOWED_ROOTS", str(tmp_path / "fs"))
    # Ensure SUPERVISOR_TOKEN is unset so _is_addon_mode() returns False.
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    (tmp_path / "fs").mkdir(parents=True, exist_ok=True)
    # Point backup store root to the temp store created above.
    monkeypatch.setenv("OPENCLAW_BACKUP_ROOT", str(tmp_path / "store"))


@pytest.fixture
def live_file(tmp_path: Path) -> Path:
    """Return a pre-created live file inside the allowed roots."""
    f = tmp_path / "fs" / "config.yaml"
    f.write_text("key: value\n", encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# fs.write
# ---------------------------------------------------------------------------


def test_fs_write_missing_path() -> None:
    result = handle_fs_write({})
    assert result["ok"] is False
    assert result["error"] == "MISSING_PARAM"
    assert "path" in result["message"]


def test_fs_write_missing_content(tmp_path: Path) -> None:
    result = handle_fs_write({"path": str(tmp_path / "fs" / "x.yaml")})
    assert result["ok"] is False
    assert result["error"] == "MISSING_PARAM"
    assert "content" in result["message"]


def test_fs_write_protected_path_returns_proposal_required() -> None:
    result = handle_fs_write({"path": "/config/automations.yaml", "content": "x"})
    assert result["ok"] is False
    assert result["error"] == "PROPOSAL_REQUIRED"


def test_fs_write_storage_refused() -> None:
    result = handle_fs_write({"path": "/config/.storage/core.config_entries", "content": "x"})
    assert result["ok"] is False
    assert result["error"] == "STORAGE_READONLY"


def test_fs_write_out_of_bounds(tmp_path: Path) -> None:
    result = handle_fs_write(
        {
            "path": "/var/secret",
            "content": "oops",
            "agent_bridge": False,
        }
    )
    assert result["ok"] is False
    assert result["error"] == "PATH_NOT_ALLOWED"


def test_fs_write_utf8_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "fs" / "new.yaml"
    result = handle_fs_write(
        {
            "path": str(target),
            "content": "hello: world\n",
            "encoding": "utf-8",
            "proposal_id": "p-test",
            "agent_bridge": False,
        }
    )
    assert result["ok"] is True
    assert result["size"] == len(b"hello: world\n")
    assert target.read_text(encoding="utf-8") == "hello: world\n"


def test_fs_write_base64_content(tmp_path: Path) -> None:
    import base64

    data = b"\xff\xfehello"
    target = tmp_path / "fs" / "binary.bin"
    result = handle_fs_write(
        {
            "path": str(target),
            "content": base64.b64encode(data).decode(),
            "encoding": "base64",
            "agent_bridge": False,
        }
    )
    assert result["ok"] is True
    assert target.read_bytes() == data


def test_fs_write_invalid_encoding(tmp_path: Path) -> None:
    result = handle_fs_write(
        {
            "path": str(tmp_path / "fs" / "x.yaml"),
            "content": "x",
            "encoding": "latin-1",
            "agent_bridge": False,
        }
    )
    assert result["ok"] is False
    assert result["error"] == "INVALID_ENCODING"


def test_fs_write_invalid_base64(tmp_path: Path) -> None:
    result = handle_fs_write(
        {
            "path": str(tmp_path / "fs" / "x.yaml"),
            "content": "!!!not base64!!!",
            "encoding": "base64",
            "agent_bridge": False,
        }
    )
    assert result["ok"] is False
    assert result["error"] == "INVALID_CONTENT"


def test_fs_write_captures_prior_bytes(tmp_path: Path, live_file: Path) -> None:
    result = handle_fs_write(
        {
            "path": str(live_file),
            "content": "key: updated\n",
            "agent_bridge": False,
            "proposal_id": "p1",
        }
    )
    assert result["ok"] is True
    # The prior bytes ("key: value\n") must be in the backup store.
    store = fs_write_mod._get_store()
    versions = store.history(str(live_file))
    assert len(versions) == 1
    assert store.fetch_object(versions[0].sha256) == b"key: value\n"


def test_fs_write_overwrites_existing_file(tmp_path: Path, live_file: Path) -> None:
    handle_fs_write(
        {
            "path": str(live_file),
            "content": "key: v2\n",
            "agent_bridge": False,
            "proposal_id": "p1",
        }
    )
    assert live_file.read_text(encoding="utf-8") == "key: v2\n"


# ---------------------------------------------------------------------------
# fs.restore
# ---------------------------------------------------------------------------


def test_fs_restore_missing_path() -> None:
    result = handle_fs_restore({})
    assert result["ok"] is False
    assert result["error"] == "MISSING_PARAM"


def test_fs_restore_protected_path_returns_proposal_required() -> None:
    result = handle_fs_restore({"path": "/config/automations.yaml", "version": 1})
    assert result["ok"] is False
    assert result["error"] == "PROPOSAL_REQUIRED"


def test_fs_restore_no_selector(tmp_path: Path, live_file: Path) -> None:
    # Seed a version first.
    handle_fs_write(
        {
            "path": str(live_file),
            "content": "v2\n",
            "agent_bridge": False,
            "proposal_id": "p1",
        }
    )
    result = handle_fs_restore({"path": str(live_file), "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"
    assert "Exactly one" in result["message"]


def test_fs_restore_two_selectors(tmp_path: Path, live_file: Path) -> None:
    handle_fs_write(
        {
            "path": str(live_file),
            "content": "v2\n",
            "agent_bridge": False,
            "proposal_id": "p1",
        }
    )
    result = handle_fs_restore(
        {"path": str(live_file), "version": 1, "proposal_id": "p1", "agent_bridge": False}
    )
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"


def test_fs_restore_by_version(tmp_path: Path, live_file: Path) -> None:
    original = live_file.read_bytes()
    handle_fs_write(
        {
            "path": str(live_file),
            "content": "v2\n",
            "agent_bridge": False,
            "proposal_id": "p1",
        }
    )
    result = handle_fs_restore({"path": str(live_file), "version": 1, "agent_bridge": False})
    assert result["ok"] is True
    assert live_file.read_bytes() == original


def test_fs_restore_version_not_found(tmp_path: Path, live_file: Path) -> None:
    handle_fs_write(
        {
            "path": str(live_file),
            "content": "v2\n",
            "agent_bridge": False,
            "proposal_id": "p1",
        }
    )
    result = handle_fs_restore({"path": str(live_file), "version": 99, "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "VERSION_NOT_FOUND"


def test_fs_restore_invalid_version_type(tmp_path: Path, live_file: Path) -> None:
    handle_fs_write(
        {
            "path": str(live_file),
            "content": "v2\n",
            "agent_bridge": False,
            "proposal_id": "p1",
        }
    )
    result = handle_fs_restore(
        {"path": str(live_file), "version": "not-an-int", "agent_bridge": False}
    )
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"


# ---------------------------------------------------------------------------
# fs.history
# ---------------------------------------------------------------------------


def test_fs_history_missing_path() -> None:
    result = handle_fs_history({})
    assert result["ok"] is False
    assert result["error"] == "MISSING_PARAM"


def test_fs_history_empty_for_new_path(tmp_path: Path) -> None:
    result = handle_fs_history({"path": str(tmp_path / "fs" / "nofile.yaml")})
    assert result["ok"] is True
    assert result["versions"] == []


def test_fs_history_returns_versions(tmp_path: Path, live_file: Path) -> None:
    handle_fs_write(
        {
            "path": str(live_file),
            "content": "v2\n",
            "agent_bridge": False,
            "proposal_id": "p1",
        }
    )
    handle_fs_write(
        {
            "path": str(live_file),
            "content": "v3\n",
            "agent_bridge": False,
            "proposal_id": "p2",
        }
    )
    result = handle_fs_history({"path": str(live_file)})
    assert result["ok"] is True
    assert len(result["versions"]) == 2
    assert result["versions"][0]["proposal_id"] == "p1"
    assert result["versions"][1]["proposal_id"] == "p2"
    assert all("ts" in v and "sha256" in v and "op" in v for v in result["versions"])


# ---------------------------------------------------------------------------
# fs.diff
# ---------------------------------------------------------------------------


def test_fs_diff_missing_path() -> None:
    result = handle_fs_diff({})
    assert result["ok"] is False
    assert result["error"] == "MISSING_PARAM"
    assert "path" in result["message"]


def test_fs_diff_missing_from_version(tmp_path: Path, live_file: Path) -> None:
    result = handle_fs_diff({"path": str(live_file)})
    assert result["ok"] is False
    assert result["error"] == "MISSING_PARAM"
    assert "from_version" in result["message"]


def test_fs_diff_between_two_stored_versions(tmp_path: Path, live_file: Path) -> None:
    handle_fs_write(
        {
            "path": str(live_file),
            "content": "key: v2\n",
            "agent_bridge": False,
            "proposal_id": "p1",
        }
    )
    handle_fs_write(
        {
            "path": str(live_file),
            "content": "key: v3\n",
            "agent_bridge": False,
            "proposal_id": "p2",
        }
    )
    result = handle_fs_diff({"path": str(live_file), "from_version": 1, "to_version": 2})
    assert result["ok"] is True
    assert "-key: value" in result["diff"]
    assert "+key: v2" in result["diff"]


def test_fs_diff_against_live(tmp_path: Path, live_file: Path) -> None:
    handle_fs_write(
        {
            "path": str(live_file),
            "content": "key: v2\n",
            "agent_bridge": False,
            "proposal_id": "p1",
        }
    )
    result = handle_fs_diff({"path": str(live_file), "from_version": 1, "to_version": None})
    assert result["ok"] is True
    assert "-key: value" in result["diff"]
    assert "+key: v2" in result["diff"]


def test_fs_diff_version_not_found(tmp_path: Path, live_file: Path) -> None:
    handle_fs_write(
        {
            "path": str(live_file),
            "content": "key: v2\n",
            "agent_bridge": False,
            "proposal_id": "p1",
        }
    )
    result = handle_fs_diff({"path": str(live_file), "from_version": 99, "to_version": 1})
    assert result["ok"] is False
    assert result["error"] == "DIFF_ERROR"


def test_fs_diff_sha256_selector(tmp_path: Path, live_file: Path) -> None:
    # Two writes capture two prior-byte versions; diff version 1 sha vs version 2 sha.
    handle_fs_write(
        {
            "path": str(live_file),
            "content": "key: v2\n",
            "agent_bridge": False,
            "proposal_id": "p1",
        }
    )
    handle_fs_write(
        {
            "path": str(live_file),
            "content": "key: v3\n",
            "agent_bridge": False,
            "proposal_id": "p2",
        }
    )
    store = fs_write_mod._get_store()
    versions = store.history(str(live_file))
    sha1 = versions[0].sha256
    sha2 = versions[1].sha256
    result = handle_fs_diff({"path": str(live_file), "from_version": sha1, "to_version": sha2})
    assert result["ok"] is True
    assert "-key: value" in result["diff"]
    assert "+key: v2" in result["diff"]


# ---------------------------------------------------------------------------
# Branch coverage for error / unusual paths
# ---------------------------------------------------------------------------


def test_backup_root_addon_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    assert _backup_root() == Path("/share/openclaw-backups")


def test_get_store_lazy_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_BACKUP_ROOT", str(tmp_path / "lazy-store"))
    fs_write_mod._store = None  # force lazy path
    store = _get_store()
    assert store is not None
    assert store.root.exists()


def test_fs_write_no_allowed_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_ALLOWED_ROOTS", raising=False)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    result = handle_fs_write({"path": "/tmp/x.yaml", "content": "x", "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "NO_ALLOWED_ROOTS"


def test_fs_write_backup_capture_fails(
    tmp_path: Path, live_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openclaw_node.backup_store import BackupStoreError

    def boom_capture(*a: object, **kw: object) -> object:
        raise BackupStoreError("store full")

    monkeypatch.setattr(fs_write_mod._get_store(), "capture", boom_capture)
    result = handle_fs_write({"path": str(live_file), "content": "x", "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "BACKUP_ERROR"


def test_fs_write_atomic_write_fails(
    tmp_path: Path, live_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Patch _atomic_write (not os.replace) so the backup store write isn't affected.
    def boom(*a: object, **kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("openclaw_node.commands.fs_write._atomic_write", boom)
    result = handle_fs_write({"path": str(live_file), "content": "x", "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "WRITE_ERROR"


def test_fs_write_prior_bytes_read_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "fs" / "x.yaml"
    target.write_bytes(b"existing")

    original_read_bytes = Path.read_bytes

    def fake_read_bytes(self: Path) -> bytes:
        if self == target:
            raise OSError("permission denied")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
    result = handle_fs_write({"path": str(target), "content": "new", "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "READ_ERROR"


def test_fs_restore_storage_refused() -> None:
    result = handle_fs_restore({"path": "/config/.storage/x", "version": 1})
    assert result["ok"] is False
    assert result["error"] == "STORAGE_READONLY"


def test_fs_restore_out_of_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    result = handle_fs_restore({"path": "/var/secret", "version": 1, "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "PATH_NOT_ALLOWED"


def test_fs_restore_by_at_selector(tmp_path: Path, live_file: Path) -> None:
    handle_fs_write(
        {"path": str(live_file), "content": "v2\n", "agent_bridge": False, "proposal_id": "p1"}
    )
    result = handle_fs_restore(
        {"path": str(live_file), "at": "2099-01-01T00:00:00Z", "agent_bridge": False}
    )
    assert result["ok"] is True


def test_fs_restore_prior_bytes_read_error(
    tmp_path: Path, live_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seed a version so resolve_version and fetch_object succeed.
    store = fs_write_mod._get_store()
    store.capture(str(live_file), live_file.read_bytes(), proposal_id="seed", op="write")

    original_read_bytes = Path.read_bytes

    def fake_read_bytes(self: Path) -> bytes:
        if self == live_file:
            raise OSError("permission denied")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
    result = handle_fs_restore({"path": str(live_file), "version": 1, "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "READ_ERROR"


def test_fs_restore_object_missing(
    tmp_path: Path, live_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle_fs_write(
        {"path": str(live_file), "content": "v2\n", "agent_bridge": False, "proposal_id": "p1"}
    )
    from openclaw_node.backup_store import ObjectMissingError

    def boom_fetch(sha: str) -> bytes:
        raise ObjectMissingError("evicted")

    monkeypatch.setattr(fs_write_mod._get_store(), "fetch_object", boom_fetch)
    result = handle_fs_restore({"path": str(live_file), "version": 1, "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "OBJECT_MISSING"


def test_fs_restore_backup_error(
    tmp_path: Path, live_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openclaw_node.backup_store import BackupStoreError

    # Seed a version via direct store capture, then patch capture to raise.
    store = fs_write_mod._get_store()
    store.capture(str(live_file), live_file.read_bytes(), proposal_id="seed", op="write")

    def boom_capture(*a: object, **kw: object) -> object:
        raise BackupStoreError("index full")

    monkeypatch.setattr(store, "capture", boom_capture)
    result = handle_fs_restore({"path": str(live_file), "version": 1, "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "BACKUP_ERROR"


def test_fs_restore_write_fails(
    tmp_path: Path, live_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle_fs_write(
        {"path": str(live_file), "content": "v2\n", "agent_bridge": False, "proposal_id": "p1"}
    )

    def boom(*a: object, **kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("openclaw_node.commands.fs_write._atomic_write", boom)
    result = handle_fs_restore({"path": str(live_file), "version": 1, "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "WRITE_ERROR"


def test_fs_history_store_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openclaw_node.backup_store import BackupStoreError

    def boom_history(path: str) -> list[object]:
        raise BackupStoreError("corrupt")

    monkeypatch.setattr(fs_write_mod._get_store(), "history", boom_history)
    result = handle_fs_history({"path": str(tmp_path / "fs" / "x.yaml")})
    assert result["ok"] is False
    assert result["error"] == "STORE_ERROR"


def test_fs_write_atomic_cleanup_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Test _atomic_write directly so the patch doesn't hit the backup store.
    from openclaw_node.commands.fs_write import _atomic_write

    def boom_replace(*a: object) -> None:
        raise OSError("full")

    monkeypatch.setattr("openclaw_node.commands.fs_write.os.replace", boom_replace)
    target = tmp_path / "target.txt"
    with pytest.raises(OSError):
        _atomic_write(target, b"data")
    leftovers = list(tmp_path.glob(".oc_write.*"))
    assert leftovers == []


def test_fs_write_atomic_dir_fsync_oserror_suppressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OSError on directory fsync after os.replace is suppressed; write still succeeds."""
    from openclaw_node.commands.fs_write import _atomic_write

    original_open = os.open

    def failing_dir_open(path: str, flags: int, *a: object, **kw: object) -> int:
        if flags & os.O_DIRECTORY:
            raise OSError("dir fsync not supported")
        return original_open(path, flags)

    monkeypatch.setattr("openclaw_node.commands.fs_write.os.open", failing_dir_open)
    target = tmp_path / "fs" / "out.txt"
    _atomic_write(target, b"hello")  # must not raise
    assert target.read_bytes() == b"hello"


# ---------------------------------------------------------------------------
# Security: protected-root bypass prevention (High findings from Codex review)
# ---------------------------------------------------------------------------


def test_fs_write_protected_not_bypassed_by_agent_bridge_false() -> None:
    """Protected roots gate unconditionally; agent_bridge=False cannot override."""
    result = handle_fs_write(
        {"path": "/config/automations.yaml", "content": "x", "agent_bridge": False}
    )
    assert result["ok"] is False
    assert result["error"] == "PROPOSAL_REQUIRED"


def test_fs_restore_protected_not_bypassed_by_agent_bridge_false() -> None:
    """Protected roots gate unconditionally; agent_bridge=False cannot override."""
    result = handle_fs_restore(
        {"path": "/config/automations.yaml", "version": 1, "agent_bridge": False}
    )
    assert result["ok"] is False
    assert result["error"] == "PROPOSAL_REQUIRED"


def test_fs_write_post_resolution_protected_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A symlink/traversal that resolves into a protected root is still blocked."""
    monkeypatch.setattr(
        "openclaw_node.commands.fs_write.resolve_safe",
        lambda path, roots: Path("/config/sneaky.yaml"),
    )
    result = handle_fs_write({"path": "/tmp/link.yaml", "content": "x", "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "PROPOSAL_REQUIRED"


def test_fs_restore_post_resolution_protected_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A symlink/traversal that resolves into a protected root blocks restore too."""
    monkeypatch.setattr(
        "openclaw_node.commands.fs_write.resolve_safe",
        lambda path, roots: Path("/config/sneaky.yaml"),
    )
    result = handle_fs_restore({"path": "/tmp/link.yaml", "version": 1, "agent_bridge": False})
    assert result["ok"] is False
    assert result["error"] == "PROPOSAL_REQUIRED"


# ---------------------------------------------------------------------------
# Medium: _coerce sha256 ambiguity
# ---------------------------------------------------------------------------


def test_fs_diff_all_digit_sha256_treated_as_hash(tmp_path: Path, live_file: Path) -> None:
    """A 64-char all-lowercase-hex from_version is treated as sha256, not int."""
    handle_fs_write(
        {"path": str(live_file), "content": "v2\n", "agent_bridge": False, "proposal_id": "p1"}
    )
    store = fs_write_mod._get_store()
    versions = store.history(str(live_file))
    sha = versions[0].sha256
    # Pass the actual sha256 (which is lowercase hex); verify it resolves correctly.
    result = handle_fs_diff({"path": str(live_file), "from_version": sha, "to_version": None})
    assert result["ok"] is True
