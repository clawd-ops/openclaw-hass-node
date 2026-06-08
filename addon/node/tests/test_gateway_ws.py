"""Tests for openclaw_node.gateway_ws."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openclaw_node.config import NodeConfig
from openclaw_node.gateway_ws import _CONNECT_COMMANDS, GatewayClient, _make_req
from openclaw_node.identity import generate_identity
from openclaw_node.pairing import PairingState


def _make_config() -> NodeConfig:
    return NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="test",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=Path("/tmp/test"),
    )


def _make_client(device_token: str = "") -> GatewayClient:
    config = _make_config()
    identity = generate_identity()
    return GatewayClient(config=config, identity=identity, device_token=device_token)


# ---- _make_req tests ----


def test_make_req_structure() -> None:
    req = _make_req("connect", {"foo": "bar"})
    assert req["type"] == "req"
    assert req["method"] == "connect"
    assert req["params"] == {"foo": "bar"}
    assert "id" in req


def test_make_req_unique_ids() -> None:
    r1 = _make_req("m", {})
    r2 = _make_req("m", {})
    assert r1["id"] != r2["id"]


# ---- GatewayClient init tests ----


def test_gateway_client_initial_state() -> None:
    client = _make_client()
    assert client.pairing_state is PairingState.UNKNOWN


def test_gateway_client_with_pairing_callback() -> None:
    states: list[PairingState] = []
    config = _make_config()
    identity = generate_identity()
    client = GatewayClient(
        config=config,
        identity=identity,
        device_token="",
        pairing_state_callback=lambda s: states.append(s),
    )
    assert client.pairing_state is PairingState.UNKNOWN


def test_connect_commands_advertise_full_surface() -> None:
    assert _CONNECT_COMMANDS == [
        "ping",
        "fs.read",
        "fs.list",
        "fs.stat",
        "fs.glob",
        "fs.write",
        "fs.restore",
        "fs.history",
        "fs.diff",
        "fs.move",
        "fs.delete",
        "fs.patch",
        "system.run",
        "system.which",
        "ha.list_states",
        "ha.get_state",
        "ha.call_service",
        "ha.list_areas",
        "ha.list_devices",
        "ha.list_services",
        "ha.list_entity_registry",
        "ha.logbook",
        "ha.history",
        "ha.reload_config",
        "ha.light_turn_on",
        "ha.light_turn_off",
        "ha.list_automations",
        "ha.check_config",
    ]


# ---- _recv_challenge tests ----


async def test_recv_challenge_valid() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.recv = AsyncMock(
        return_value=json.dumps(
            {
                "type": "event",
                "event": "connect.challenge",
                "payload": {"nonce": "abc123", "ts": 9999},
            }
        )
    )
    nonce, ts = await client._recv_challenge(ws)
    assert nonce == "abc123"
    assert ts == 9999


async def test_recv_challenge_wrong_event() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.recv = AsyncMock(return_value=json.dumps({"type": "event", "event": "something.else"}))
    with pytest.raises(ValueError, match=r"Expected connect\.challenge"):
        await client._recv_challenge(ws)


# ---- _send_connect tests ----


async def test_send_connect_sends_correct_frame() -> None:
    client = _make_client(device_token="my-token")
    ws = AsyncMock()
    ws.send = AsyncMock()

    req_id = await client._send_connect(ws, "test-nonce")
    assert req_id
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "req"
    assert sent["method"] == "connect"
    assert sent["params"]["device"]["nonce"] == "test-nonce"
    assert sent["params"]["auth"]["token"] == "my-token"
    assert sent["params"]["role"] == "node"
    assert sent["params"]["commands"] == _CONNECT_COMMANDS


# ---- _recv_connect_response tests ----


async def test_recv_connect_response_ok() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.recv = AsyncMock(
        return_value=json.dumps({"type": "res", "ok": True, "payload": {"sessionId": "s1"}})
    )
    await client._recv_connect_response(ws, "req-1")
    assert client.pairing_state is PairingState.PAIRED


async def test_recv_connect_response_pairing_required() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.recv = AsyncMock(
        return_value=json.dumps({"type": "res", "ok": False, "error": "PAIRING_REQUIRED"})
    )
    await client._recv_connect_response(ws, "req-1")
    assert client.pairing_state is PairingState.PENDING


async def test_recv_connect_response_wrong_type() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.recv = AsyncMock(return_value=json.dumps({"type": "event", "event": "something"}))
    with pytest.raises(ValueError, match="Expected res frame"):
        await client._recv_connect_response(ws, "req-1")


# ---- _handle_invoke tests ----


async def test_handle_invoke_ping() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    await client._handle_invoke(
        ws, {"id": "inv-1", "command": "ping", "params": {"message": "hi"}}
    )
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["params"]["ok"] is True
    assert sent["params"]["payload"]["pong"] is True


async def test_handle_invoke_unknown_command() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    await client._handle_invoke(
        ws, {"id": "inv-2", "command": "does.not.exist", "params": {}}
    )
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["params"]["ok"] is False
    assert sent["params"]["error"]["code"] == "UNKNOWN_COMMAND"


async def test_handle_invoke_command_exception() -> None:
    """Commands that raise unexpected errors return a COMMAND_ERROR response."""
    from openclaw_node.commands.dispatcher import register_handler

    def bad_handler(params: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("something went wrong")

    register_handler("test.bad", bad_handler)
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    await client._handle_invoke(ws, {"id": "inv-3", "command": "test.bad", "params": {}})
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["params"]["ok"] is False
    assert sent["params"]["error"]["code"] == "COMMAND_ERROR"
    assert "something went wrong" not in json.dumps(sent)


# ---- _ack_pending tests ----


async def test_ack_pending_sends_correct_request() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    await client._ack_pending(ws, "inv-99")
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["method"] == "node.pending.ack"
    assert sent["params"]["ids"] == ["inv-99"]


# ---- _pull_pending tests ----


async def test_pull_pending_empty_items() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.recv = AsyncMock(return_value=json.dumps({"ok": True, "payload": {"items": []}}))
    await client._pull_pending(ws)
    ws.send.assert_called_once()


async def test_pull_pending_with_items() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    items = [{"id": "i1", "command": "ping", "params": {}}]
    ws.recv = AsyncMock(return_value=json.dumps({"ok": True, "payload": {"items": items}}))
    await client._pull_pending(ws)
    # One send for pull request, one for invoke result, one for ack
    assert ws.send.call_count == 3


async def test_pull_pending_failure_logged() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.recv = AsyncMock(return_value=json.dumps({"ok": False, "error": "SOME_ERROR"}))
    # Should not raise; just log and return
    await client._pull_pending(ws)


# ---- _await_approval tests ----


async def test_await_approval_transitions_to_paired() -> None:
    client = _make_client()
    # Put into pending state
    client._pairing.on_connect_response(ok=False, error="PAIRING_REQUIRED")
    assert client.pairing_state is PairingState.PENDING

    ws = MagicMock()

    async def _messages() -> AsyncIterator[str]:
        yield json.dumps({"type": "event", "event": "other.event"})
        yield json.dumps({"type": "event", "event": "connect.approved", "payload": {}})

    ws.__aiter__ = lambda self: _messages().__aiter__()
    await client._await_approval(ws)
    # mypy narrows state from prior assert; state actually mutated by _await_approval
    assert client.pairing_state is PairingState.PAIRED  # type: ignore[comparison-overlap]


# ---- _event_loop tests ----


async def test_event_loop_dispatches_invoke() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()

    messages = [
        json.dumps(
            {
                "type": "event",
                "event": "node.invoke.request",
                "payload": {"id": "e1", "command": "ping", "params": {}},
            }
        ),
    ]

    async def _recv_iter() -> AsyncIterator[str]:
        for m in messages:
            yield m

    ws.__aiter__ = lambda self: _recv_iter().__aiter__()
    await client._event_loop(ws)
    ws.send.assert_called_once()


async def test_event_loop_ignores_unknown_events() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()

    messages = [
        json.dumps({"type": "event", "event": "some.other.event", "payload": {}}),
    ]

    async def _recv_iter() -> AsyncIterator[str]:
        for m in messages:
            yield m

    ws.__aiter__ = lambda self: _recv_iter().__aiter__()
    await client._event_loop(ws)
    ws.send.assert_not_called()


# ---- run() reconnect test ----


async def test_run_reconnects_on_exception() -> None:
    """run() calls on_reconnect and retries after a connection error."""
    client = _make_client()
    call_count = 0

    async def _failing_connect_and_loop() -> None:
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ConnectionError("refused")
        # Second call: stop the loop by raising CancelledError
        raise asyncio.CancelledError

    with (
        patch.object(client, "_connect_and_loop", _failing_connect_and_loop),
        patch("asyncio.sleep", return_value=None),
        pytest.raises(asyncio.CancelledError),
    ):
        await client.run()

    assert call_count == 2


async def test_connect_and_loop_paired() -> None:
    """_connect_and_loop runs the full handshake with a mocked WS."""
    client = _make_client()
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock()

    # recv sequence: challenge, then connect ok response, then empty event loop
    challenge = json.dumps(
        {
            "type": "event",
            "event": "connect.challenge",
            "payload": {"nonce": "nonce1", "ts": 1000},
        }
    )
    connect_ok = json.dumps({"type": "res", "ok": True, "payload": {"sessionId": "s1"}})
    pending_pull_ok = json.dumps({"ok": True, "payload": {"items": []}})

    recv_calls = [challenge, connect_ok, pending_pull_ok]
    recv_idx = 0

    async def _recv() -> str:
        nonlocal recv_idx
        val = recv_calls[recv_idx]
        recv_idx += 1
        return val

    mock_ws.recv = _recv

    # Make __aiter__ return empty so event_loop exits immediately

    async def _empty_iter() -> AsyncIterator[str]:
        return
        yield

    mock_ws.__aiter__ = lambda self: _empty_iter()

    context = AsyncMock()
    context.__aenter__ = AsyncMock(return_value=mock_ws)
    context.__aexit__ = AsyncMock(return_value=False)

    with patch("websockets.asyncio.client.connect", return_value=context):
        await client._connect_and_loop()

    assert client.pairing_state is PairingState.PAIRED


async def test_connect_and_loop_pending_then_approved() -> None:
    """_connect_and_loop enters _await_approval when pairing is pending."""
    client = _make_client()
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock()

    challenge = json.dumps(
        {
            "type": "event",
            "event": "connect.challenge",
            "payload": {"nonce": "nonce2", "ts": 2000},
        }
    )
    connect_pending = json.dumps({"type": "res", "ok": False, "error": "PAIRING_REQUIRED"})
    pending_pull_ok = json.dumps({"ok": True, "payload": {"items": []}})

    recv_calls = [challenge, connect_pending, pending_pull_ok]
    recv_idx = 0

    async def _recv() -> str:
        nonlocal recv_idx
        val = recv_calls[recv_idx]
        recv_idx += 1
        return val

    mock_ws.recv = _recv

    # __aiter__ yields: first the approval event, then ends for event_loop
    messages_for_approval = [
        json.dumps({"type": "event", "event": "connect.approved", "payload": {}}),
    ]

    async def _approval_iter() -> AsyncIterator[str]:
        for m in messages_for_approval:
            yield m

    mock_ws.__aiter__ = lambda self: _approval_iter()

    context = AsyncMock()
    context.__aenter__ = AsyncMock(return_value=mock_ws)
    context.__aexit__ = AsyncMock(return_value=False)

    with patch("websockets.asyncio.client.connect", return_value=context):
        await client._connect_and_loop()

    assert client.pairing_state is PairingState.PAIRED


# ---- _notify_pairing_state tests ----


def test_notify_pairing_state_with_callback() -> None:
    states: list[PairingState] = []
    config = _make_config()
    identity = generate_identity()
    client = GatewayClient(
        config=config,
        identity=identity,
        pairing_state_callback=lambda s: states.append(s),
    )
    client._notify_pairing_state()
    assert states == [PairingState.UNKNOWN]


def test_notify_pairing_state_without_callback() -> None:
    client = _make_client()
    # Should not raise when no callback is registered
    client._notify_pairing_state()


# ---- runtime gateway_connected toggle ----


def test_runtime_gateway_connected_starts_false() -> None:
    """A fresh NodeRuntime reports gateway_connected=False until the WS connects."""
    from openclaw_node.http_api import NodeRuntime

    runtime = NodeRuntime(_make_config())
    assert runtime.gateway_connected is False
