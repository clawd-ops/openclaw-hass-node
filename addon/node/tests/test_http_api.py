"""Tests for the node local HTTP API."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from aiohttp.web import Application, Request

from openclaw_node.authz import Actor, derive_actor_signing_secret, sign_actor
from openclaw_node.config import NodeConfig
from openclaw_node.http_api import NodeRuntime, aiohttp_timeout, create_app
from openclaw_node.pairing import PairingState

_TEST_TOKEN = "test-token-shhh"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path,
) -> AsyncGenerator[TestClient[Request, Application]]:
    """Return a test client for the local API authed with ``_TEST_TOKEN``.

    The fail-closed bearer middleware (P5.13 follow-up #70) means
    non-public paths require a token. We bind it to ``_TEST_TOKEN`` and
    use a ClientSession with a default ``Authorization`` header so each
    test exercises the real auth path without per-test boilerplate.
    """
    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gateway.example/ws",
        pairing_token="",
        node_name="test-node",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=_TEST_TOKEN,
    )
    app = create_app(NodeRuntime(config))
    server = TestServer(app)
    client = TestClient[Request, Application](
        server,
        headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
    )
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_health(client: TestClient[Request, Application]) -> None:
    """Health endpoint returns safe runtime data."""
    response = await client.get("/health")
    data = await response.json()

    assert response.status == 200
    assert data["ok"] is True
    assert data["config"]["hass_token"] is False
    assert data["config"]["pairing_token"] is False
    local_api_token = data["config"]["local_api_token"]
    assert local_api_token != _TEST_TOKEN
    assert local_api_token is True


@pytest.mark.asyncio
async def test_ping_endpoint(client: TestClient[Request, Application]) -> None:
    """Ping endpoint dispatches to the command registry."""
    response = await client.post("/commands/ping", json={"message": "hi"})
    data = await response.json()

    assert response.status == 200
    assert data["pong"] is True
    assert data["message"] == "hi"


@pytest.mark.asyncio
async def test_unknown_command_endpoint(client: TestClient[Request, Application]) -> None:
    """Unknown local commands return 404 with structured error."""
    response = await client.post("/v1/commands/nope", json={})
    data = await response.json()

    assert response.status == 404
    assert data == {"ok": False, "error": "UNKNOWN_COMMAND", "command": "nope"}


@pytest.mark.asyncio
async def test_ha_snapshot_missing_token(client: TestClient[Request, Application]) -> None:
    """HA snapshot reports missing credentials clearly."""
    response = await client.get("/ha/snapshot")
    data = await response.json()

    assert response.status == 503
    assert data["ok"] is False
    assert data["error"] == "HA_TOKEN_OR_URL_MISSING"


@pytest.mark.asyncio
async def test_assist_turn_unpaired(client: TestClient[Request, Application]) -> None:
    """Assist placeholder is clear before gateway pairing."""
    response = await client.post("/v1/conversation", json={"text": "hello"})
    data = await response.json()

    assert response.status == 200
    assert data["paired"] is False
    assert "not paired" in data["response"]
    assert data["echo"] == "hello"


@pytest.mark.asyncio
async def test_assist_turn_operator_not_connected(tmp_path: Path) -> None:
    """Assist returns a specific diagnostic when only the node connection
    is up — Assist requires the operator-role connection."""
    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=_TEST_TOKEN,
    )
    runtime = NodeRuntime(config)
    runtime.pairing_state = PairingState.PAIRED
    runtime.node_connected = True
    runtime.operator_connected = False  # node is up but operator isn't
    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](
        server, headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
    )
    await tc.start_server()
    try:
        response = await tc.post(
            "/v1/conversation",
            json={"text": "hello", "conversation_id": "conv-1"},
        )
        data = await response.json()
        assert data["ok"] is False
        assert "operator" in data["response"].lower()
    finally:
        await tc.close()


@pytest.mark.asyncio
async def test_assist_turn_no_relay(tmp_path: Path) -> None:
    """Assist returns diagnostic when paired but no chat relay."""
    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=_TEST_TOKEN,
    )
    runtime = NodeRuntime(config)
    runtime.pairing_state = PairingState.PAIRED
    runtime.node_connected = True
    runtime.operator_connected = True
    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](
        server, headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
    )
    await tc.start_server()
    try:
        response = await tc.post(
            "/v1/conversation",
            json={"text": "hello", "conversation_id": "conv-1"},
        )
        data = await response.json()
        assert data["paired"] is True
        assert data["ok"] is False
        assert "relay" in data["response"].lower()
    finally:
        await tc.close()


@pytest.mark.asyncio
async def test_assist_turn_missing_conversation_id(tmp_path: Path) -> None:
    """Assist returns error when conversation_id is missing."""
    from openclaw_node.chat_relay import ChatRelay

    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=_TEST_TOKEN,
    )
    runtime = NodeRuntime(config)
    runtime.pairing_state = PairingState.PAIRED
    runtime.node_connected = True
    runtime.operator_connected = True
    runtime.chat_relay = ChatRelay(AsyncMock())
    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](
        server, headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
    )
    await tc.start_server()
    try:
        response = await tc.post("/v1/conversation", json={"text": "hello"})
        data = await response.json()
        assert data["ok"] is False
        assert "conversation_id" in data["response"]
    finally:
        await tc.close()


@pytest.mark.asyncio
async def test_assist_turn_relay_success(tmp_path: Path) -> None:
    """Assist relays turn through ChatRelay and returns reply."""
    from openclaw_node.chat_relay import ChatRelay

    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=_TEST_TOKEN,
    )
    runtime = NodeRuntime(config)
    runtime.pairing_state = PairingState.PAIRED
    runtime.node_connected = True
    runtime.operator_connected = True

    mock_relay = MagicMock(spec=ChatRelay)
    mock_relay.relay_turn = AsyncMock(return_value="Lights turned on!")
    runtime.chat_relay = mock_relay

    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](
        server, headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
    )
    await tc.start_server()
    try:
        response = await tc.post(
            "/v1/conversation",
            json={"text": "turn on the lights", "conversation_id": "conv-42"},
        )
        data = await response.json()
        assert response.status == 200
        assert data["ok"] is True
        assert data["response"] == "Lights turned on!"
        assert data["echo"] == "turn on the lights"
        mock_relay.relay_turn.assert_awaited_once()
        args = mock_relay.relay_turn.await_args.args
        kwargs = mock_relay.relay_turn.await_args.kwargs
        assert args == ("conv-42", "turn on the lights", "en")
        assert kwargs["authz"].role == "user"
    finally:
        await tc.close()


@pytest.mark.asyncio
async def test_assist_turn_resolves_actor_for_relay(tmp_path: Path) -> None:
    """Forwarded HA actor controls the resolved authz policy."""
    from openclaw_node.chat_relay import ChatRelay
    from openclaw_node.config import IdentityConfig

    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=_TEST_TOKEN,
        identity=IdentityConfig(
            super_admins=frozenset({"rob-uuid"}),
            user_agent_map={"rob-uuid": "clawd"},
            default_agent_id="clawd-household",
        ),
    )
    runtime = NodeRuntime(config)
    runtime.pairing_state = PairingState.PAIRED
    runtime.node_connected = True
    runtime.operator_connected = True

    mock_relay = MagicMock(spec=ChatRelay)
    mock_relay.relay_turn = AsyncMock(return_value="Done")
    runtime.chat_relay = mock_relay

    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](
        server, headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
    )
    await tc.start_server()
    try:
        actor_ts = int(time.time())
        response = await tc.post(
            "/v1/conversation",
            json={
                "text": "restart addon",
                "conversation_id": "conv-actor",
                "actor": {"user_id": "rob-uuid", "is_admin": True},
                "actor_ts": actor_ts,
                "actor_signature": sign_actor(
                    derive_actor_signing_secret(_TEST_TOKEN),
                    actor=Actor("rob-uuid", is_admin=True),
                    text="restart addon",
                    conversation_id="conv-actor",
                    language="en",
                    ts=actor_ts,
                ),
            },
        )
        assert response.status == 200
        authz = mock_relay.relay_turn.await_args.kwargs["authz"]
        assert authz.role == "super_admin"
        assert authz.agent_id == "clawd"
    finally:
        await tc.close()


@pytest.mark.asyncio
async def test_assist_turn_ignores_unsigned_actor(tmp_path: Path) -> None:
    """Unsigned actor metadata cannot elevate role or select mapped agents."""
    from openclaw_node.chat_relay import ChatRelay
    from openclaw_node.config import IdentityConfig

    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=_TEST_TOKEN,
        identity=IdentityConfig(
            super_admins=frozenset({"rob-uuid"}),
            user_agent_map={"rob-uuid": "clawd"},
            default_agent_id="clawd-household",
        ),
    )
    runtime = NodeRuntime(config)
    runtime.pairing_state = PairingState.PAIRED
    runtime.node_connected = True
    runtime.operator_connected = True

    mock_relay = MagicMock(spec=ChatRelay)
    mock_relay.relay_turn = AsyncMock(return_value="Done")
    runtime.chat_relay = mock_relay

    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](
        server, headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
    )
    await tc.start_server()
    try:
        response = await tc.post(
            "/v1/conversation",
            json={
                "text": "restart addon",
                "conversation_id": "conv-actor",
                "actor": {"user_id": "rob-uuid", "is_admin": True},
            },
        )
        assert response.status == 200
        authz = mock_relay.relay_turn.await_args.kwargs["authz"]
        assert authz.role == "user"
        assert authz.agent_id == "clawd-household"
    finally:
        await tc.close()


@pytest.mark.asyncio
async def test_assist_turn_relay_error(tmp_path: Path) -> None:
    """Assist returns 502 on ChatRelayError."""
    from openclaw_node.chat_relay import ChatRelay, ChatRelayError

    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=_TEST_TOKEN,
    )
    runtime = NodeRuntime(config)
    runtime.pairing_state = PairingState.PAIRED
    runtime.node_connected = True
    runtime.operator_connected = True

    mock_relay = MagicMock(spec=ChatRelay)
    mock_relay.relay_turn = AsyncMock(side_effect=ChatRelayError("TIMEOUT", "chat.send timed out"))
    runtime.chat_relay = mock_relay

    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](
        server, headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
    )
    await tc.start_server()
    try:
        response = await tc.post(
            "/v1/conversation",
            json={"text": "hello", "conversation_id": "conv-err"},
        )
        data = await response.json()
        assert response.status == 502
        assert data["ok"] is False
        assert data["error"] == "TIMEOUT"
    finally:
        await tc.close()


@pytest.mark.asyncio
async def test_assist_turn_stream_yields_ndjson_deltas(tmp_path: Path) -> None:
    """/v1/conversation/stream returns NDJSON with one delta per line + done."""
    from openclaw_node.chat_relay import ChatRelay

    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=_TEST_TOKEN,
    )
    runtime = NodeRuntime(config)
    runtime.pairing_state = PairingState.PAIRED
    runtime.node_connected = True
    runtime.operator_connected = True

    async def _fake_stream(*_args: Any, **_kwargs: Any) -> Any:
        for chunk in ["Hello", ", ", "world!"]:
            yield chunk

    mock_relay = MagicMock(spec=ChatRelay)
    mock_relay.stream_turn = _fake_stream
    runtime.chat_relay = mock_relay

    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](
        server, headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
    )
    await tc.start_server()
    try:
        response = await tc.post(
            "/v1/conversation/stream",
            json={"text": "hi", "conversation_id": "conv-stream"},
        )
        assert response.status == 200
        assert response.headers["Content-Type"] == "application/x-ndjson"
        body = await response.text()
    finally:
        await tc.close()

    lines = [json.loads(line) for line in body.strip().split("\n") if line]
    assert lines == [
        {"delta": "Hello"},
        {"delta": ", "},
        {"delta": "world!"},
        {"done": True},
    ]


@pytest.mark.asyncio
async def test_assist_turn_stream_emits_keepalive_frames(tmp_path: Path) -> None:
    """A StreamKeepalive sentinel yielded by the relay is translated to a
    `{"keepalive": true}` NDJSON frame on the wire — separate from
    `{"delta": ...}` so the HA shim can skip it when accumulating the
    assistant message text.
    """
    from openclaw_node.chat_relay import ChatRelay, StreamKeepalive

    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=_TEST_TOKEN,
    )
    runtime = NodeRuntime(config)
    runtime.pairing_state = PairingState.PAIRED
    runtime.node_connected = True
    runtime.operator_connected = True

    async def _fake_stream(*_args: Any, **_kwargs: Any) -> Any:
        yield StreamKeepalive()
        yield "Hello"
        yield StreamKeepalive()
        yield " world!"

    mock_relay = MagicMock(spec=ChatRelay)
    mock_relay.stream_turn = _fake_stream
    runtime.chat_relay = mock_relay

    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](
        server, headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
    )
    await tc.start_server()
    try:
        response = await tc.post(
            "/v1/conversation/stream",
            json={"text": "long question", "conversation_id": "conv-keepalive"},
        )
        assert response.status == 200
        body = await response.text()
    finally:
        await tc.close()

    lines = [json.loads(line) for line in body.strip().split("\n") if line]
    assert lines == [
        {"keepalive": True},
        {"delta": "Hello"},
        {"keepalive": True},
        {"delta": " world!"},
        {"done": True},
    ]


@pytest.mark.asyncio
async def test_assist_turn_stream_error_frame_on_relay_error(tmp_path: Path) -> None:
    """A ChatRelayError mid-stream surfaces as an `{"error": "<code>"}` frame."""
    from openclaw_node.chat_relay import ChatRelay, ChatRelayError

    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=_TEST_TOKEN,
    )
    runtime = NodeRuntime(config)
    runtime.pairing_state = PairingState.PAIRED
    runtime.node_connected = True
    runtime.operator_connected = True

    async def _fake_stream(*_args: Any, **_kwargs: Any) -> Any:
        yield "partial"
        raise ChatRelayError("TIMEOUT", "chat.send timed out")

    mock_relay = MagicMock(spec=ChatRelay)
    mock_relay.stream_turn = _fake_stream
    runtime.chat_relay = mock_relay

    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](
        server, headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
    )
    await tc.start_server()
    try:
        response = await tc.post(
            "/v1/conversation/stream",
            json={"text": "hi", "conversation_id": "conv-err"},
        )
        body = await response.text()
    finally:
        await tc.close()

    lines = [json.loads(line) for line in body.strip().split("\n") if line]
    assert lines == [{"delta": "partial"}, {"error": "TIMEOUT"}]


@pytest.mark.asyncio
async def test_assist_turn_stream_unpaired_emits_error_frame(tmp_path: Path) -> None:
    """Stream endpoint returns NDJSON error frame when node is not paired."""
    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=_TEST_TOKEN,
    )
    runtime = NodeRuntime(config)  # not paired
    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](
        server, headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
    )
    await tc.start_server()
    try:
        response = await tc.post(
            "/v1/conversation/stream",
            json={"text": "hi", "conversation_id": "c"},
        )
        body = await response.text()
        assert response.status == 502
    finally:
        await tc.close()

    lines = [json.loads(line) for line in body.strip().split("\n") if line]
    assert lines == [{"error": "NOT_PAIRED"}]


@pytest.mark.asyncio
async def test_conversation_info_advertises_streaming(
    client: TestClient[Request, Application],
) -> None:
    """conversation_info advertises supports_streaming + streaming_endpoint."""
    response = await client.get("/v1/conversation/info")
    data = await response.json()
    assert data["supports_streaming"] is True
    assert data["streaming_endpoint"] == "/v1/conversation/stream"


@pytest.mark.asyncio
async def test_conversation_info_endpoint(client: TestClient[Request, Application]) -> None:
    """/v1/conversation/info returns runtime metadata."""
    response = await client.get("/v1/conversation/info")
    data = await response.json()
    assert response.status == 200
    assert data["ok"] is True
    assert data["paired"] is False
    assert data["gateway_connected"] is False
    assert "version" in data
    assert data["pairing_state"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_command_dispatch_success(
    client: TestClient[Request, Application],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful generic command route returns ok with result."""
    # Register a temp handler and add it to the HTTP allowlist for this test.
    from openclaw_node import http_api as http_api_mod
    from openclaw_node.commands import dispatcher as _dispatcher

    monkeypatch.setitem(_dispatcher._REGISTRY, "test.echo", lambda p: {"echoed": p})
    monkeypatch.setattr(http_api_mod, "_HTTP_COMMAND_ALLOWLIST", frozenset({"ping", "test.echo"}))
    response = await client.post("/v1/commands/test.echo", json={"x": 1})
    data = await response.json()
    assert response.status == 200
    assert data["ok"] is True
    assert data["result"]["echoed"] == {"x": 1}


@pytest.mark.asyncio
async def test_command_dispatch_rejects_non_allowlisted(
    client: TestClient[Request, Application],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-allowlisted commands return 404 UNKNOWN_COMMAND even if registered (#88/3)."""
    from openclaw_node.commands import dispatcher as _dispatcher

    monkeypatch.setitem(
        _dispatcher._REGISTRY, "system.run", lambda p: {"should": "never run via http"}
    )
    response = await client.post("/v1/commands/system.run", json={})
    data = await response.json()
    assert response.status == 404
    assert data == {"ok": False, "error": "UNKNOWN_COMMAND", "command": "system.run"}


@pytest.mark.asyncio
async def test_json_body_non_object_400(client: TestClient[Request, Application]) -> None:
    """A non-object JSON body returns 400."""
    response = await client.post(
        "/commands/ping",
        data=b"[1, 2, 3]",
        headers={"Content-Type": "application/json"},
    )
    assert response.status == 400


@pytest.mark.asyncio
async def test_ha_snapshot_success(tmp_path: Path) -> None:
    """ha_snapshot returns entity count when HA is reachable."""
    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="",
        hass_url="http://ha.test:8123",
        hass_token="ha-tok",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=_TEST_TOKEN,
    )
    runtime = NodeRuntime(config)
    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](
        server, headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
    )
    await tc.start_server()

    try:
        # Mock the HA REST calls at the http_api module level
        ha_config_resp = MagicMock()
        ha_config_resp.ok = True
        ha_config_resp.status = 200
        ha_config_resp.json = AsyncMock(return_value={"version": "2026.6.1"})

        ha_states_resp = MagicMock()
        ha_states_resp.ok = True
        ha_states_resp.status = 200
        ha_states_resp.json = AsyncMock(return_value=[{"entity_id": "light.test", "state": "on"}])

        class _ResponseCM:
            def __init__(self, resp: Any) -> None:
                self._resp = resp

            async def __aenter__(self) -> Any:
                return self._resp

            async def __aexit__(self, *args: Any) -> None:
                pass

        class _SessionCM:
            async def __aenter__(self) -> Any:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

            def get(self, url: str, headers: Any = None) -> Any:
                return _ResponseCM(ha_config_resp if "config" in url else ha_states_resp)

        with patch("openclaw_node.http_api.ClientSession", return_value=_SessionCM()):
            response = await tc.get("/v1/ha/snapshot")
            data = await response.json()

        assert response.status == 200
        assert data["ok"] is True
        assert data["entity_count"] == 1
        assert data["ha_version"] == "2026.6.1"
    finally:
        await tc.close()


def test_aiohttp_timeout_returns_timeout() -> None:
    """aiohttp_timeout returns a ClientTimeout instance."""
    from aiohttp import ClientTimeout

    result = aiohttp_timeout()
    assert isinstance(result, ClientTimeout)
    assert result.total == 8


@pytest.mark.asyncio
async def test_ha_snapshot_unreachable(tmp_path: Path) -> None:
    """ha_snapshot returns 503 when HA REST is unreachable."""
    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="",
        hass_url="http://ha.test:8123",
        hass_token="ha-tok",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=_TEST_TOKEN,
    )
    runtime = NodeRuntime(config)
    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](
        server, headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
    )
    await tc.start_server()

    try:

        class _FailingSessionCM:
            async def __aenter__(self) -> Any:
                raise aiohttp.ClientError("refused")

            async def __aexit__(self, *args: Any) -> None:
                pass

        with patch("openclaw_node.http_api.ClientSession", return_value=_FailingSessionCM()):
            response = await tc.get("/v1/ha/snapshot")
            data = await response.json()

        assert response.status == 503
        assert data["error"] == "HA_REST_UNREACHABLE"
    finally:
        await tc.close()


