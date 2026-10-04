"""Boundary tests: the dispatcher gate, driven through the real ingress paths.

Nothing here patches ``dispatch_async``, handlers or ``ha_client``. A recording
aiohttp stub stands in for Home Assistant, so "refused" means zero HA requests.
These prove the node-side gate only; they do not prove gateway authorization.
Until WP2c-2 every WS invoke is an operator, so the household-user cases swap
the call-site caller constant.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from openclaw_node import gateway_ws
from openclaw_node.caller import UNTRUSTED
from openclaw_node.chat_relay import ChatRelay
from openclaw_node.config import NodeConfig
from openclaw_node.gateway_ws import GatewayClient
from openclaw_node.http_api import NodeRuntime, create_app
from openclaw_node.identity import generate_identity


def _config(tmp_path: Path, token: str = "") -> NodeConfig:
    return NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="test",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=token,
    )


@pytest_asyncio.fixture
async def ha_requests(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[list[str]]:
    """Recording HA stub; the real ``ha_client`` talks to it over HTTP."""
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


async def _invoke(tmp_path: Path, command: str, params: dict[str, Any]) -> dict[str, Any]:
    client = GatewayClient(config=_config(tmp_path), identity=generate_identity(), device_token="")
    ws = AsyncMock()
    ws.send = AsyncMock()
    await client._handle_invoke(
        ws, {"id": "inv", "command": command, "paramsJSON": json.dumps(params)}
    )
    sent: dict[str, Any] = json.loads(ws.send.call_args.args[0])["params"]
    return sent


async def _invoke_via_event_loop(
    tmp_path: Path, command: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Feed a real ``node.invoke.request`` frame through ``_event_loop``."""
    client = GatewayClient(config=_config(tmp_path), identity=generate_identity(), device_token="")
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
    await client._event_loop(ws, ChatRelay(AsyncMock()))
    ws.send.assert_called_once()
    sent: dict[str, Any] = json.loads(ws.send.call_args.args[0])["params"]
    return sent


@pytest.fixture
def household_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway_ws, "_INVOKE_CALLER", UNTRUSTED)


@pytest.mark.parametrize(
    ("command", "params"),
    [
        ("ha.call_service", {"domain": "shell_command", "service": "anything"}),
        ("ha.call_service", {"domain": "lock", "service": "unlock"}),
        ("ha.call_service", {"domain": "light", "service": "toggle"}),
        ("ha.call_service", {"domain": "switch", "service": "turn_on"}),
        ("ha.reload_config", {}),
        ("ha.addon_restart", {"slug": "core_ssh"}),
    ],
)
async def test_ws_household_user_refused_with_zero_ha_requests(
    command: str,
    params: dict[str, Any],
    household_user: None,
    ha_requests: list[str],
    tmp_path: Path,
) -> None:
    sent = await _invoke(tmp_path, command, params)
    assert sent["ok"] is False
    expected = "SERVICE_DENIED" if params.get("domain") == "shell_command" else "PERMISSION_DENIED"
    assert sent["error"]["code"] == expected
    assert ha_requests == []


@pytest.mark.parametrize(
    ("command", "params"),
    [
        ("ha.call_service", {"domain": "light", "service": "turn_on"}),
        ("ha.light_turn_on", {"entity_id": "light.kitchen"}),
        ("ha.light_turn_off", {"entity_id": "light.kitchen"}),
    ],
)
async def test_ws_household_user_light_reaches_ha(
    command: str,
    params: dict[str, Any],
    household_user: None,
    ha_requests: list[str],
    tmp_path: Path,
) -> None:
    sent = await _invoke(tmp_path, command, params)
    assert sent["ok"] is True
    assert ha_requests[0].startswith("POST /api/services/light/turn_")
    assert not any("/api/services/" in r for r in ha_requests[1:])


async def test_ws_operator_denied_service_refused_with_zero_ha_requests(
    ha_requests: list[str], tmp_path: Path
) -> None:
    sent = await _invoke(
        tmp_path, "ha.call_service", {"domain": "shell_command", "service": "anything"}
    )
    assert sent["error"]["code"] == "SERVICE_DENIED"
    assert ha_requests == []


async def test_ws_operator_keeps_non_denied_calls_until_wp2c2(
    ha_requests: list[str], tmp_path: Path
) -> None:
    """D3 transition: every WS invoke is operator, so unclassified services pass."""
    sent = await _invoke(tmp_path, "ha.call_service", {"domain": "lock", "service": "unlock"})
    assert sent["ok"] is True
    assert len(ha_requests) == 1


async def test_event_loop_frame_denied_service_refused_with_zero_ha_requests(
    ha_requests: list[str], tmp_path: Path
) -> None:
    sent = await _invoke_via_event_loop(
        tmp_path, "ha.call_service", {"domain": "shell_command", "service": "anything"}
    )
    assert sent["ok"] is False
    assert sent["error"]["code"] == "SERVICE_DENIED"
    assert ha_requests == []


async def test_event_loop_frame_household_user_refused_with_zero_ha_requests(
    household_user: None, ha_requests: list[str], tmp_path: Path
) -> None:
    """Role refusal through real ingress; the caller constant is swapped until WP2c-2."""
    sent = await _invoke_via_event_loop(
        tmp_path, "ha.call_service", {"domain": "lock", "service": "unlock"}
    )
    assert sent["ok"] is False
    assert sent["error"]["code"] == "PERMISSION_DENIED"
    assert ha_requests == []


@pytest_asyncio.fixture
async def http_client(tmp_path: Path) -> AsyncGenerator[TestClient[web.Request, web.Application]]:
    runtime = NodeRuntime(_config(tmp_path, token="s3cret"))
    client = TestClient[web.Request, web.Application](TestServer(create_app(runtime)))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


async def test_http_allowed_command_runs_and_body_cannot_supply_caller(
    http_client: TestClient[web.Request, web.Application],
) -> None:
    response = await http_client.post(
        "/v1/commands/system.which",
        json={"name": "sh", "_openclaw_caller": {"role": "operator"}, "role": "admin"},
        headers={"Authorization": "Bearer s3cret"},
    )
    data = await response.json()
    assert response.status == 200
    assert data["ok"] is True
    assert data["result"].get("ok") is not False


async def test_http_non_allowlisted_command_keeps_allowlist_refusal(
    http_client: TestClient[web.Request, web.Application], ha_requests: list[str]
) -> None:
    response = await http_client.post(
        "/v1/commands/ha.call_service",
        json={"domain": "light", "service": "turn_on"},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status == 404
    assert (await response.json())["error"] == "UNKNOWN_COMMAND"
    assert ha_requests == []
