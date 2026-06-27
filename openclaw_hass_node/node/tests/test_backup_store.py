"""Tests for the per-file content-addressed backup store."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from openclaw_node.backup_store import (
    BackupStore,
    BackupStoreError,
    ObjectMissingError,
    Version,
    VersionNotFoundError,
)


@pytest.fixture
def store(tmp_path: Path) -> BackupStore:
    """Return a fresh store rooted at *tmp_path*."""
    return BackupStore(tmp_path / "store")


def test_constructor_creates_layout(tmp_path: Path) -> None:
    root = tmp_path / "fresh"
    BackupStore(root)
    assert (root / "objects").is_dir()
    assert (root / "index").is_dir()
    assert (root / "tmp").is_dir()
    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    assert meta["store_version"] == 1
    assert meta["cap_bytes"] == 500 * 1024 * 1024


def test_constructor_is_idempotent_on_existing_store(tmp_path: Path) -> None:
    root = tmp_path / "store"
    BackupStore(root)
    BackupStore(root)
    assert (root / "meta.json").exists()


def test_constructor_rejects_corrupt_meta(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir()
    (root / "meta.json").write_text("not json", encoding="utf-8")
    with pytest.raises(BackupStoreError, match=r"Corrupt meta\.json"):
        BackupStore(root)


def test_constructor_rejects_unknown_store_version(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir()
    (root / "meta.json").write_text(json.dumps({"store_version": 999}), encoding="utf-8")
    with pytest.raises(BackupStoreError, match="Unsupported store_version"):
        BackupStore(root)


def test_constructor_rejects_non_object_meta(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir()
    (root / "meta.json").write_text("[]", encoding="utf-8")
    with pytest.raises(BackupStoreError, match="Unsupported store_version"):
        BackupStore(root)


def test_capture_writes_object_and_index_line(store: BackupStore) -> None:
    v = store.capture(
        "/config/configuration.yaml",
        b"hello",
        proposal_id="p1",
        op="write",
    )
    assert v.sha256 == _sha256(b"hello")
    assert v.size == 5
    assert v.op == "write"
    assert v.actor == "clawd"
    assert v.prev_sha256 is None
    obj_path = store.root / "objects" / v.sha256[:2] / v.sha256
    assert obj_path.read_bytes() == b"hello"
    idx_path = store.root / "index" / "%2Fconfig%2Fconfiguration.yaml.jsonl"
    lines = idx_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    recorded = Version.from_json(lines[0])
    assert recorded == v


def test_capture_dedups_identical_object(store: BackupStore) -> None:
    store.capture("/config/a.yaml", b"same", proposal_id="p1", op="write")
    store.capture("/config/b.yaml", b"same", proposal_id="p2", op="write")
    sha = _sha256(b"same")
    obj_dir = store.root / "objects" / sha[:2]
    assert list(obj_dir.iterdir()) == [obj_dir / sha]


def test_capture_threads_prev_sha(store: BackupStore) -> None:
    first = store.capture("/config/x.yaml", b"v1", proposal_id="p1", op="write")
    second = store.capture("/config/x.yaml", b"v2", proposal_id="p2", op="write")
    third = store.capture("/config/x.yaml", b"v3", proposal_id="p3", op="write")
    assert first.prev_sha256 is None
    assert second.prev_sha256 == first.sha256
    assert third.prev_sha256 == second.sha256


def test_capture_records_custom_actor(store: BackupStore) -> None:
    v = store.capture("/config/x.yaml", b"hi", proposal_id="p1", op="write", actor="codex")
    assert v.actor == "codex"


def test_history_empty_for_unknown_path(store: BackupStore) -> None:
    assert store.history("/config/nothing.yaml") == []


def test_history_returns_versions_in_order(store: BackupStore) -> None:
    v1 = store.capture("/config/x.yaml", b"a", proposal_id="p1", op="write")
    v2 = store.capture("/config/x.yaml", b"b", proposal_id="p2", op="write")
    v3 = store.capture("/config/x.yaml", b"c", proposal_id="p3", op="delete")
    assert store.history("/config/x.yaml") == [v1, v2, v3]


def test_history_rejects_corrupt_line(store: BackupStore) -> None:
    store.capture("/config/x.yaml", b"a", proposal_id="p1", op="write")
    idx = store.root / "index" / "%2Fconfig%2Fx.yaml.jsonl"
    with idx.open("a", encoding="utf-8") as fh:
        fh.write("not json\n")
    with pytest.raises(BackupStoreError, match="Corrupt index line"):
        store.history("/config/x.yaml")


def test_fetch_object_returns_bytes(store: BackupStore) -> None:
    v = store.capture("/config/x.yaml", b"payload", proposal_id="p1", op="write")
    assert store.fetch_object(v.sha256) == b"payload"


def test_fetch_object_missing_raises(store: BackupStore) -> None:
    with pytest.raises(ObjectMissingError):
        store.fetch_object("0" * 64)


def test_resolve_version_by_index(store: BackupStore) -> None:
    v1 = store.capture("/config/x.yaml", b"a", proposal_id="p1", op="write")
    v2 = store.capture("/config/x.yaml", b"b", proposal_id="p2", op="write")
    assert store.resolve_version("/config/x.yaml", version=1) == v1
    assert store.resolve_version("/config/x.yaml", version=2) == v2
    assert store.resolve_version("/config/x.yaml", version=-1) == v2


def test_resolve_version_by_proposal(store: BackupStore) -> None:
    v1 = store.capture("/config/x.yaml", b"a", proposal_id="p1", op="write")
    v2 = store.capture("/config/x.yaml", b"b", proposal_id="p2", op="write")
    assert store.resolve_version("/config/x.yaml", proposal_id="p1") == v1
    assert store.resolve_version("/config/x.yaml", proposal_id="p2") == v2


def test_resolve_version_by_at_picks_latest_le(store: BackupStore) -> None:
    times = iter(["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z"])
    with patch("openclaw_node.backup_store._utc_now_iso", side_effect=lambda: next(times)):
        store.capture("/config/x.yaml", b"a", proposal_id="p1", op="write")
        store.capture("/config/x.yaml", b"b", proposal_id="p2", op="write")
        v3 = store.capture("/config/x.yaml", b"c", proposal_id="p3", op="write")
    chosen = store.resolve_version("/config/x.yaml", at="2026-01-03T12:00:00Z")
    assert chosen == v3


def test_resolve_version_no_versions_raises(store: BackupStore) -> None:
    with pytest.raises(VersionNotFoundError, match="No versions recorded"):
        store.resolve_version("/config/missing.yaml", version=1)


def test_resolve_version_out_of_range_raises(store: BackupStore) -> None:
    store.capture("/config/x.yaml", b"a", proposal_id="p1", op="write")
    with pytest.raises(VersionNotFoundError, match="out of range"):
        store.resolve_version("/config/x.yaml", version=5)


def test_resolve_version_proposal_not_found_raises(store: BackupStore) -> None:
    store.capture("/config/x.yaml", b"a", proposal_id="p1", op="write")
    with pytest.raises(VersionNotFoundError, match="No version with proposal_id"):
        store.resolve_version("/config/x.yaml", proposal_id="nope")


def test_resolve_version_at_before_any_raises(store: BackupStore) -> None:
    with patch(
        "openclaw_node.backup_store._utc_now_iso",
        return_value="2026-06-01T00:00:00Z",
    ):
        store.capture("/config/x.yaml", b"a", proposal_id="p1", op="write")
    with pytest.raises(VersionNotFoundError, match="No version at or before"):
        store.resolve_version("/config/x.yaml", at="2026-01-01T00:00:00Z")


def test_resolve_version_requires_exactly_one_selector(store: BackupStore) -> None:
    store.capture("/config/x.yaml", b"a", proposal_id="p1", op="write")
    with pytest.raises(ValueError, match="Exactly one"):
        store.resolve_version("/config/x.yaml")
    with pytest.raises(ValueError, match="Exactly one"):
        store.resolve_version("/config/x.yaml", version=1, proposal_id="p1")


def test_diff_between_two_versions(store: BackupStore) -> None:
    store.capture("/config/x.yaml", b"foo\nbar\n", proposal_id="p1", op="write")
    store.capture("/config/x.yaml", b"foo\nbaz\n", proposal_id="p2", op="write")
    out = store.diff("/config/x.yaml", from_version=1, to_version=2)
    assert "-bar" in out
    assert "+baz" in out


def test_diff_against_current_live_file(store: BackupStore, tmp_path: Path) -> None:
    live = tmp_path / "live.yaml"
    live.write_text("foo\nlive\n", encoding="utf-8")
    store.capture(str(live), b"foo\nbar\n", proposal_id="p1", op="write")
    out = store.diff(str(live), from_version=1, to_version=None)
    assert "-bar" in out
    assert "+live" in out


def test_diff_missing_live_file_raises(store: BackupStore, tmp_path: Path) -> None:
    store.capture(str(tmp_path / "ghost.yaml"), b"foo", proposal_id="p1", op="write")
    with pytest.raises(BackupStoreError, match="Cannot read live bytes"):
        store.diff(str(tmp_path / "ghost.yaml"), from_version=1, to_version=None)


def test_diff_by_sha256_selector(store: BackupStore) -> None:
    v1 = store.capture("/config/x.yaml", b"foo\nbar\n", proposal_id="p1", op="write")
    v2 = store.capture("/config/x.yaml", b"foo\nbaz\n", proposal_id="p2", op="write")
    out = store.diff("/config/x.yaml", from_version=v1.sha256, to_version=v2.sha256)
    assert "-bar" in out
    assert "+baz" in out


def test_diff_unknown_sha256_raises(store: BackupStore) -> None:
    store.capture("/config/x.yaml", b"foo", proposal_id="p1", op="write")
    with pytest.raises(VersionNotFoundError, match="No version with sha256"):
        store.diff("/config/x.yaml", from_version="deadbeef", to_version=1)


def test_diff_evicted_side_raises(store: BackupStore) -> None:
    store.capture("/config/x.yaml", b"foo", proposal_id="p1", op="write")
    idx = store.root / "index" / "%2Fconfig%2Fx.yaml.jsonl"
    line = json.loads(idx.read_text(encoding="utf-8"))
    line["evicted"] = True
    idx.write_text(json.dumps(line) + "\n", encoding="utf-8")
    with pytest.raises(ObjectMissingError, match="evicted"):
        store.diff("/config/x.yaml", from_version=1, to_version=1)


def test_diff_handles_binary_content(store: BackupStore) -> None:
    store.capture("/config/x.bin", b"\xff\xfeAB", proposal_id="p1", op="write")
    store.capture("/config/x.bin", b"\xff\xfeAC", proposal_id="p2", op="write")
    out = store.diff("/config/x.bin", from_version=1, to_version=2)
    assert out  # not empty, no exception


def test_version_from_json_rejects_non_object() -> None:
    with pytest.raises(BackupStoreError, match="not a JSON object"):
        Version.from_json("[]")


def test_version_from_json_rejects_missing_field() -> None:
    with pytest.raises(BackupStoreError, match="missing field"):
        Version.from_json(json.dumps({"ts": "now"}))


def test_version_from_json_rejects_unknown_op() -> None:
    payload = {
        "ts": "x",
        "proposal_id": "p",
        "sha256": "s",
        "size": 0,
        "op": "exec",
        "actor": "a",
        "prev_sha256": None,
    }
    with pytest.raises(BackupStoreError, match="Unknown op"):
        Version.from_json(json.dumps(payload))


def test_version_to_json_roundtrip() -> None:
    v = Version(
        ts="2026-01-01T00:00:00Z",
        proposal_id="p1",
        sha256="abc",
        size=4,
        op="delete",
        actor="clawd",
        prev_sha256=None,
        evicted=True,
    )
    assert Version.from_json(v.to_json()) == v


def test_encode_path_length_cap_raises(store: BackupStore) -> None:
    # 300 'a' chars encodes to 300 (no special chars) — exceeds the 250-char cap.
    long_path = "/" + "a" * 300
    with pytest.raises(BackupStoreError, match="too long"):
        store.capture(long_path, b"x", proposal_id="p1", op="write")


def test_atomic_write_cleans_tmp_on_failure(
    store: BackupStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("boom")

    monkeypatch.setattr("openclaw_node.backup_store.os.replace", boom)
    with pytest.raises(OSError):
        store.capture("/config/x.yaml", b"data", proposal_id="p1", op="write")
    leftovers = list((store.root / "tmp").iterdir())
    assert leftovers == []


def test_atomic_write_swallows_unlink_failure(
    store: BackupStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def replace_boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("replace failed")

    def unlink_boom(_path: str) -> None:
        raise OSError("unlink failed")

    monkeypatch.setattr("openclaw_node.backup_store.os.replace", replace_boom)
    monkeypatch.setattr("openclaw_node.backup_store.os.unlink", unlink_boom)
    with pytest.raises(OSError, match="replace failed"):
        store.capture("/config/x.yaml", b"data", proposal_id="p1", op="write")


def test_encode_path_cap_raised_to_250(store: BackupStore) -> None:
    # 251 raw chars encodes to >250 (each '/' -> '%2F'); verify the new cap allows
    # realistic long paths (raw path under ~83 chars fits inside 250 encoded).
    # 250-char raw path of slashes: each '/' -> 3 chars = 750 encoded - too long.
    # But a realistic HA path like /config/custom_components/name/subdir/x.yaml is fine.
    ok_path = "/config/custom_components/" + "a" * 60 + ".yaml"  # ~92 raw chars, ~115 encoded
    v = store.capture(ok_path, b"x", proposal_id="p1", op="write")
    assert v.sha256 == _sha256(b"x")


def test_from_json_rejects_non_integer_size() -> None:
    payload = {
        "ts": "2026-01-01T00:00:00Z",
        "proposal_id": "p",
        "sha256": "s",
        "size": "not-an-int",
        "op": "write",
        "actor": "a",
        "prev_sha256": None,
    }
    with pytest.raises(BackupStoreError, match="Corrupt index line"):
        Version.from_json(json.dumps(payload))


def test_resolve_version_by_at_with_offset_string(store: BackupStore) -> None:
    with patch(
        "openclaw_node.backup_store._utc_now_iso",
        return_value="2026-01-01T00:00:00Z",
    ):
        v = store.capture("/config/x.yaml", b"a", proposal_id="p1", op="write")
    # Pass `at` as an explicit UTC offset string instead of Z-suffix.
    chosen = store.resolve_version("/config/x.yaml", at="2026-01-01T00:01:00+00:00")
    assert chosen == v


def test_atomic_write_fsyncs_parent_dir(
    store: BackupStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    fsynced_dirs: list[str] = []
    real_open = os.open

    def tracking_open(path: str, flags: int, *args: object, **kwargs: object) -> int:
        fd = real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]
        if flags & os.O_DIRECTORY:
            fsynced_dirs.append(path)
        return fd

    monkeypatch.setattr("openclaw_node.backup_store.os.open", tracking_open)
    store.capture("/config/x.yaml", b"data", proposal_id="p1", op="write")
    assert any("objects" in d for d in fsynced_dirs)


def test_append_line_fsync_failure_propagates(
    store: BackupStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_fsync = os.fsync
    call_count = 0

    def selective_fsync(fd: int) -> None:
        nonlocal call_count
        call_count += 1
        if call_count > 1:  # first call is for the object write; second is the append
            raise OSError("fsync failed")
        real_fsync(fd)

    monkeypatch.setattr("openclaw_node.backup_store.os.fsync", selective_fsync)
    with pytest.raises(OSError, match="fsync failed"):
        store.capture("/config/x.yaml", b"data", proposal_id="p1", op="write")


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()
