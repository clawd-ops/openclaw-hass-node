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
from openclaw_node.authz import Actor, resolve_turn_authz
from openclaw_node.caller import UNTRUSTED, Caller
from openclaw_node.chat_relay import ChatRelay
from openclaw_node.config import IdentityConfig, NodeConfig
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
        ("ha.call_service", {"domain": "automation", "service": "trigger"}),
        ("ha.call_service", {"domain": "script", "service": "reload"}),
        ("ha.call_service", {"domain": "script", "service": "turn_off"}),
        ("ha.call_service", {"domain": "homeassistant", "service": "turn_off"}),
        ("ha.reload_config", {}),
        ("ha.addon_restart", {"slug": "core_ssh"}),
        ("ha.addon_update", {"slug": "core_ssh"}),
        ("system.execApprovals.set", {"file": {"version": 1, "agents": {}}}),
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
    denied = {"shell_command", "script"}
    expected = (
        "SERVICE_DENIED"
        if params.get("domain") in denied and params.get("service") != "turn_off"
        else "PERMISSION_DENIED"
    )
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
        tmp_path, "ha.call_service", {"domain": "automation", "service": "trigger"}
    )
    assert sent["ok"] is False
    assert sent["error"]["code"] == "PERMISSION_DENIED"
    assert ha_requests == []


