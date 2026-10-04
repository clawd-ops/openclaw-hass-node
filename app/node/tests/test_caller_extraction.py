"""WP2c-2: the reserved caller hint, resolved at the real WebSocket ingress.

Frames go through ``GatewayClient._event_loop`` into the real dispatcher and
handlers; a recording aiohttp stub stands in for Home Assistant, so "refused"
means zero HA requests. The hint is a lookup key into the relay's node-owned
registry of in-flight Assist turns, never an identity claim. These prove the
node-side wrapper path only; direct ``node.invoke`` without the hint remains
operator-default.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

from openclaw_node.authz import Actor, resolve_turn_authz
from openclaw_node.chat_relay import ChatRelay
from openclaw_node.commands import dispatcher
from openclaw_node.config import IdentityConfig, NodeConfig
from openclaw_node.gateway_ws import GatewayClient
from openclaw_node.http_api import NodeRuntime
from openclaw_node.identity import generate_identity

_SESSION = "ha-assist:conv-x"


class FakeSender:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def send(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)


async def _start_turn_in_flight(
    relay: ChatRelay, sender: FakeSender, conv_id: str, *, authz: Any = None
) -> asyncio.Task[str]:
    """Start a real relay turn and answer create/subscribe; chat.send then awaits its ack."""
    task = asyncio.create_task(relay.relay_turn(conv_id, "hi", authz=authz))
    for index in range(2):
        while len(sender.frames) <= index:
            await asyncio.sleep(0.001)
        relay.handle_response({"type": "res", "id": sender.frames[index]["id"], "ok": True})
    while len(sender.frames) < 3:
        await asyncio.sleep(0.001)
    return task


_HINT = "_openclaw_caller"


@pytest_asyncio.fixture
async def ha_requests(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[list[str]]:
    seen: list[str] = []

    async def _record(request: web.Request) -> web.Response:
        seen.append(f"{request.method} {request.path}")
        return web.json_response([])

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", _record)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.setenv("HASS_URL", str(server.make_url("")))
    monkeypatch.setenv("HASS_TOKEN", "stub-token")
    try:
        yield seen
    finally:
        await server.close()


def _config(tmp_path: Path) -> NodeConfig:
    return NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="test",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
    )


@pytest_asyncio.fixture
async def turn(
    tmp_path: Path,
) -> AsyncGenerator[tuple[GatewayClient, ChatRelay, asyncio.Task[str]]]:
    """A node client whose runtime relay has one household-user Assist turn in flight."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)
    relay._gateway_agents = ("only-agent",)
    runtime = NodeRuntime(_config(tmp_path))
    runtime.chat_relay = relay
    client = GatewayClient(
        config=_config(tmp_path),
        identity=generate_identity(),
        device_token="",
        runtime=runtime,
        chat_relay_enabled=False,
    )
    task = await _start_turn_in_flight(relay, sender, "conv-x")
    try:
        yield client, relay, task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _ingress(client: GatewayClient, command: str, params: dict[str, Any]) -> dict[str, Any]:
    ws = AsyncMock()
    ws.send = AsyncMock()
    frame = json.dumps(
        {
            "type": "event",
            "event": "node.invoke.request",
            "payload": {"id": "inv", "command": command, "paramsJSON": json.dumps(params)},
        }
    )

    async def _frames() -> AsyncIterator[str]:
        yield frame

    ws.__aiter__ = lambda self: _frames().__aiter__()
    await client._event_loop(ws, None)
    ws.send.assert_called_once()
    sent: dict[str, Any] = json.loads(ws.send.call_args.args[0])["params"]
    return sent


def _hint(session_key: str) -> dict[str, Any]:
    return {_HINT: {"sessionKey": session_key}}


async def test_active_turn_hint_applies_household_user_policy(
    turn: tuple[GatewayClient, ChatRelay, asyncio.Task[str]], ha_requests: list[str]
) -> None:
    client, _, _ = turn
    refused = await _ingress(
        client,
        "ha.call_service",
        {"domain": "notify", "service": "send_message", **_hint(_SESSION)},
    )
    assert refused["ok"] is False
    assert refused["error"]["code"] == "PERMISSION_DENIED"
    assert ha_requests == []

    allowed = await _ingress(
        client, "ha.call_service", {"domain": "light", "service": "turn_on", **_hint(_SESSION)}
    )
    assert allowed["ok"] is True
    assert ha_requests == ["POST /api/services/light/turn_on"]


async def test_hint_is_case_insensitive_and_wrapper_path_reaches_policy(
    turn: tuple[GatewayClient, ChatRelay, asyncio.Task[str]], ha_requests: list[str]
) -> None:
    client, _, _ = turn
    sent = await _ingress(
        client,
        "ha.light_turn_off",
        {"entity_id": "light.kitchen", **_hint(f"  {_SESSION.upper()} ")},
    )
    assert sent["ok"] is True
    assert ha_requests[0].startswith("POST /api/services/light/turn_off")


@pytest.mark.parametrize(
    "hint",
    [
        _hint("ha-assist:unknown"),
        _hint("agent:other:ha-assist:conv-x"),
        _hint("main"),
    ],
)
async def test_unknown_or_stale_hint_refused_with_zero_ha_requests(
    turn: tuple[GatewayClient, ChatRelay, asyncio.Task[str]],
    ha_requests: list[str],
    hint: dict[str, Any],
) -> None:
    client, _, _ = turn
    sent = await _ingress(
        client, "ha.call_service", {"domain": "light", "service": "turn_on", **hint}
    )
    assert sent["ok"] is False
    assert sent["error"]["code"] == "PERMISSION_DENIED"
    assert ha_requests == []


