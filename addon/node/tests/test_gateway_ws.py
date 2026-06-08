"""Tests for openclaw_node.gateway_ws."""

from __future__ import annotations

import asyncio
import contextlib
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


def _find_sent_method(send_mock: AsyncMock, method: str) -> dict[str, Any]:
    """Return the last frame sent through ``send_mock`` whose method matches."""
    for call in reversed(send_mock.call_args_list):
        frame: dict[str, Any] = json.loads(call[0][0])
        if frame.get("method") == method:
            return frame
    raise AssertionError(f"No sent frame with method={method!r}")


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
        return_value=json.dumps(
            {"type": "res", "id": "req-1", "ok": True, "payload": {"sessionId": "s1"}}
        )
    )
    await client._recv_connect_response(ws, "req-1")
    assert client.pairing_state is PairingState.PAIRED


async def test_recv_connect_response_pairing_required() -> None:
    """Canonical ResponseFrame.error shape: `{code, message}` object."""
    client = _make_client()
    ws = AsyncMock()
    ws.recv = AsyncMock(
        return_value=json.dumps(
            {
                "type": "res",
                "id": "req-1",
                "ok": False,
                "error": {"code": "PAIRING_REQUIRED", "message": "device awaiting approval"},
            }
        )
    )
    await client._recv_connect_response(ws, "req-1")
    assert client.pairing_state is PairingState.PENDING


async def test_recv_connect_response_id_mismatch_raises() -> None:
    """An interleaved response with a different id must not be accepted."""
    client = _make_client()
    ws = AsyncMock()
    ws.recv = AsyncMock(
        return_value=json.dumps({"type": "res", "id": "other", "ok": True, "payload": {}})
    )
    with pytest.raises(ValueError, match="id mismatch"):
        await client._recv_connect_response(ws, "req-1")


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
        ws,
        {"id": "inv-1", "command": "ping", "paramsJSON": json.dumps({"message": "hi"})},
    )
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["params"]["ok"] is True
    assert sent["params"]["payload"]["pong"] is True
    # Round-trip the decoded params into the handler — proves paramsJSON
    # was decoded, not silently dropped.
    assert sent["params"]["payload"]["message"] == "hi"


