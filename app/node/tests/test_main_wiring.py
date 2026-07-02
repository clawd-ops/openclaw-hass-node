"""Smoke tests for the production process wiring in ``openclaw_node.__main__``.

The full ``_main`` coroutine binds a real TCP port and runs forever, so we
test the wiring through :func:`build_runtime`, which is the pure factory
that assembles the shared :class:`NodeRuntime` and the gateway client.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from openclaw_node.__main__ import (
    _ha_user_id_by_name,
    _initial_device_token,
    _reset_pairing_state,
    _resolve_identity_usernames,
    build_runtime,
)
from openclaw_node.config import IdentityConfig, NodeConfig
from openclaw_node.http_api import NodeRuntime
from openclaw_node.identity import DeviceIdentity, load_or_generate
from openclaw_node.pairing import PairingState


@pytest.fixture
def config(tmp_path: Path) -> NodeConfig:
    """Return a minimal in-process config rooted at ``tmp_path``."""
    return NodeConfig(
        addon_mode=False,
        gateway_url="wss://gateway.example/ws",
        pairing_token="",
        node_name="test-node",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
    )


@pytest.fixture
def identity(config: NodeConfig) -> DeviceIdentity:
    """Generate a throwaway device identity under the test data dir."""
    ident, _ = load_or_generate(config.key_path)
    return ident


def test_build_runtime_shares_runtime_with_gateway(
    config: NodeConfig, identity: DeviceIdentity
) -> None:
    """Both gateway clients must hold the same runtime instance as the HTTP API."""
    runtime, node_client, operator_client = build_runtime(config, identity)

    assert runtime.config is config
    assert runtime.pairing_state is PairingState.UNKNOWN
    assert runtime.gateway_connected is False
    assert node_client._runtime is runtime
    assert operator_client._runtime is runtime
    # Role + scope wiring for the P5.13 dual-WS world.
    assert node_client._role == "node"
    assert node_client._scopes == []
    assert node_client._chat_relay_enabled is False
    assert node_client._invoke_dispatch_enabled is True
    assert operator_client._role == "operator"
    assert "operator.write" in operator_client._scopes
    assert operator_client._chat_relay_enabled is True
    assert operator_client._invoke_dispatch_enabled is False
    assert operator_client._pair_fallback_enabled is False


def test_ha_user_id_by_name_extracts_names_and_credentials() -> None:
    """HA config/auth/list names come from stable usernames, not display names."""
    result = {
        "users": [
            {
                "id": "uuid-rob",
                "name": "Mutable Display Name",
                "credentials": [
                    {
                        "auth_provider_type": "homeassistant",
                        "data": {"username": "bigrob8181"},
                    }
                ],
            },
            {"id": "uuid-ash", "username": "ash"},
        ]
    }

    assert _ha_user_id_by_name(result) == {
        "bigrob8181": "uuid-rob",
        "ash": "uuid-ash",
    }


async def test_resolve_identity_usernames_maps_to_ids(
    monkeypatch: pytest.MonkeyPatch,
    config: NodeConfig,
) -> None:
    """Configured identity usernames resolve to signed HA user IDs."""
    config = replace(
        config,
        identity=IdentityConfig(
            super_admins=frozenset({"BigRob8181"}),
            user_agent_map={"Ash": "clawd-household"},
        ),
    )

    async def fake_ws_call(msg_type: str) -> dict[str, Any]:
        assert msg_type == "config/auth/list"
        return {
            "users": [
                {"id": "rob-uuid", "username": "bigrob8181"},
                {"id": "ash-uuid", "username": "ash"},
            ]
        }

    monkeypatch.setattr("openclaw_node.__main__.ha_ws_call", fake_ws_call)

    resolved = await _resolve_identity_usernames(config)

    assert resolved.identity.super_admins == frozenset({"rob-uuid"})
    assert resolved.identity.user_agent_map == {"ash-uuid": "clawd-household"}


async def test_resolve_identity_usernames_drops_unknown_entries(
    monkeypatch: pytest.MonkeyPatch,
    config: NodeConfig,
) -> None:
    """Unknown configured usernames are ignored without blocking startup."""
    config = replace(
        config,
        identity=IdentityConfig(
            super_admins=frozenset({"bigrob8181", "unknown"}),
            user_agent_map={"unknown-route": "clawd-household"},
        ),
    )

    async def fake_ws_call(msg_type: str) -> list[dict[str, Any]]:
        assert msg_type == "config/auth/list"
        return [{"id": "rob-uuid", "username": "bigrob8181"}]

    monkeypatch.setattr("openclaw_node.__main__.ha_ws_call", fake_ws_call)

    resolved = await _resolve_identity_usernames(config)

    assert resolved.identity.super_admins == frozenset({"rob-uuid"})
    assert resolved.identity.user_agent_map == {}


async def test_resolve_identity_usernames_fails_closed_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
    config: NodeConfig,
) -> None:
    """A slow HA config/auth/list call must not abort add-on startup."""
    config = replace(
        config,
        identity=IdentityConfig(
            super_admins=frozenset({"bigrob8181"}),
            user_agent_map={"ash": "clawd-household"},
        ),
    )

    async def fake_ws_call(msg_type: str) -> dict[str, Any]:
        assert msg_type == "config/auth/list"
        raise TimeoutError("timed out")

    monkeypatch.setattr("openclaw_node.__main__.ha_ws_call", fake_ws_call)

    resolved = await _resolve_identity_usernames(config)

    assert resolved.identity.super_admins == frozenset()
    assert resolved.identity.user_agent_map == {}


@pytest.mark.parametrize(
    ("state", "expected_paired"),
    [
        (PairingState.PAIRED, True),
        (PairingState.PENDING, False),
        (PairingState.ERROR, False),
        (PairingState.UNKNOWN, False),
    ],
)
def test_build_runtime_wires_pairing_callback(
    config: NodeConfig,
    identity: DeviceIdentity,
    state: PairingState,
    expected_paired: bool,
) -> None:
    """Pairing-state callback must mutate the shared runtime, not a private copy."""
    runtime, node_client, _operator_client = build_runtime(config, identity)

    callback = node_client._pairing_state_callback
    assert callback is not None

    callback(state)
    assert runtime.pairing_state is state
    assert runtime.is_paired is expected_paired


def test_initial_device_token_prefers_persisted(
    config: NodeConfig,
) -> None:
    """A persisted per-role device token must win over the one-shot pairing token."""
    node_path = config.device_token_path_for("node")
    node_path.parent.mkdir(parents=True, exist_ok=True)
    node_path.write_text("persisted-node-token\n")
    op_path = config.device_token_path_for("operator")
    op_path.write_text("persisted-operator-token\n")
    object.__setattr__(config, "pairing_token", "one-shot")

    assert _initial_device_token(config, role="node") == "persisted-node-token"
    assert _initial_device_token(config, role="operator") == "persisted-operator-token"


def test_initial_device_token_per_role_isolated(config: NodeConfig) -> None:
    """A node-only token must NOT satisfy an operator lookup (and vice versa)."""
    node_path = config.device_token_path_for("node")
    node_path.parent.mkdir(parents=True, exist_ok=True)
    node_path.write_text("only-node\n")
    object.__setattr__(config, "pairing_token", "boot")

    assert _initial_device_token(config, role="node") == "only-node"
    # operator has no persisted token; must fall back to the bootstrap.
    assert _initial_device_token(config, role="operator") == "boot"


def test_initial_device_token_falls_back_to_pairing(
    config: NodeConfig,
) -> None:
    """When no persisted token exists, the pairing token is used."""
    object.__setattr__(config, "pairing_token", "one-shot")

    assert _initial_device_token(config) == "one-shot"


def test_initial_device_token_returns_none_when_neither_present(
    config: NodeConfig,
) -> None:
    """With neither token, the client connects token-less and triggers pairing."""
    assert _initial_device_token(config) is None


def test_initial_device_token_whitespace_persisted_falls_back(
    config: NodeConfig,
) -> None:
    """A whitespace-only persisted token must not mask the one-shot pairing token."""
    node_path = config.device_token_path_for("node")
    node_path.parent.mkdir(parents=True, exist_ok=True)
    node_path.write_text("   \n")
    object.__setattr__(config, "pairing_token", "one-shot")

    assert _initial_device_token(config) == "one-shot"


def test_legacy_device_token_migrates_to_node_role_path(config: NodeConfig) -> None:
    """A legacy ``data_dir/device-token`` file must migrate to the node-role path."""
    from openclaw_node.__main__ import _migrate_legacy_device_token

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.device_token_path.write_text("legacy-token\n")
    target = config.device_token_path_for("node")
    assert not target.exists()

    _migrate_legacy_device_token(config)

    assert not config.device_token_path.exists()
    assert target.exists()
    assert target.read_text() == "legacy-token"


def test_legacy_migration_refuses_symlink(config: NodeConfig, tmp_path: Path) -> None:
    """A symlinked legacy device-token must NOT be migrated (would carry the symlink)."""
    from openclaw_node.__main__ import _migrate_legacy_device_token

    config.data_dir.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "evil.txt"
    outside.write_text("evil")
    config.device_token_path.symlink_to(outside)

    _migrate_legacy_device_token(config)

    # Per-role file NOT created via symlink follow.
    assert not config.device_token_path_for("node").exists()
    # Symlink itself stays in place (we refused to touch it).
    assert config.device_token_path.is_symlink()


def test_legacy_migration_token_lands_at_mode_0o600(
    config: NodeConfig,
) -> None:
    """Migrated token must be 0o600 even if the legacy file was world-readable."""
    from openclaw_node.__main__ import _migrate_legacy_device_token

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.device_token_path.write_text("legacy\n")
    config.device_token_path.chmod(0o644)

    _migrate_legacy_device_token(config)

    target = config.device_token_path_for("node")
    assert target.exists()
    assert (target.stat().st_mode & 0o777) == 0o600


def test_legacy_migration_skips_when_target_already_exists(config: NodeConfig) -> None:
    """If the per-role path already holds a (newer) token, do NOT clobber it."""
    from openclaw_node.__main__ import _migrate_legacy_device_token

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.device_token_path.write_text("stale-legacy\n")
    target = config.device_token_path_for("node")
    target.write_text("fresh-per-role\n")

    _migrate_legacy_device_token(config)

    # Per-role wins; legacy remains untouched as a no-op (idempotent).
    assert target.read_text() == "fresh-per-role\n"
    assert config.device_token_path.exists()


def test_reset_pairing_state_token_mode_wipes_only_device_token(
    config: NodeConfig,
) -> None:
    """``reset_pairing=token`` (the safe default) must keep the device identity."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.device_token_path.write_text("stale-token\n")
    config.key_path.write_text('{"fake":"identity"}\n')
    object.__setattr__(config, "reset_pairing", "token")

    _reset_pairing_state(config)

    assert not config.device_token_path.exists()
    assert config.key_path.exists(), "token-mode must NOT wipe the identity"