@pytest.mark.asyncio
async def test_json_body_none_returns_empty(client: TestClient[Request, Application]) -> None:
    """A JSON null body is treated as an empty dict."""
    response = await client.post(
        "/commands/ping",
        data=b"null",
        headers={"Content-Type": "application/json"},
    )
    # null body is treated as no params — pong with empty message
    assert response.status == 200
    data = await response.json()
    assert data["pong"] is True


def test_node_runtime_is_paired_false() -> None:
    """NodeRuntime.is_paired is False when not PAIRED."""
    from pathlib import Path

    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=Path("/tmp"),
    )
    runtime = NodeRuntime(config)
    assert runtime.is_paired is False
    runtime.pairing_state = PairingState.PENDING
    assert runtime.is_paired is False


# ---------------------------------------------------------------------------
# /v1/conversation/stream — precondition error frames (#127 coverage)
# ---------------------------------------------------------------------------


def _stream_runtime(tmp_path: Path) -> NodeRuntime:
    """Build a paired runtime suitable for /v1/conversation/stream tests."""
    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=_TEST_TOKEN,
    )
    runtime = NodeRuntime(config)
    runtime.pairing_state = PairingState.PAIRED
    runtime.node_connected = True
    runtime.operator_connected = True
    return runtime


async def _post_stream(runtime: NodeRuntime, body: dict[str, Any]) -> tuple[int, list[Any]]:
    """POST body to /v1/conversation/stream and parse the NDJSON frames."""
    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](
        server, headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
    )
    await tc.start_server()
    try:
        response = await tc.post("/v1/conversation/stream", json=body)
        status = response.status
        text = await response.text()
    finally:
        await tc.close()
    frames = [json.loads(line) for line in text.strip().split("\n") if line]
    return status, frames