async def test_hint_for_finished_turn_is_refused(
    turn: tuple[GatewayClient, ChatRelay, asyncio.Task[str]], ha_requests: list[str]
) -> None:
    client, relay, task = turn
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert relay.active_caller(_SESSION) is None
    sent = await _ingress(
        client, "ha.call_service", {"domain": "light", "service": "turn_on", **_hint(_SESSION)}
    )
    assert sent["error"]["code"] == "PERMISSION_DENIED"
    assert ha_requests == []


async def test_hint_without_any_relay_is_refused(tmp_path: Path, ha_requests: list[str]) -> None:
    client = GatewayClient(config=_config(tmp_path), identity=generate_identity(), device_token="")
    sent = await _ingress(
        client, "ha.call_service", {"domain": "light", "service": "turn_on", **_hint(_SESSION)}
    )
    assert sent["error"]["code"] == "PERMISSION_DENIED"
    assert ha_requests == []


@pytest.mark.parametrize(
    "bad",
    [None, "ha-assist:conv-x", [], {}, {"sessionKey": 7}, {"sessionKey": ""}, {"sessionKey": "  "}],
)
async def test_malformed_hint_refused_invalid_params_with_zero_ha_requests(
    turn: tuple[GatewayClient, ChatRelay, asyncio.Task[str]], ha_requests: list[str], bad: Any
) -> None:
    client, _, _ = turn
    sent = await _ingress(
        client, "ha.call_service", {"domain": "light", "service": "turn_on", _HINT: bad}
    )
    assert sent["ok"] is False
    assert sent["error"]["code"] == "INVALID_PARAMS"
    assert ha_requests == []


async def test_absent_hint_is_operator_behaviour(
    turn: tuple[GatewayClient, ChatRelay, asyncio.Task[str]], ha_requests: list[str]
) -> None:
    """D3: direct node.invoke carries no hint and stays operator-default."""
    client, _, _ = turn
    sent = await _ingress(client, "ha.call_service", {"domain": "lock", "service": "unlock"})
    assert sent["ok"] is True
    assert ha_requests == ["POST /api/services/lock/unlock"]


async def test_reserved_field_never_reaches_handlers(
    turn: tuple[GatewayClient, ChatRelay, asyncio.Task[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = turn
    seen: list[dict[str, Any]] = []

    async def _spy(params: dict[str, Any]) -> dict[str, Any]:
        seen.append(dict(params))
        return {"ok": True}

    monkeypatch.setitem(dispatcher._REGISTRY, "system.which", _spy)
    await _ingress(client, "system.which", {"name": "sh", **_hint(_SESSION)})
    await _ingress(client, "system.which", {"name": "sh"})
    assert seen == [{"name": "sh"}, {"name": "sh"}]


async def test_registered_admin_turn_is_not_downgraded_to_operator_or_user(
    tmp_path: Path, ha_requests: list[str]
) -> None:
    """The resolved turn role, not the hint, decides: an admin turn is admin."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)
    relay._gateway_agents = ("only-agent",)
    runtime = NodeRuntime(_config(tmp_path))
    runtime.chat_relay = relay
    client = GatewayClient(
        config=_config(tmp_path),
        identity=generate_identity(),
        device_token="",
        runtime=runtime,
        chat_relay_enabled=False,
    )
    admin = resolve_turn_authz(IdentityConfig(), Actor("u1", is_admin=True))
    task = await _start_turn_in_flight(relay, sender, "conv-x", authz=admin)
    try:
        caller = relay.active_caller(_SESSION)
        assert caller is not None
        assert caller.role == "admin"
        denied = await _ingress(
            client,
            "ha.call_service",
            {"domain": "shell_command", "service": "x", **_hint(_SESSION)},
        )
        assert denied["ok"] is False
        assert ha_requests == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_contract_fixture_threads_active_turn_and_hint() -> None:
    """The TS contract suite's Python fixture exercises the reserved field end to end."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "invoke_fixture", Path(__file__).parent / "contracts" / "invoke_fixture.py"
    )
    assert spec is not None
    assert spec.loader is not None
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    hint = {"sessionKey": _SESSION}
    base = {"nodeId": "n", "active_turn": _SESSION}

    refused = await fixture.invoke(
        {
            **base,
            "command": "ha.call_service",
            "params": {"domain": "notify", "service": "send_message", _HINT: hint},
        }
    )
    assert refused["response"]["ok"] is False
    assert refused["ha_calls"] == []

    allowed = await fixture.invoke(
        {
            **base,
            "command": "ha.light_turn_on",
            "params": {"entity_id": "light.test", _HINT: hint},
        }
    )
    assert allowed["response"]["ok"] is True
    assert len(allowed["ha_calls"]) == 1

    stale = await fixture.invoke(
        {
            "nodeId": "n",
            "command": "ha.light_turn_on",
            "params": {"entity_id": "light.test", _HINT: hint},
        }
    )
    assert stale["response"]["ok"] is False
    assert stale["ha_calls"] == []
