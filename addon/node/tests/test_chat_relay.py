"""Tests for the chat-surface relay (P5.12)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from openclaw_node.chat_relay import (
    ChatRelay,
    ChatRelayError,
    _SESSION_KEY_PREFIX,
)


class FakeSender:
    """Captures frames sent via the relay's send callback."""

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def send(self, frame: dict[str, Any]) -> None:
        """Record a sent frame."""
        self.frames.append(frame)


def _ok_response(req_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"type": "res", "id": req_id, "ok": True, "payload": payload or {}}


def _error_response(req_id: str, code: str, message: str = "") -> dict[str, Any]:
    return {
        "type": "res",
        "id": req_id,
        "ok": False,
        "error": {"code": code, "message": message},
    }


def _session_message_event(
    session_key: str, role: str, text: str
) -> dict[str, Any]:
    return {
        "type": "event",
        "event": "session.message",
        "payload": {
            "sessionKey": session_key,
            "role": role,
            "message": text,
        },
    }


@pytest.mark.asyncio
async def test_relay_turn_success() -> None:
    """Full relay turn: create, subscribe, send, get reply."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "test-conv-001"
    session_key = f"{_SESSION_KEY_PREFIX}{conv_id}"

    async def _simulate_gateway() -> None:
        await asyncio.sleep(0.01)

        # Respond to sessions.create
        create_frame = sender.frames[0]
        assert create_frame["method"] == "sessions.create"
        relay.handle_response(_ok_response(create_frame["id"]))

        await asyncio.sleep(0.01)

        # Respond to sessions.messages.subscribe
        sub_frame = sender.frames[1]
        assert sub_frame["method"] == "sessions.messages.subscribe"
        relay.handle_response(_ok_response(sub_frame["id"]))

        await asyncio.sleep(0.01)

        # Simulate session.message event (assistant reply) before chat.send response
        relay.handle_event(
            _session_message_event(session_key, "assistant", "Hello from Clawd!")
        )

        # Respond to chat.send
        send_frame = sender.frames[2]
        assert send_frame["method"] == "chat.send"
        relay.handle_response(_ok_response(send_frame["id"]))

    task = asyncio.create_task(_simulate_gateway())
    reply = await relay.relay_turn(conv_id, "Hello")
    await task

    assert reply == "Hello from Clawd!"
    assert len(sender.frames) == 3


@pytest.mark.asyncio
async def test_relay_turn_second_turn_skips_create() -> None:
    """Second turn in the same conversation skips session create."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "conv-002"
    session_key = f"{_SESSION_KEY_PREFIX}{conv_id}"

    # First turn
    async def _first() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[1]["id"]))
        await asyncio.sleep(0.01)
        relay.handle_event(
            _session_message_event(session_key, "assistant", "First reply")
        )
        relay.handle_response(_ok_response(sender.frames[2]["id"]))

    task = asyncio.create_task(_first())
    await relay.relay_turn(conv_id, "Turn 1")
    await task

    first_count = len(sender.frames)
    assert first_count == 3

    # Second turn -- only chat.send, no create/subscribe
    async def _second() -> None:
        await asyncio.sleep(0.01)
        relay.handle_event(
            _session_message_event(session_key, "assistant", "Second reply")
        )
        send_frame = sender.frames[first_count]
        assert send_frame["method"] == "chat.send"
        relay.handle_response(_ok_response(send_frame["id"]))

    task = asyncio.create_task(_second())
    reply = await relay.relay_turn(conv_id, "Turn 2")
    await task

    assert reply == "Second reply"
    assert len(sender.frames) == first_count + 1