@pytest.mark.asyncio
async def test_assist_turn_stream_operator_not_connected(tmp_path: Path) -> None:
    runtime = _stream_runtime(tmp_path)
    runtime.operator_connected = False
    status, frames = await _post_stream(runtime, {"text": "hi", "conversation_id": "c1"})
    assert status == 502
    assert frames == [{"error": "OPERATOR_NOT_CONNECTED"}]


@pytest.mark.asyncio
async def test_assist_turn_stream_relay_not_initialised(tmp_path: Path) -> None:
    runtime = _stream_runtime(tmp_path)
    runtime.chat_relay = None
    status, frames = await _post_stream(runtime, {"text": "hi", "conversation_id": "c1"})
    assert status == 502
    assert frames == [{"error": "RELAY_NOT_INITIALISED"}]


@pytest.mark.asyncio
async def test_assist_turn_stream_empty_text(tmp_path: Path) -> None:
    from openclaw_node.chat_relay import ChatRelay

    runtime = _stream_runtime(tmp_path)
    runtime.chat_relay = MagicMock(spec=ChatRelay)
    status, frames = await _post_stream(runtime, {"text": "   ", "conversation_id": "c1"})
    assert status == 400
    assert frames == [{"error": "EMPTY_TEXT"}]


@pytest.mark.asyncio
async def test_assist_turn_stream_missing_conversation_id(tmp_path: Path) -> None:
    from openclaw_node.chat_relay import ChatRelay

    runtime = _stream_runtime(tmp_path)
    runtime.chat_relay = MagicMock(spec=ChatRelay)
    status, frames = await _post_stream(runtime, {"text": "hi", "conversation_id": ""})
    assert status == 400
    assert frames == [{"error": "MISSING_CONVERSATION_ID"}]