class _Stub:
    """Recording HA stub with a scripted reply, bodies included."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.status = 200
        self.reply: Any = []
        self.get_reply: Any = None  # when set, GETs (state snapshots) answer with this


@pytest_asyncio.fixture
async def ha_stub(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[_Stub]:
    stub = _Stub()

    async def _record(request: web.Request) -> web.Response:
        body = await request.json() if request.can_read_body else None
        stub.calls.append((request.path, body))
        reply = (
            stub.get_reply if request.method == "GET" and stub.get_reply is not None else stub.reply
        )
        return web.json_response(reply, status=stub.status)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", _record)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.setenv("HASS_TOKEN", "stub-token")
    monkeypatch.setenv("HASS_URL", str(server.make_url("")))
    try:
        yield stub
    finally:
        await server.close()


def _as_role(monkeypatch: pytest.MonkeyPatch, is_admin: bool) -> None:
    turn = resolve_turn_authz(IdentityConfig(), Actor("u", is_admin=is_admin))
    monkeypatch.setattr(gateway_ws, "_INVOKE_CALLER", Caller.from_turn(turn))


_EVERYDAY: list[tuple[str, str]] = [
    ("light", "turn_on"),
    ("media_player", "turn_on"),
    ("switch", "turn_on"),
    ("scene", "turn_on"),
    ("script", "turn_on"),
    ("button", "press"),
    ("cover", "open_cover"),
    ("lock", "unlock"),
    ("alarm_control_panel", "alarm_disarm"),
]


@pytest.mark.parametrize("is_admin", [False, True])
@pytest.mark.parametrize(("domain", "service"), _EVERYDAY)
async def test_ws_everyday_control_reaches_ha_for_user_and_admin(
    domain: str,
    service: str,
    is_admin: bool,
    monkeypatch: pytest.MonkeyPatch,
    ha_stub: _Stub,
    tmp_path: Path,
) -> None:
    _as_role(monkeypatch, is_admin)
    params = {"domain": domain, "service": service, "target": {"entity_id": f"{domain}.x"}}

    sent = await _invoke(tmp_path, "ha.call_service", params)

    assert sent["ok"] is True
    assert ha_stub.calls[0] == (f"/api/services/{domain}/{service}", {"entity_id": f"{domain}.x"})


async def test_ws_user_non_allowlisted_service_makes_zero_ha_requests(
    monkeypatch: pytest.MonkeyPatch, ha_stub: _Stub, tmp_path: Path
) -> None:
    _as_role(monkeypatch, False)

    sent = await _invoke(
        tmp_path, "ha.call_service", {"domain": "automation", "service": "trigger"}
    )

    assert sent["error"]["code"] == "PERMISSION_DENIED"
    assert ha_stub.calls == []


async def test_ws_lock_code_reaches_ha_but_not_logs_or_result(
    monkeypatch: pytest.MonkeyPatch,
    ha_stub: _Stub,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    _as_role(monkeypatch, False)
    params = {
        "domain": "lock",
        "service": "unlock",
        "target": {"entity_id": "lock.front"},
        "data": {"code": "482913"},
    }

    with caplog.at_level("DEBUG"):
        sent = await _invoke(tmp_path, "ha.call_service", params)

    assert sent["ok"] is True
    assert ha_stub.calls[0][1] == {"entity_id": "lock.front", "code": "482913"}
    assert "482913" not in caplog.text
    assert "482913" not in json.dumps(sent)


async def test_ws_ha_code_rejection_is_readable_and_code_free(
    monkeypatch: pytest.MonkeyPatch,
    ha_stub: _Stub,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    _as_role(monkeypatch, True)
    ha_stub.status = 400
    ha_stub.reply = {"message": "Invalid code 482913 provided for lock.front"}
    params = {
        "domain": "lock",
        "service": "unlock",
        "target": {"entity_id": "lock.front"},
        "data": {"code": "482913"},
    }

    with caplog.at_level("DEBUG"):
        sent = await _invoke(tmp_path, "ha.call_service", params)

    assert sent["ok"] is False
    message = sent["error"]["message"]
    assert "Invalid code" in message
    assert "482913" not in message
    assert "482913" not in caplog.text


async def test_ws_missing_code_rejection_is_readable(
    monkeypatch: pytest.MonkeyPatch, ha_stub: _Stub, tmp_path: Path
) -> None:
    _as_role(monkeypatch, False)
    ha_stub.status = 400
    ha_stub.reply = {"message": "Invalid code for lock.front"}

    sent = await _invoke(
        tmp_path,
        "ha.call_service",
        {"domain": "lock", "service": "unlock", "target": {"entity_id": "lock.front"}},
    )

    assert sent["ok"] is False
    assert "Invalid code" in sent["error"]["message"]
    assert "code" not in ha_stub.calls[0][1]


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
        json={"name": "sh"},
        headers={"Authorization": "Bearer s3cret"},
    )
    data = await response.json()
    assert response.status == 200
    assert data["ok"] is True
    assert data["result"].get("ok") is not False

    # Caller claims in the body are unknown parameters, refused rather than honoured.
    response = await http_client.post(
        "/v1/commands/system.which",
        json={"name": "sh", "_openclaw_caller": {"role": "operator"}, "role": "admin"},
        headers={"Authorization": "Bearer s3cret"},
    )
    refused = (await response.json())["result"]
    assert refused["error"] == "INVALID_PARAM"
    assert "_openclaw_caller" in refused["message"]


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


@pytest.mark.parametrize(
    ("domain", "service"),
    [("input_select", "set_options"), ("input_select", "reload"), ("light", "brand_new_service")],
)
async def test_ws_user_unlisted_service_in_allowlisted_domain_makes_zero_ha_requests(
    domain: str, service: str, monkeypatch: pytest.MonkeyPatch, ha_stub: _Stub, tmp_path: Path
) -> None:
    _as_role(monkeypatch, False)

    sent = await _invoke(
        tmp_path,
        "ha.call_service",
        {"domain": domain, "service": service, "target": {"entity_id": f"{domain}.x"}},
    )

    assert sent["ok"] is False
    assert ha_stub.calls == []


_ECHO_STATE = {
    "entity_id": "lock.front",
    "state": "unlocked",
    "attributes": {"note": "last code x482913x", "codes": ["482913", {"deep": "pin=482913"}]},
}


async def test_ws_code_echoed_in_changed_states_is_redacted(
    monkeypatch: pytest.MonkeyPatch, ha_stub: _Stub, tmp_path: Path
) -> None:
    _as_role(monkeypatch, False)
    ha_stub.reply = [_ECHO_STATE]

    sent = await _invoke(
        tmp_path,
        "ha.call_service",
        {
            "domain": "lock",
            "service": "unlock",
            "target": {"entity_id": "lock.front"},
            "data": {"code": "482913"},
        },
    )

    assert sent["ok"] is True
    assert "482913" not in json.dumps(sent)
    assert "unlocked" in json.dumps(sent)


async def test_ws_code_echoed_in_fetched_snapshot_is_redacted(
    monkeypatch: pytest.MonkeyPatch, ha_stub: _Stub, tmp_path: Path
) -> None:
    _as_role(monkeypatch, True)
    ha_stub.reply = []
    ha_stub.get_reply = _ECHO_STATE

    sent = await _invoke(
        tmp_path,
        "ha.call_service",
        {
            "domain": "lock",
            "service": "unlock",
            "target": {"entity_id": "lock.front"},
            "data": {"code": "482913"},
        },
    )

    assert sent["ok"] is True
    assert "unlocked" in json.dumps(sent)
    assert "482913" not in json.dumps(sent)


async def test_ws_embedded_code_in_error_is_redacted(
    monkeypatch: pytest.MonkeyPatch, ha_stub: _Stub, tmp_path: Path
) -> None:
    _as_role(monkeypatch, False)
    ha_stub.status = 400
    ha_stub.reply = {"message": "Invalid code x482913x for lock.front"}

    sent = await _invoke(
        tmp_path,
        "ha.call_service",
        {
            "domain": "lock",
            "service": "unlock",
            "target": {"entity_id": "lock.front"},
            "data": {"code": "482913"},
        },
    )

    assert "Invalid code" in sent["error"]["message"]
    assert "482913" not in json.dumps(sent)


_CODE_TEXT_ECHO_STATE = {
    "entity_id": "lock.front",
    "state": "unlocked",
    "attributes": {
        "last_code": "482913",
        "pin": 482913,
        "codes": ["482913", {"deep": "pin 482913"}],
        "battery": 87,
    },
}


async def test_ws_numeric_code_is_sent_as_text_and_masked_in_changed_states(
    monkeypatch: pytest.MonkeyPatch, ha_stub: _Stub, tmp_path: Path
) -> None:
    _as_role(monkeypatch, False)
    ha_stub.reply = [_CODE_TEXT_ECHO_STATE]

    sent = await _invoke(
        tmp_path,
        "ha.call_service",
        {
            "domain": "lock",
            "service": "unlock",
            "target": {"entity_id": "lock.front"},
            "data": {"code": 482913},
        },
    )

    assert sent["ok"] is True
    assert ha_stub.calls[-1][1] == {"code": "482913", "entity_id": "lock.front"}
    assert "482913" not in json.dumps(sent)
    assert "87" in json.dumps(sent)
    assert sent["payload"]["changed_states"][0]["attributes"]["pin"] == "[redacted]"


async def test_ws_numeric_code_is_sent_as_text_and_masked_in_fetched_snapshot(
    monkeypatch: pytest.MonkeyPatch, ha_stub: _Stub, tmp_path: Path
) -> None:
    _as_role(monkeypatch, True)
    ha_stub.reply = []
    ha_stub.get_reply = _CODE_TEXT_ECHO_STATE

    sent = await _invoke(
        tmp_path,
        "ha.call_service",
        {
            "domain": "lock",
            "service": "unlock",
            "target": {"entity_id": "lock.front"},
            "data": {"code": 482913},
        },
    )

    assert sent["ok"] is True
    assert "unlocked" in json.dumps(sent)
    assert "482913" not in json.dumps(sent)
    assert sent["payload"]["changed_states"][0]["attributes"]["pin"] == "[redacted]"


@pytest.mark.parametrize(
    "data",
    [
        {"variables": {"code": "482913"}},
        {"variables": {"steps": [{"code": "482913"}]}},
        {"items": [{"nested": {"code": "482913"}}]},
    ],
)
async def test_ws_nested_code_reaches_ha_but_not_logs_or_result(
    monkeypatch: pytest.MonkeyPatch,
    ha_stub: _Stub,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    data: dict[str, Any],
) -> None:
    _as_role(monkeypatch, False)
    ha_stub.reply = [_ECHO_STATE]
    ha_stub.get_reply = _ECHO_STATE
    params = {
        "domain": "script",
        "service": "turn_on",
        "target": {"entity_id": "script.front"},
        "data": data,
    }

    with caplog.at_level("DEBUG"):
        sent = await _invoke(tmp_path, "ha.call_service", params)

    assert sent["ok"] is True
    assert ha_stub.calls[0][1] == {"entity_id": "script.front", **data}
    assert "482913" not in caplog.text
    assert "482913" not in json.dumps(sent)
    assert "unlocked" in json.dumps(sent)


async def test_ws_unknown_key_equal_to_code_is_refused_without_echo(
    monkeypatch: pytest.MonkeyPatch,
    ha_stub: _Stub,
    tmp_path: Path,
) -> None:
    _as_role(monkeypatch, False)
    for params in (
        {
            "domain": "lock",
            "service": "unlock",
            "target": {"482913": 1},
            "data": {"code": "482913"},
        },
        {"domain": "lock", "service": "unlock", "data": {"code": "482913"}, "482913": 1},
    ):
        sent = await _invoke(tmp_path, "ha.call_service", params)
        assert "482913" not in json.dumps(sent)
    assert ha_stub.calls == []


async def test_ws_admin_approval_refusal_never_echoes_a_supplied_code(
    monkeypatch: pytest.MonkeyPatch,
    ha_stub: _Stub,
    tmp_path: Path,
) -> None:
    _as_role(monkeypatch, True)
    params = {"domain": "automation", "service": "trigger", "data": {"code": "automation"}}
    sent = await _invoke(tmp_path, "ha.call_service", params)
    assert sent["error"]["code"] == "APPROVAL_REQUIRED"
    assert "automation" not in sent["error"]["message"]
    assert ha_stub.calls == []