@pytest.mark.asyncio
async def test_rpc_timeout() -> None:
    """RPC times out when gateway never responds."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    with pytest.raises(ChatRelayError) as exc_info:
        await relay._rpc("test.method", {"key": "val"}, timeout=0.05)

    assert exc_info.value.code == "TIMEOUT"
    assert len(sender.frames) == 1
    assert sender.frames[0]["method"] == "test.method"


@pytest.mark.asyncio
async def test_relay_turn_gateway_error() -> None:
    """Gateway rejects chat.send with an error."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "conv-error"

    async def _respond() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[1]["id"]))
        await asyncio.sleep(0.01)
        relay.handle_response(
            _error_response(sender.frames[2]["id"], "SESSION_LOCKED", "busy")
        )

    task = asyncio.create_task(_respond())
    with pytest.raises(ChatRelayError) as exc_info:
        await relay.relay_turn(conv_id, "Hello")
    await task

    assert exc_info.value.code == "SESSION_LOCKED"


@pytest.mark.asyncio
async def test_handle_response_unknown_id() -> None:
    """Response with unknown ID returns False."""
    relay = ChatRelay(FakeSender().send)
    assert relay.handle_response(_ok_response("unknown-id")) is False


@pytest.mark.asyncio
async def test_handle_event_non_assistant_ignored() -> None:
    """Non-assistant session.message events are not captured."""
    relay = ChatRelay(FakeSender().send)
    relay.handle_event(
        _session_message_event("ha-assist:x", "user", "user text")
    )
    assert relay._last_assistant_text == {}


@pytest.mark.asyncio
async def test_handle_event_non_session_event_ignored() -> None:
    """Events that are not session.message are ignored."""
    relay = ChatRelay(FakeSender().send)
    relay.handle_event({"type": "event", "event": "tick", "payload": {}})
    assert relay._last_assistant_text == {}


@pytest.mark.asyncio
async def test_reset_clears_state() -> None:
    """Reset clears subscriptions, pending futures, and cached messages."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    relay._subscribed.add("ha-assist:x")
    relay._last_assistant_text["ha-assist:x"] = "cached"

    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()
    relay._pending["req-1"] = future

    relay.reset()

    assert len(relay._subscribed) == 0
    assert len(relay._last_assistant_text) == 0
    assert len(relay._pending) == 0
    with pytest.raises(ChatRelayError):
        future.result()


@pytest.mark.asyncio
async def test_handle_event_cumulative_snapshot() -> None:
    """Later assistant messages overwrite earlier ones for the same session."""
    relay = ChatRelay(FakeSender().send)
    key = "ha-assist:multi"

    relay.handle_event(_session_message_event(key, "assistant", "partial"))
    relay.handle_event(_session_message_event(key, "assistant", "partial response here"))

    assert relay._last_assistant_text[key] == "partial response here"


@pytest.mark.asyncio
async def test_create_session_already_exists() -> None:
    """If sessions.create returns an error, relay proceeds (session exists)."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "conv-existing"
    session_key = f"{_SESSION_KEY_PREFIX}{conv_id}"

    async def _respond() -> None:
        await asyncio.sleep(0.01)
        # sessions.create fails (ALREADY_EXISTS)
        relay.handle_response(
            _error_response(sender.frames[0]["id"], "ALREADY_EXISTS", "exists")
        )
        await asyncio.sleep(0.01)
        # subscribe succeeds
        relay.handle_response(_ok_response(sender.frames[1]["id"]))
        await asyncio.sleep(0.01)
        # chat.send succeeds
        relay.handle_event(
            _session_message_event(session_key, "assistant", "Still works")
        )
        relay.handle_response(_ok_response(sender.frames[2]["id"]))

    task = asyncio.create_task(_respond())
    reply = await relay.relay_turn(conv_id, "Test")
    await task

    assert reply == "Still works"


@pytest.mark.asyncio
async def test_handle_response_legacy_string_error() -> None:
    """Legacy string error format is handled."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()
    relay._pending["req-legacy"] = future

    relay.handle_response({
        "type": "res",
        "id": "req-legacy",
        "ok": False,
        "error": "SOME_ERROR",
    })

    with pytest.raises(ChatRelayError) as exc_info:
        future.result()
    assert exc_info.value.code == "SOME_ERROR"