@pytest.mark.asyncio
async def test_assist_turn_stream_unexpected_exception_surfaces_internal_error(
    tmp_path: Path,
) -> None:
    """An unexpected exception inside the relay surfaces as
    `{"error": "INTERNAL_ERROR"}` on the stream and does NOT propagate
    (otherwise HA would see a torn connection instead of an error frame).
    """
    from openclaw_node.chat_relay import ChatRelay

    runtime = _stream_runtime(tmp_path)

    async def _fake_stream(*_args: Any, **_kwargs: Any) -> Any:
        yield "first"
        raise RuntimeError("simulated relay bug")

    mock_relay = MagicMock(spec=ChatRelay)
    mock_relay.stream_turn = _fake_stream
    runtime.chat_relay = mock_relay

    status, frames = await _post_stream(runtime, {"text": "hi", "conversation_id": "c-boom"})
    assert status == 200  # headers already flushed before the exception
    assert frames == [{"delta": "first"}, {"error": "INTERNAL_ERROR"}]


# ---------------------------------------------------------------------------
# TODO #29 — tool_progress frames serialisation in the HTTP API layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assist_turn_stream_emits_tool_progress_frames(tmp_path: Path) -> None:
    """ToolProgressFrame sentinels are serialised as ``{"tool_progress": true,
    "phase": ..., "name": ..., "seq": ...}`` NDJSON frames on the wire.
    A non-empty ``id`` is included; an empty ``id`` is omitted."""
    from openclaw_node.chat_relay import ChatRelay, ToolProgressFrame

    runtime = _stream_runtime(tmp_path)

    async def _fake_stream(*_args: Any, **_kwargs: Any) -> Any:
        yield ToolProgressFrame(phase="start", name="Bash", id="call-1", seq=1)
        yield "partial"
        yield ToolProgressFrame(phase="end", name="Bash", id="call-1", seq=1)
        yield " done"
        # id-less frame: id field must be omitted from the wire frame
        yield ToolProgressFrame(phase="start", name="Python", id="", seq=2)

    mock_relay = MagicMock(spec=ChatRelay)
    mock_relay.stream_turn = _fake_stream
    runtime.chat_relay = mock_relay

    status, frames = await _post_stream(
        runtime,
        {
            "text": "hi",
            "conversation_id": "c-tp",
            "client_caps": ["tool-progress-frames"],
        },
    )
    assert status == 200
    assert frames == [
        {"tool_progress": True, "phase": "start", "name": "Bash", "id": "call-1", "seq": 1},
        {"delta": "partial"},
        {"tool_progress": True, "phase": "end", "name": "Bash", "id": "call-1", "seq": 1},
        {"delta": " done"},
        {"tool_progress": True, "phase": "start", "name": "Python", "seq": 2},
        {"done": True},
    ]