def test_reset_pairing_state_identity_mode_wipes_both(
    config: NodeConfig,
) -> None:
    """``reset_pairing=identity`` must wipe both device-token and node-key."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.device_token_path.write_text("stale-token\n")
    config.key_path.write_text('{"fake":"identity"}\n')
    object.__setattr__(config, "reset_pairing", "identity")

    _reset_pairing_state(config)

    assert not config.device_token_path.exists()
    assert not config.key_path.exists()


def test_reset_pairing_state_none_mode_is_noop(config: NodeConfig) -> None:
    """``reset_pairing=none`` must not delete anything."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.device_token_path.write_text("keep-me\n")
    config.key_path.write_text('{"keep":"me"}\n')
    object.__setattr__(config, "reset_pairing", "none")

    _reset_pairing_state(config)

    assert config.device_token_path.exists()
    assert config.key_path.exists()


def test_reset_pairing_state_is_idempotent_when_files_absent(
    config: NodeConfig,
) -> None:
    """``reset_pairing`` modes must not raise when artifacts are already missing."""
    object.__setattr__(config, "reset_pairing", "identity")
    assert not config.device_token_path.exists()
    assert not config.key_path.exists()

    _reset_pairing_state(config)

    assert not config.device_token_path.exists()
    assert not config.key_path.exists()


