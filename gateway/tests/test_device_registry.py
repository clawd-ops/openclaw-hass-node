"""Tests for openclaw_gateway.device_registry."""

from __future__ import annotations

from pathlib import Path

import pytest
from openclaw_gateway.device_registry import DeviceRegistry, DeviceState


def test_register_new_device_is_pending() -> None:
    reg = DeviceRegistry()
    rec = reg.register_or_get("node-a", "pubkey")
    assert rec.state is DeviceState.PENDING
    assert rec.device_id == "node-a"
    assert rec.public_key_b64url == "pubkey"


def test_register_existing_device_returns_same_record() -> None:
    reg = DeviceRegistry()
    a = reg.register_or_get("node-a", "pubkey")
    b = reg.register_or_get("node-a", "pubkey")
    assert a is b


def test_register_device_with_different_key_raises() -> None:
    reg = DeviceRegistry()
    reg.register_or_get("node-a", "key1")
    with pytest.raises(ValueError, match="different public key"):
        reg.register_or_get("node-a", "key2")


def test_approve_transitions_to_paired_and_issues_token() -> None:
    reg = DeviceRegistry()
    reg.register_or_get("node-a", "pubkey")
    rec = reg.approve("node-a")
    assert rec.state is DeviceState.PAIRED
    assert rec.token  # non-empty


def test_approve_unknown_device_raises() -> None:
    reg = DeviceRegistry()
    with pytest.raises(KeyError):
        reg.approve("does-not-exist")


def test_approve_is_idempotent_for_token() -> None:
    """Approving twice keeps the same token (so reconnects survive)."""
    reg = DeviceRegistry()
    reg.register_or_get("node-a", "pubkey")
    first = reg.approve("node-a").token
    second = reg.approve("node-a").token
    assert first == second


def test_get_returns_none_for_unknown() -> None:
    assert DeviceRegistry().get("nope") is None


def test_revoke_removes_device() -> None:
    reg = DeviceRegistry()
    reg.register_or_get("node-a", "pubkey")
    reg.revoke("node-a")
    assert reg.get("node-a") is None


def test_revoke_unknown_is_safe() -> None:
    reg = DeviceRegistry()
    reg.revoke("never-existed")


def test_all_devices_returns_snapshot() -> None:
    reg = DeviceRegistry()
    reg.register_or_get("a", "k1")
    reg.register_or_get("b", "k2")
    ids = {r.device_id for r in reg.all_devices()}
    assert ids == {"a", "b"}


def test_persistence_round_trip(tmp_path: Path) -> None:
    """Records survive a reload via the JSON file."""

    path: Path = tmp_path / "devices.json"
    reg = DeviceRegistry(persist_path=path)
    reg.register_or_get("node-a", "pubkey-a")
    reg.approve("node-a")
    reg.register_or_get("node-b", "pubkey-b")

    reloaded = DeviceRegistry(persist_path=path)
    a = reloaded.get("node-a")
    b = reloaded.get("node-b")
    assert a is not None
    assert a.state is DeviceState.PAIRED
    assert a.token  # token persisted
    assert b is not None
    assert b.state is DeviceState.PENDING


def test_persistence_revoke_removes_from_file(tmp_path: Path) -> None:
    path = tmp_path / "devices.json"
    reg = DeviceRegistry(persist_path=path)
    reg.register_or_get("node-a", "pubkey")
    reg.revoke("node-a")

    reloaded = DeviceRegistry(persist_path=path)
    assert reloaded.get("node-a") is None


def test_persistence_ignores_malformed_file(tmp_path: Path) -> None:
    path = tmp_path / "devices.json"
    path.write_text("not json")
    reg = DeviceRegistry(persist_path=path)
    assert reg.all_devices() == []


def test_persistence_skips_malformed_entries(tmp_path: Path) -> None:
    import json

    path = tmp_path / "devices.json"
    path.write_text(
        json.dumps(
            {
                "devices": [
                    {"device_id": "ok", "public_key_b64url": "k", "state": "paired", "token": "t"},
                    {"device_id": "bad", "state": "not-a-real-state"},
                ]
            }
        )
    )
    reg = DeviceRegistry(persist_path=path)
    ids = {r.device_id for r in reg.all_devices()}
    assert ids == {"ok"}


def test_persistence_missing_file_is_safe(tmp_path: Path) -> None:
    """A first-run with no existing file just starts empty."""
    reg = DeviceRegistry(persist_path=tmp_path / "does-not-exist.json")
    assert reg.all_devices() == []
