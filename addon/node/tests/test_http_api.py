"""Tests for the node local HTTP API."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from aiohttp.web import Application, Request

from openclaw_node.config import NodeConfig
from openclaw_node.http_api import NodeRuntime, aiohttp_timeout, create_app
from openclaw_node.pairing import PairingState


@pytest_asyncio.fixture
async def client(
    tmp_path: Path,
) -> AsyncGenerator[TestClient[Request, Application]]:
    """Return a test client for the local API."""
    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gateway.example/ws",
        pairing_token="",
        node_name="test-node",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
    )
    app = create_app(NodeRuntime(config))
    server = TestServer(app)
    client = TestClient[Request, Application](server)
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
    )
    runtime = NodeRuntime(config)
    runtime.pairing_state = PairingState.PAIRED
    runtime.gateway_connected = True
    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](server)
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
    )
    runtime = NodeRuntime(config)
    runtime.pairing_state = PairingState.PAIRED
    runtime.gateway_connected = True
    runtime.chat_relay = ChatRelay(AsyncMock())
    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](server)
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
    )
    runtime = NodeRuntime(config)
    runtime.pairing_state = PairingState.PAIRED
    runtime.gateway_connected = True

    mock_relay = MagicMock(spec=ChatRelay)
    mock_relay.relay_turn = AsyncMock(return_value="Lights turned on!")
    runtime.chat_relay = mock_relay

    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](server)
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
        mock_relay.relay_turn.assert_awaited_once_with("conv-42", "turn on the lights", "en")
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
    )
    runtime = NodeRuntime(config)
    runtime.pairing_state = PairingState.PAIRED
    runtime.gateway_connected = True

    mock_relay = MagicMock(spec=ChatRelay)
    mock_relay.relay_turn = AsyncMock(side_effect=ChatRelayError("TIMEOUT", "chat.send timed out"))
    runtime.chat_relay = mock_relay

    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](server)
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
async def test_command_dispatch_success(client: TestClient[Request, Application]) -> None:
    """Successful unknown command route returns ok with result."""
    # Register a temp handler and dispatch via the generic route
    from openclaw_node.commands.dispatcher import register_handler

    register_handler("test.echo", lambda p: {"echoed": p})
    response = await client.post("/v1/commands/test.echo", json={"x": 1})
    data = await response.json()
    assert response.status == 200
    assert data["ok"] is True
    assert data["result"]["echoed"] == {"x": 1}


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
    )
    runtime = NodeRuntime(config)
    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](server)
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
    )
    runtime = NodeRuntime(config)
    server = TestServer(create_app(runtime))
    tc = TestClient[Request, Application](server)
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