@pytest.mark.asyncio
async def test_assist_turn_stream_passes_client_caps_to_relay(tmp_path: Path) -> None:
    """The ``client_caps`` body field is parsed and forwarded to
    ``relay.stream_turn`` as the ``client_caps`` keyword argument."""
    from openclaw_node.chat_relay import ChatRelay

    runtime = _stream_runtime(tmp_path)
    captured_caps: list[list[str]] = []

    async def _fake_stream(
        *_args: Any, client_caps: list[str] | None = None, **_kwargs: Any
    ) -> Any:
        captured_caps.append(list(client_caps or []))
        yield "ok"

    mock_relay = MagicMock(spec=ChatRelay)
    mock_relay.stream_turn = _fake_stream
    runtime.chat_relay = mock_relay

    await _post_stream(
        runtime,
        {
            "text": "hi",
            "conversation_id": "c-caps",
            "client_caps": ["tool-progress-frames", "other-cap"],
        },
    )
    assert captured_caps == [["tool-progress-frames", "other-cap"]]


@pytest.mark.asyncio
async def test_assist_turn_stream_no_client_caps_passes_empty_list(tmp_path: Path) -> None:
    """Without ``client_caps`` in the body the relay receives an empty list."""
    from openclaw_node.chat_relay import ChatRelay

    runtime = _stream_runtime(tmp_path)
    captured_caps: list[list[str]] = []

    async def _fake_stream(
        *_args: Any, client_caps: list[str] | None = None, **_kwargs: Any
    ) -> Any:
        captured_caps.append(list(client_caps or []))
        yield "ok"

    mock_relay = MagicMock(spec=ChatRelay)
    mock_relay.stream_turn = _fake_stream
    runtime.chat_relay = mock_relay

    await _post_stream(runtime, {"text": "hi", "conversation_id": "c-nocaps"})
    assert captured_caps == [[]]