async def test_handle_invoke_unknown_command() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    await client._handle_invoke(
        ws,
        {"id": "inv-2", "command": "does.not.exist", "paramsJSON": "{}"},
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
    await client._handle_invoke(ws, {"id": "inv-3", "command": "test.bad", "paramsJSON": "{}"})
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["params"]["ok"] is False
    assert sent["params"]["error"]["code"] == "COMMAND_ERROR"
    assert "something went wrong" not in json.dumps(sent)


# ---- _ack_pending tests ----


async def test_ack_pending_sends_correct_request_and_awaits_response() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()

    async def _ack_response() -> str:
        # Echo back ok=true with the same id the client just sent.
        sent_frame = json.loads(ws.send.call_args[0][0])
        return json.dumps({"type": "res", "id": sent_frame["id"], "ok": True})

    ws.recv = _ack_response
    await client._ack_pending(ws, "inv-99")
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["method"] == "node.pending.ack"
    assert sent["params"]["ids"] == ["inv-99"]


async def test_ack_pending_logs_when_gateway_rejects(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-ok ack response logs a WARNING so missed acks aren't silent."""
    import logging

    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()

    async def _ack_response() -> str:
        sent_frame = json.loads(ws.send.call_args[0][0])
        return json.dumps(
            {
                "type": "res",
                "id": sent_frame["id"],
                "ok": False,
                "error": {"code": "UNKNOWN_ID", "message": "no such pending id"},
            }
        )

    ws.recv = _ack_response
    with caplog.at_level(logging.WARNING):
        await client._ack_pending(ws, "inv-bad")
    assert any("rejected by gateway" in rec.message for rec in caplog.records)


async def test_ack_pending_times_out_when_gateway_silent(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the gateway never responds, _ack_pending logs a timeout and returns.

    Regression guard against the deadlock-on-missing-ack scenario Codex
    flagged in review. With no bounded timeout, a single dropped ack
    response could stall the whole pending drain.
    """
    import logging

    # Shorten the timeout so the test is fast.
    monkeypatch.setattr("openclaw_node.gateway_ws._ACK_RESPONSE_TIMEOUT_S", 0.05)
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()

    async def _never() -> str:
        await asyncio.sleep(10)
        return ""  # pragma: no cover

    ws.recv = _never
    with caplog.at_level(logging.WARNING):
        await client._ack_pending(ws, "inv-silent")
    assert any("response timeout" in rec.message for rec in caplog.records)


async def test_ack_pending_tolerates_id_mismatch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An interleaved frame with the wrong id is logged and dropped."""
    import logging

    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.recv = AsyncMock(
        return_value=json.dumps({"type": "event", "event": "node.invoke.request", "payload": {}})
    )
    with caplog.at_level(logging.WARNING):
        await client._ack_pending(ws, "inv-x")
    assert any("unexpected frame" in rec.message for rec in caplog.records)


# ---- _pull_pending tests ----


async def test_pull_pending_empty_items() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.recv = AsyncMock(return_value=json.dumps({"ok": True, "payload": {"items": []}}))
    await client._pull_pending(ws)
    ws.send.assert_called_once()


async def test_pull_pending_canonical_envelope() -> None:
    """Each item is the canonical `{id, payload: <invoke-payload>}` envelope."""
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    items = [
        {
            "id": "queue-item-1",
            "payload": {"id": "inv-A", "command": "ping", "paramsJSON": "{}"},
        }
    ]
    ws.recv = AsyncMock(return_value=json.dumps({"ok": True, "payload": {"items": items}}))
    await client._pull_pending(ws)
    # pull req, invoke result, ack
    assert ws.send.call_count == 3
    ack_frame = json.loads(ws.send.call_args_list[2][0][0])
    assert ack_frame["method"] == "node.pending.ack"
    # Must ack the queue-item id from the envelope, not the inner invoke id.
    assert ack_frame["params"]["ids"] == ["queue-item-1"]


@pytest.mark.parametrize(
    "params_json",
    [
        "{not-json",  # malformed JSON
        json.dumps([1, 2, 3]),  # JSON array
        json.dumps("string-value"),  # JSON string
        json.dumps(42),  # JSON primitive
        json.dumps(None),  # JSON null
        "",  # empty string
    ],
    ids=["malformed", "array", "string", "primitive", "null", "empty"],
)
async def test_handle_invoke_invalid_paramsjson_returns_invalid_params(
    params_json: str,
) -> None:
    """Malformed / non-object `paramsJSON` must surface as INVALID_PARAMS, not silently {}."""
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    await client._handle_invoke(
        ws,
        {"id": "inv-bad", "command": "ping", "paramsJSON": params_json},
    )
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["params"]["ok"] is False
    assert sent["params"]["error"]["code"] == "INVALID_PARAMS"


async def test_pull_pending_non_dict_item_skipped() -> None:
    """A non-dict item in the pending list is skipped without crashing the loop."""
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    items = [
        "not-an-object",
        {
            "id": "good-item",
            "payload": {"id": "inv-G", "command": "ping", "paramsJSON": "{}"},
        },
    ]
    ws.recv = AsyncMock(return_value=json.dumps({"ok": True, "payload": {"items": items}}))
    await client._pull_pending(ws)
    # pull req + (invoke result + ack) for the good item only.
    assert ws.send.call_count == 3
    ack_frame = _find_sent_method(ws.send, "node.pending.ack")
    assert ack_frame["params"]["ids"] == ["good-item"]


async def test_pull_pending_missing_id_skipped() -> None:
    """A pending item with no `id` is skipped without ack."""
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    items = [
        {"payload": {"id": "inv", "command": "ping", "paramsJSON": "{}"}},  # no envelope id
    ]
    ws.recv = AsyncMock(return_value=json.dumps({"ok": True, "payload": {"items": items}}))
    await client._pull_pending(ws)
    # Only the pull request itself was sent; no invoke, no ack.
    assert ws.send.call_count == 1


async def test_pull_pending_malformed_payload_skipped() -> None:
    """A pending item with `payload` present but not a dict is skipped (not acked)."""
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    items = [
        {"id": "bad-item", "payload": "not-a-dict"},  # malformed: skipped
        {
            "id": "good-item",
            "payload": {"id": "inv-G", "command": "ping", "paramsJSON": "{}"},
        },
    ]
    ws.recv = AsyncMock(return_value=json.dumps({"ok": True, "payload": {"items": items}}))
    await client._pull_pending(ws)
    # pull req + (invoke result + ack) for the good item only.
    assert ws.send.call_count == 3
    ack_frame = _find_sent_method(ws.send, "node.pending.ack")
    assert ack_frame["params"]["ids"] == ["good-item"]


async def test_pull_pending_failure_logged() -> None:
    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.recv = AsyncMock(
        return_value=json.dumps(
            {"ok": False, "error": {"code": "SOME_ERROR", "message": "queue offline"}}
        )
    )
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


async def test_await_approval_raises_when_socket_closes_without_approval() -> None:
    """If the WS iterator exits without approval, raise so reconnect kicks in."""
    client = _make_client()
    client._pairing.on_connect_response(ok=False, error="PAIRING_REQUIRED")
    ws = MagicMock()

    async def _empty() -> AsyncIterator[str]:
        for _ in ():  # pragma: no cover — empty iterator marker
            yield ""
        return

    ws.__aiter__ = lambda self: _empty().__aiter__()
    with pytest.raises(ConnectionError, match="before pairing approval"):
        await client._await_approval(ws)


# ---- _event_loop tests ----


async def test_event_loop_dispatches_invoke() -> None:
    from openclaw_node.chat_relay import ChatRelay

    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    relay = ChatRelay(AsyncMock())

    messages = [
        json.dumps(
            {
                "type": "event",
                "event": "node.invoke.request",
                "payload": {"id": "e1", "command": "ping", "paramsJSON": "{}"},
            }
        ),
    ]

    async def _recv_iter() -> AsyncIterator[str]:
        for m in messages:
            yield m

    ws.__aiter__ = lambda self: _recv_iter().__aiter__()
    await client._event_loop(ws, relay)
    ws.send.assert_called_once()


async def test_event_loop_ignores_unknown_events() -> None:
    from openclaw_node.chat_relay import ChatRelay

    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    relay = ChatRelay(AsyncMock())

    messages = [
        json.dumps({"type": "event", "event": "some.other.event", "payload": {}}),
    ]

    async def _recv_iter() -> AsyncIterator[str]:
        for m in messages:
            yield m

    ws.__aiter__ = lambda self: _recv_iter().__aiter__()
    await client._event_loop(ws, relay)
    ws.send.assert_not_called()


async def test_event_loop_routes_session_message_to_relay() -> None:
    """session.message events are forwarded to the ChatRelay."""
    from openclaw_node.chat_relay import ChatRelay

    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    relay = ChatRelay(AsyncMock())

    messages = [
        json.dumps({
            "type": "event",
            "event": "session.message",
            "payload": {
                "sessionKey": "ha-assist:conv-1",
                "role": "assistant",
                "message": "Hello from the agent!",
            },
        }),
    ]

    async def _recv_iter() -> AsyncIterator[str]:
        for m in messages:
            yield m

    ws.__aiter__ = lambda self: _recv_iter().__aiter__()
    await client._event_loop(ws, relay)

    assert relay._last_assistant_text.get("ha-assist:conv-1") == "Hello from the agent!"


async def test_event_loop_routes_response_to_relay() -> None:
    """RPC responses are forwarded to the ChatRelay's pending futures."""
    import asyncio

    from openclaw_node.chat_relay import ChatRelay

    client = _make_client()
    ws = AsyncMock()
    ws.send = AsyncMock()
    relay = ChatRelay(AsyncMock())

    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()
    relay._pending["req-123"] = future

    messages = [
        json.dumps({
            "type": "res",
            "id": "req-123",
            "ok": True,
            "payload": {"status": "done"},
        }),
    ]

    async def _recv_iter() -> AsyncIterator[str]:
        for m in messages:
            yield m

    ws.__aiter__ = lambda self: _recv_iter().__aiter__()
    await client._event_loop(ws, relay)

    assert future.done()
    assert future.result() == {"status": "done"}


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
    pending_pull_ok = json.dumps({"ok": True, "payload": {"items": []}})

    # The connect-response id must match the connect-request id (canonical
    # schema validation). Build the recv values lazily so we can echo the
    # actual req id captured from `ws.send`.
    recv_step = 0

    async def _recv() -> str:
        nonlocal recv_step
        recv_step += 1
        if recv_step == 1:
            return challenge
        if recv_step == 2:
            connect_req = _find_sent_method(mock_ws.send, "connect")
            return json.dumps(
                {
                    "type": "res",
                    "id": connect_req["id"],
                    "ok": True,
                    "payload": {"sessionId": "s1"},
                }
            )
        return pending_pull_ok

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
    pending_pull_ok = json.dumps({"ok": True, "payload": {"items": []}})

    recv_step = 0

    async def _recv() -> str:
        nonlocal recv_step
        recv_step += 1
        if recv_step == 1:
            return challenge
        if recv_step == 2:
            connect_req = _find_sent_method(mock_ws.send, "connect")
            return json.dumps(
                {
                    "type": "res",
                    "id": connect_req["id"],
                    "ok": False,
                    "error": {"code": "PAIRING_REQUIRED", "message": "approval needed"},
                }
            )
        return pending_pull_ok

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


# ---- device-token persistence permissions ----


def _make_client_in(data_dir: Path) -> GatewayClient:
    """Build a GatewayClient whose config.data_dir is *data_dir*."""
    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="t",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=data_dir,
    )
    identity = generate_identity()
    return GatewayClient(config=config, identity=identity)


def test_persist_device_token_writes_with_mode_0600(tmp_path: Path) -> None:
    """The device token must land at mode 0o600."""
    import stat as _stat

    client = _make_client_in(tmp_path)
    client._persist_device_token("issued-token")
    path = tmp_path / "device-token"
    assert path.read_text() == "issued-token"
    assert _stat.S_IMODE(path.stat().st_mode) == 0o600


def test_persist_device_token_tightens_existing_loose_file(tmp_path: Path) -> None:
    """An existing 0o644 token file must be tightened on the next persist."""
    import stat as _stat

    client = _make_client_in(tmp_path)
    path = tmp_path / "device-token"
    path.write_text("old")
    path.chmod(0o644)
    client._persist_device_token("new-token")
    assert _stat.S_IMODE(path.stat().st_mode) == 0o600


def test_persist_device_token_replaces_existing_loose_tmp(tmp_path: Path) -> None:
    """A pre-existing 0o644 device-token.tmp must end up 0o600 after persist."""
    import stat as _stat

    client = _make_client_in(tmp_path)
    tmp = tmp_path / "device-token.tmp"
    tmp.write_text("stale-tmp")
    tmp.chmod(0o644)
    client._persist_device_token("real-token")
    final = tmp_path / "device-token"
    assert final.read_text() == "real-token"
    assert _stat.S_IMODE(final.stat().st_mode) == 0o600
    assert not tmp.exists()


def test_persist_device_token_rejects_symlinked_tmp(tmp_path: Path) -> None:
    """A symlink planted at device-token.tmp is unlinked before write."""
    client = _make_client_in(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("untouched")
    tmp = tmp_path / "device-token.tmp"
    tmp.symlink_to(elsewhere)
    client._persist_device_token("real-token")
    # The decoy outside file must not have been overwritten via the symlink.
    assert elsewhere.read_text() == "untouched"
    final = tmp_path / "device-token"
    assert final.read_text() == "real-token"


def test_persist_device_token_no_chmod_through_symlink_final(tmp_path: Path) -> None:
    """If the final path is a symlink, the token still lands at the correct spot.

    Because `replace()` renames the temp file into place, the symlink at the
    final path is replaced atomically. The defensive `chmod` after replace
    must not follow whatever target the *new* final path resolves to (it is
    no longer a symlink at that point), but if a race re-created a symlink
    we must not chmod its target.
    """
    import stat as _stat

    client = _make_client_in(tmp_path)
    final = tmp_path / "device-token"
    decoy = tmp_path / "decoy"
    decoy.write_text("x")
    decoy.chmod(0o644)
    final.symlink_to(decoy)
    client._persist_device_token("real-token")
    # The replace turned `final` from a symlink into a regular file at 0o600.
    assert final.read_text() == "real-token"
    assert _stat.S_IMODE(final.lstat().st_mode) == 0o600
    # The decoy that the symlink USED to point at must still be 0o644 — proof
    # we never chmod'd through the symlink anywhere in the path.
    assert _stat.S_IMODE(decoy.stat().st_mode) == 0o644


def test_token_path_safe_to_unlink_accepts_path_inside_data_dir(tmp_path: Path) -> None:
    """Token path under data_dir is safe to unlink."""
    client = _make_client_in(tmp_path)
    token_path = tmp_path / "device-token"
    token_path.write_text("x")
    assert client._token_path_safe_to_unlink(token_path) is True


def test_token_path_safe_to_unlink_rejects_path_outside_data_dir(tmp_path: Path) -> None:
    """A path that resolves outside data_dir must be refused."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    client = _make_client_in(data_dir)
    outside = tmp_path / "elsewhere" / "device-token"
    outside.parent.mkdir()
    outside.write_text("x")
    assert client._token_path_safe_to_unlink(outside) is False
    assert outside.exists()


def test_token_path_safe_to_unlink_refuses_symlink(tmp_path: Path) -> None:
    """A symlink at the token path is refused even if the target is inside data_dir."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    real = data_dir / "real"
    real.write_text("x")
    link = data_dir / "device-token"
    link.symlink_to(real)
    client = _make_client_in(data_dir)
    assert client._token_path_safe_to_unlink(link) is False
    assert link.exists()
    assert real.exists()


def test_maybe_drop_invalid_device_token_unlinks_when_safe(tmp_path: Path) -> None:
    """A NOT_PAIRED trigger with a safe path actually removes the file."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    client = _make_client_in(data_dir)
    client._device_token = "stale"
    path = client._config.device_token_path
    path.write_text("stale")
    client._maybe_drop_invalid_device_token(RuntimeError("NOT_PAIRED from gateway"))
    assert not path.exists()
    assert client._device_token == ""


def test_maybe_drop_invalid_device_token_no_op_on_unrelated_exc(tmp_path: Path) -> None:
    """An unrelated exception must not touch the persisted token."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    client = _make_client_in(data_dir)
    client._device_token = "stale"
    path = client._config.device_token_path
    path.write_text("stale")
    client._maybe_drop_invalid_device_token(RuntimeError("WS_CLOSED"))
    assert path.exists()
    assert client._device_token == "stale"


def test_maybe_drop_invalid_device_token_no_op_when_empty(tmp_path: Path) -> None:
    """No-op when device_token is empty (nothing to drop)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    client = _make_client_in(data_dir)
    client._device_token = ""
    client._maybe_drop_invalid_device_token(RuntimeError("NOT_PAIRED"))
    assert client._device_token == ""


def test_maybe_drop_invalid_device_token_no_op_when_using_pairing_token(
    tmp_path: Path,
) -> None:
    """No-op when device_token already equals pairing_token."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config = NodeConfig(
        addon_mode=False,
        gateway_url="wss://gw.test/ws",
        pairing_token="pair-tok",
        node_name="t",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=data_dir,
    )
    identity = generate_identity()
    client = GatewayClient(config=config, identity=identity, device_token="pair-tok")
    client._maybe_drop_invalid_device_token(RuntimeError("NOT_PAIRED"))
    assert client._device_token == "pair-tok"


def test_maybe_drop_invalid_device_token_unlink_oserror(tmp_path: Path) -> None:
    """OSError during unlink is logged, not raised."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    client = _make_client_in(data_dir)
    client._device_token = "stale"
    path = client._config.device_token_path
    path.write_text("stale")
    with patch.object(Path, "unlink", side_effect=OSError("disk full")):
        client._maybe_drop_invalid_device_token(RuntimeError("NOT_PAIRED"))
    assert client._device_token == ""


def test_token_path_safe_to_unlink_resolve_oserror(tmp_path: Path) -> None:
    """OSError during path.resolve returns False."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    client = _make_client_in(data_dir)
    bad_path = data_dir / "device-token"
    with patch.object(Path, "resolve", side_effect=OSError("broken")):
        assert client._token_path_safe_to_unlink(bad_path) is False


def test_persist_device_token_fchmod_oserror_propagates(tmp_path: Path) -> None:
    """OSError from fchmod propagates and closes the fd."""
    import os as _os

    client = _make_client_in(tmp_path)
    with (
        patch.object(_os, "fchmod", side_effect=OSError("not supported")),
        pytest.raises(OSError, match="not supported"),
    ):
        client._persist_device_token("tok")


def test_persist_device_token_write_failure_cleans_tmp(tmp_path: Path) -> None:
    """If the write fails, the temp file is cleaned up."""
    client = _make_client_in(tmp_path)
    with (
        patch("os.fdopen", side_effect=OSError("write failed")),
        pytest.raises(OSError, match="write failed"),
    ):
        client._persist_device_token("tok")
    tmp = tmp_path / "device-token.tmp"
    assert not tmp.exists()


def test_persist_device_token_closes_fd_when_fdopen_fails(tmp_path: Path) -> None:
    """If os.fdopen raises before taking ownership, the raw fd is closed."""
    import os as _os

    client = _make_client_in(tmp_path)
    # Use a real fd from the OS so os.close on it actually works (and would
    # surface as an OSError on double-close if we got the bookkeeping wrong).
    read_fd, write_fd = _os.pipe()
    try:
        closed_fds: list[int] = []
        real_close = _os.close

        def _tracking_close(fd: int) -> None:
            closed_fds.append(fd)
            real_close(fd)

        with (
            patch("os.open", return_value=read_fd),
            patch("os.fchmod"),
            patch("os.fdopen", side_effect=OSError("fdopen failed")),
            patch("os.close", side_effect=_tracking_close),
            pytest.raises(OSError, match="fdopen failed"),
        ):
            client._persist_device_token("tok")
        assert read_fd in closed_fds, "fd from os.open must be closed when fdopen fails"
    finally:
        # Clean up the write end of the pipe (read end was closed by the test).
        with contextlib.suppress(OSError):
            _os.close(write_fd)


def test_persist_device_token_replace_failure_cleans_tmp(tmp_path: Path) -> None:
    """If replace fails, the temp file is cleaned up."""
    client = _make_client_in(tmp_path)
    with (
        patch.object(Path, "replace", side_effect=OSError("replace failed")),
        pytest.raises(OSError, match="replace failed"),
    ):
        client._persist_device_token("tok")
    tmp = tmp_path / "device-token.tmp"
    assert not tmp.exists()