def test_reset_pairing_state_unknown_mode_is_skipped(config: NodeConfig) -> None:
    """An unrecognised mode value must not delete anything (fail closed)."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.device_token_path.write_text("keep\n")
    config.key_path.write_text('{"k":"v"}\n')
    object.__setattr__(config, "reset_pairing", "bogus-mode")

    _reset_pairing_state(config)

    assert config.device_token_path.exists()
    assert config.key_path.exists()


def test_reset_pairing_state_swallows_unlink_oserror(
    config: NodeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-file OSError on unlink must be logged but not raised."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.device_token_path.write_text("stale\n")
    object.__setattr__(config, "reset_pairing", "token")

    real_unlink = Path.unlink

    def _boom(self: Path, *args: object, **kwargs: object) -> None:
        if self == config.device_token_path:
            raise OSError("perm denied")
        real_unlink(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "unlink", _boom)
    _reset_pairing_state(config)
    # Did not raise — that's the assertion.


def test_reset_pairing_state_identity_logs_setup_code_warning(
    config: NodeConfig, caplog: pytest.LogCaptureFixture
) -> None:
    """Identity mode must log the actionable "generate a new setup-code" guidance."""
    import logging

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.device_token_path.write_text("t\n")
    config.key_path.write_text('{"k":"v"}\n')
    object.__setattr__(config, "reset_pairing", "identity")

    with caplog.at_level(logging.WARNING):
        _reset_pairing_state(config)
    assert any("generate a NEW setup-code" in rec.message for rec in caplog.records)


def test_reset_pairing_state_refuses_symlink_targets(config: NodeConfig, tmp_path: Path) -> None:
    """A symlinked device_token must NOT be followed and unlinked."""
    outside = tmp_path / "outside.txt"
    outside.write_text("do not delete me\n")
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.device_token_path.symlink_to(outside)
    object.__setattr__(config, "reset_pairing", "token")

    _reset_pairing_state(config)

    # Symlink itself must remain (we refused to unlink it) and the target
    # outside data_dir must still exist.
    assert config.device_token_path.is_symlink()
    assert outside.exists()


# ---------------------------------------------------------------------------
# One-shot consumed-marker tests (issue #107)
# ---------------------------------------------------------------------------


def test_reset_pairing_state_writes_consumed_marker(config: NodeConfig) -> None:
    """After a successful wipe the consumed marker must be written with the mode."""
    from openclaw_node.__main__ import _RESET_CONSUMED_FILENAME

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.device_token_path.write_text("stale-token\n")
    object.__setattr__(config, "reset_pairing", "token")

    _reset_pairing_state(config)

    marker = config.data_dir / _RESET_CONSUMED_FILENAME
    assert marker.exists(), "consumed marker must be written after wipe"
    assert marker.read_text().strip() == "token"


def test_reset_pairing_state_skips_wipe_when_marker_matches(
    config: NodeConfig, caplog: pytest.LogCaptureFixture
) -> None:
    """With a matching consumed marker, wipe must be skipped and a warning logged."""
    import logging

    from openclaw_node.__main__ import _RESET_CONSUMED_FILENAME

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.device_token_path.write_text("stale-token\n")
    (config.data_dir / _RESET_CONSUMED_FILENAME).write_text("token\n")
    object.__setattr__(config, "reset_pairing", "token")

    with caplog.at_level(logging.WARNING):
        _reset_pairing_state(config)

    assert config.device_token_path.exists(), "wipe must not run when marker matches"
    assert any("already consumed" in rec.message for rec in caplog.records)


def test_reset_pairing_state_wipes_when_marker_has_different_value(
    config: NodeConfig,
) -> None:
    """With a marker for a different mode, the wipe must run and the marker updated."""
    from openclaw_node.__main__ import _RESET_CONSUMED_FILENAME

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.device_token_path.write_text("stale-token\n")
    config.key_path.write_text('{"fake":"identity"}\n')
    marker = config.data_dir / _RESET_CONSUMED_FILENAME
    marker.write_text("token\n")  # prior run consumed "token"
    object.__setattr__(config, "reset_pairing", "identity")

    _reset_pairing_state(config)

    assert not config.device_token_path.exists(), "wipe must run for new mode value"
    assert marker.read_text().strip() == "identity"


def test_reset_pairing_state_none_clears_consumed_marker(config: NodeConfig) -> None:
    """mode=none must clear the consumed marker so a toggle-on fires a fresh wipe."""
    from openclaw_node.__main__ import _RESET_CONSUMED_FILENAME

    config.data_dir.mkdir(parents=True, exist_ok=True)
    marker = config.data_dir / _RESET_CONSUMED_FILENAME
    marker.write_text("token\n")
    object.__setattr__(config, "reset_pairing", "none")

    _reset_pairing_state(config)

    assert not marker.exists(), "consumed marker must be cleared when mode is none"


@pytest.mark.asyncio
async def test_main_runs_reset_before_identity_load(
    config: NodeConfig,
    identity: DeviceIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When reset_pairing is true, the wipe must run BEFORE load_or_generate."""
    from openclaw_node import __main__ as main_mod

    object.__setattr__(config, "reset_pairing", "token")
    order: list[str] = []

    def _fake_reset(cfg: NodeConfig) -> None:
        order.append("reset")
        assert cfg is config

    def _fake_load_or_generate(_path: Path) -> tuple[DeviceIdentity, bool]:
        order.append("identity")
        return identity, False

    async def _stub_http(_runtime: NodeRuntime) -> None:
        order.append("http")
        raise RuntimeError("stop")

    class _StubClient:
        def __init__(self, _runtime: NodeRuntime) -> None:
            self._pairing_state_callback = None

        async def run(self) -> None:
            await asyncio.sleep(0)

    def _build(
        cfg: NodeConfig, _ident: DeviceIdentity
    ) -> tuple[NodeRuntime, _StubClient, _StubClient]:
        runtime = NodeRuntime(cfg)
        return runtime, _StubClient(runtime), _StubClient(runtime)

    monkeypatch.setattr(main_mod, "load_config", lambda: config)
    monkeypatch.setattr(main_mod, "_reset_pairing_state", _fake_reset)
    monkeypatch.setattr(main_mod, "load_or_generate", _fake_load_or_generate)
    monkeypatch.setattr(main_mod, "build_runtime", _build)
    monkeypatch.setattr(main_mod, "run_http_api", _stub_http)

    with pytest.raises(ExceptionGroup):
        await main_mod._main()

    assert order.index("reset") < order.index("identity"), (
        f"reset_pairing must run before load_or_generate; got order={order}"
    )


