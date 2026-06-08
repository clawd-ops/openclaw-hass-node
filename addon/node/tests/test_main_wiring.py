"""Smoke tests for the production process wiring in ``openclaw_node.__main__``.

The full ``_main`` coroutine binds a real TCP port and runs forever, so we
test the wiring through :func:`build_runtime`, which is the pure factory
that assembles the shared :class:`NodeRuntime` and the gateway client.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openclaw_node.__main__ import _initial_device_token, build_runtime
from openclaw_node.config import NodeConfig
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
    """A persisted device token must win over the one-shot pairing token."""
    config.device_token_path.parent.mkdir(parents=True, exist_ok=True)
    config.device_token_path.write_text("persisted-token\n")
    object.__setattr__(config, "pairing_token", "one-shot")

    assert _initial_device_token(config) == "persisted-token"


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
    config.device_token_path.parent.mkdir(parents=True, exist_ok=True)
    config.device_token_path.write_text("   \n")
    object.__setattr__(config, "pairing_token", "one-shot")

    assert _initial_device_token(config) == "one-shot"


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