@pytest.mark.asyncio
async def test_main_starts_both_tasks_and_propagates_failure(
    config: NodeConfig,
    identity: DeviceIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_main` must start both the WS client and the HTTP API under one TaskGroup.

    We monkeypatch the dependencies so no real port is bound, then assert that
    both fake tasks ran and that a failure in one cancels the other.
    """
    from openclaw_node import __main__ as main_mod

    started: dict[str, bool] = {"node": False, "operator": False, "http": False}
    cancelled: dict[str, bool] = {"node": False, "operator": False, "http": False}

    class _StubClient:
        def __init__(self, runtime: NodeRuntime, label: str) -> None:
            self.runtime = runtime
            self._label = label
            self._pairing_state_callback = None

        async def run(self) -> None:
            started[self._label] = True
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled[self._label] = True
                raise

    async def _stub_http(runtime: NodeRuntime) -> None:
        started["http"] = True
        assert runtime is built_runtime[0], "http task must receive the shared runtime"
        raise RuntimeError("http-task-boom")

    built_runtime: list[NodeRuntime] = []

    def _build(
        cfg: NodeConfig, ident: DeviceIdentity
    ) -> tuple[NodeRuntime, _StubClient, _StubClient]:
        runtime = NodeRuntime(cfg)
        built_runtime.append(runtime)
        return runtime, _StubClient(runtime, "node"), _StubClient(runtime, "operator")

    monkeypatch.setattr(main_mod, "load_config", lambda: config)
    monkeypatch.setattr(main_mod, "load_or_generate", lambda _p: (identity, False))
    monkeypatch.setattr(main_mod, "build_runtime", _build)
    monkeypatch.setattr(main_mod, "run_http_api", _stub_http)

    with pytest.raises(ExceptionGroup) as exc_info:
        await main_mod._main()

    assert started == {"node": True, "operator": True, "http": True}
    assert cancelled["node"] is True
    assert cancelled["operator"] is True
    flat = [e for e in exc_info.value.exceptions if isinstance(e, RuntimeError)]
    assert any("http-task-boom" in str(e) for e in flat)
