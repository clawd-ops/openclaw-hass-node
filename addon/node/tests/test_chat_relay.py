"""Tests for the chat-surface relay (P5.12)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openclaw_node.chat_relay import (
    _SESSION_KEY_PREFIX,
    ChatRelay,
    ChatRelayError,
    StreamKeepalive,
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


def _session_message_event(session_key: str, role: str, text: str) -> dict[str, Any]:
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
        relay.handle_event(_session_message_event(session_key, "assistant", "Hello from Clawd!"))

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
async def test_relay_turn_waits_for_final_not_first_delta() -> None:
    """Regression for #118 part 2: gateway streams the assistant reply as
    state='delta' chat events followed by a single state='final' event with
    the complete text. The relay must wait for the final event, NOT wake on
    the first delta (which truncates the reply to whatever short prefix was
    in the first chunk). Symptom: "Hey" worked because short, "I'm Cl" /
    "Not" truncated mid-word on a15."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "01KVH92VMPH2FQYTEFZSRWDM40"
    canonical_session_key = f"agent:clawd:{_SESSION_KEY_PREFIX}{conv_id.lower()}"

    async def _simulate_gateway() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))  # sessions.create
        await asyncio.sleep(0.01)
        relay.handle_response(
            _ok_response(
                sender.frames[1]["id"],
                {"subscribed": True, "key": canonical_session_key},
            )
        )
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[2]["id"]))  # chat.send ack
        await asyncio.sleep(0.01)
        # Streaming deltas — must NOT wake the reply waiter mid-stream.
        for partial in ["I'm", "I'm Clawd", "I'm Clawd, Rob's"]:
            relay.handle_event(
                {
                    "type": "event",
                    "event": "chat",
                    "payload": {
                        "sessionKey": canonical_session_key,
                        "state": "delta",
                        "message": {"role": "assistant", "content": partial},
                    },
                }
            )
            await asyncio.sleep(0.005)
        # Terminal event with the COMPLETE text — this is what the relay
        # should return.
        relay.handle_event(
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "sessionKey": canonical_session_key,
                    "state": "final",
                    "message": {
                        "role": "assistant",
                        "content": "I'm Clawd, Rob's AI assistant.",
                    },
                },
            }
        )

    task = asyncio.create_task(_simulate_gateway())
    reply = await relay.relay_turn(conv_id, "who are you?")
    await task

    # Must return the FINAL text, not the first delta.
    assert reply == "I'm Clawd, Rob's AI assistant."


@pytest.mark.asyncio
async def test_stream_turn_yields_deltas_and_closes_on_final() -> None:
    """stream_turn yields each chunk as deltas arrive, closes on state='final'."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "01KVH_STREAM_OK"
    canonical_session_key = f"agent:clawd:{_SESSION_KEY_PREFIX}{conv_id.lower()}"

    async def _drive() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))  # sessions.create
        await asyncio.sleep(0.01)
        relay.handle_response(
            _ok_response(
                sender.frames[1]["id"],
                {"subscribed": True, "key": canonical_session_key},
            )
        )
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[2]["id"]))  # chat.send ack
        await asyncio.sleep(0.005)
        for delta in ["Hello", " there", "!"]:
            relay.handle_event(
                {
                    "type": "event",
                    "event": "chat",
                    "payload": {
                        "sessionKey": canonical_session_key,
                        "state": "delta",
                        "deltaText": delta,
                        "message": {"role": "assistant", "content": "ignored"},
                    },
                }
            )
            await asyncio.sleep(0.005)
        relay.handle_event(
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "sessionKey": canonical_session_key,
                    "state": "final",
                    "message": {"role": "assistant", "content": "Hello there!"},
                },
            }
        )

    chunks: list[str | StreamKeepalive] = []

    async def _consume() -> None:
        async for chunk in relay.stream_turn(conv_id, "hi"):
            chunks.append(chunk)

    await asyncio.gather(_consume(), _drive())

    assert chunks == ["Hello", " there", "!"]


@pytest.mark.asyncio
async def test_stream_turn_session_message_yields_full_text_when_no_deltas() -> None:
    """session.message events without preceding deltas yield the full text as one chunk."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "01KVH_STREAM_SESSION_ONLY"
    canonical_session_key = f"agent:clawd:{_SESSION_KEY_PREFIX}{conv_id.lower()}"

    async def _drive() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.01)
        relay.handle_response(
            _ok_response(
                sender.frames[1]["id"],
                {"subscribed": True, "key": canonical_session_key},
            )
        )
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[2]["id"]))
        await asyncio.sleep(0.005)
        # No deltas, just a single session.message terminal event.
        relay.handle_event(_session_message_event(canonical_session_key, "assistant", "Done."))

    chunks: list[str | StreamKeepalive] = []

    async def _consume() -> None:
        async for chunk in relay.stream_turn(conv_id, "hi"):
            chunks.append(chunk)

    await asyncio.gather(_consume(), _drive())

    assert chunks == ["Done."]


@pytest.mark.asyncio
async def test_stream_turn_terminal_yields_tail_when_deltas_partial() -> None:
    """If the final's full text is longer than the sum of deltas, the tail is yielded."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "01KVH_STREAM_TAIL"
    canonical_session_key = f"agent:clawd:{_SESSION_KEY_PREFIX}{conv_id.lower()}"

    async def _drive() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.01)
        relay.handle_response(
            _ok_response(
                sender.frames[1]["id"],
                {"subscribed": True, "key": canonical_session_key},
            )
        )
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[2]["id"]))
        await asyncio.sleep(0.005)
        relay.handle_event(
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "sessionKey": canonical_session_key,
                    "state": "delta",
                    "deltaText": "Hello",
                    "message": {"role": "assistant", "content": "Hello"},
                },
            }
        )
        await asyncio.sleep(0.005)
        # Final has more text than the deltas covered.
        relay.handle_event(
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "sessionKey": canonical_session_key,
                    "state": "final",
                    "message": {"role": "assistant", "content": "Hello, world!"},
                },
            }
        )

    chunks: list[str | StreamKeepalive] = []

    async def _consume() -> None:
        async for chunk in relay.stream_turn(conv_id, "hi"):
            chunks.append(chunk)

    await asyncio.gather(_consume(), _drive())

    assert chunks == ["Hello", ", world!"]


@pytest.mark.asyncio
async def test_stream_turn_timeout_returns_drained_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the turn deadline elapses while waiting, any chunks already
    queued must still be yielded before the iterator closes."""
    import openclaw_node.chat_relay as cr_mod

    monkeypatch.setattr(cr_mod, "_STREAM_TURN_TIMEOUT_S", 0.1)
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "01KVH_STREAM_TIMEOUT"
    canonical_session_key = f"agent:clawd:{_SESSION_KEY_PREFIX}{conv_id.lower()}"

    async def _drive() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.01)
        relay.handle_response(
            _ok_response(
                sender.frames[1]["id"],
                {"subscribed": True, "key": canonical_session_key},
            )
        )
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[2]["id"]))
        await asyncio.sleep(0.005)
        # One delta arrives, then the stream times out without a final.
        relay.handle_event(
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "sessionKey": canonical_session_key,
                    "state": "delta",
                    "deltaText": "partial",
                    "message": {"role": "assistant", "content": "partial"},
                },
            }
        )

    chunks: list[str | StreamKeepalive] = []

    async def _consume() -> None:
        async for chunk in relay.stream_turn(conv_id, "hi"):
            chunks.append(chunk)

    await asyncio.gather(_consume(), _drive())

    assert chunks == ["partial"]


@pytest.mark.asyncio
async def test_stream_turn_chat_send_generic_exception_becomes_relay_failed() -> None:
    """Streaming variant of the generic-exception RELAY_FAILED path."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "01KVH_STREAM_FAIL"
    canonical_session_key = f"agent:clawd:{_SESSION_KEY_PREFIX}{conv_id.lower()}"

    async def _drive() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.01)
        relay.handle_response(
            _ok_response(
                sender.frames[1]["id"],
                {"subscribed": True, "key": canonical_session_key},
            )
        )
        await asyncio.sleep(0.01)
        send_frame_id = sender.frames[2]["id"]
        future = relay._pending.get(send_frame_id)
        if future is not None and not future.done():
            future.set_exception(RuntimeError("stream boom"))

    chunks: list[str | StreamKeepalive] = []

    async def _consume() -> None:
        async for chunk in relay.stream_turn(conv_id, "hi"):
            chunks.append(chunk)

    drive_task = asyncio.create_task(_drive())
    with pytest.raises(ChatRelayError) as exc_info:
        await _consume()
    await drive_task

    assert exc_info.value.code == "RELAY_FAILED"


@pytest.mark.asyncio
async def test_stream_turn_chat_send_chatrelayerror_propagates() -> None:
    """A ChatRelayError from chat.send propagates through stream_turn unchanged."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "01KVH_STREAM_RELAYERR"
    canonical_session_key = f"agent:clawd:{_SESSION_KEY_PREFIX}{conv_id.lower()}"

    async def _drive() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.01)
        relay.handle_response(
            _ok_response(
                sender.frames[1]["id"],
                {"subscribed": True, "key": canonical_session_key},
            )
        )
        await asyncio.sleep(0.01)
        relay.handle_response(_error_response(sender.frames[2]["id"], "RATE_LIMIT", "slow down"))

    async def _consume() -> None:
        async for _chunk in relay.stream_turn(conv_id, "hi"):
            pass

    drive_task = asyncio.create_task(_drive())
    with pytest.raises(ChatRelayError) as exc_info:
        await _consume()
    await drive_task

    assert exc_info.value.code == "RATE_LIMIT"


@pytest.mark.asyncio
async def test_relay_turn_chat_send_generic_exception_becomes_relay_failed() -> None:
    """A non-ChatRelayError raised from chat.send becomes RELAY_FAILED."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "01KVH_RELAY_FAIL"
    canonical_session_key = f"agent:clawd:{_SESSION_KEY_PREFIX}{conv_id.lower()}"

    async def _drive() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.01)
        relay.handle_response(
            _ok_response(
                sender.frames[1]["id"],
                {"subscribed": True, "key": canonical_session_key},
            )
        )
        await asyncio.sleep(0.01)
        # Simulate the pending chat.send future raising a generic exception.
        send_frame_id = sender.frames[2]["id"]
        future = relay._pending.get(send_frame_id)
        if future is not None and not future.done():
            future.set_exception(RuntimeError("boom"))

    task = asyncio.create_task(_drive())
    with pytest.raises(ChatRelayError) as exc_info:
        await relay.relay_turn(conv_id, "hi")
    await task

    assert exc_info.value.code == "RELAY_FAILED"


@pytest.mark.asyncio
async def test_stream_turn_reset_raises_disconnected() -> None:
    """reset() mid-stream must raise DISCONNECTED, not finish cleanly.

    A clean iterator close would let the http_api layer emit
    ``{"done": true}``, which the HACS shim treats as a successful turn —
    so a gateway disconnect would silently commit the partial reply as a
    finished assistant message. Reset must surface as an error.
    """
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "01KVH_STREAM_RESET"
    canonical_session_key = f"agent:clawd:{_SESSION_KEY_PREFIX}{conv_id.lower()}"

    async def _drive() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.01)
        relay.handle_response(
            _ok_response(
                sender.frames[1]["id"],
                {"subscribed": True, "key": canonical_session_key},
            )
        )
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[2]["id"]))
        await asyncio.sleep(0.005)
        # Yield ONE delta, then disconnect mid-stream.
        relay.handle_event(
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "sessionKey": canonical_session_key,
                    "state": "delta",
                    "deltaText": "partial",
                    "message": {"role": "assistant", "content": "partial"},
                },
            }
        )
        await asyncio.sleep(0.01)
        relay.reset()

    chunks: list[str | StreamKeepalive] = []
    raised: list[ChatRelayError] = []

    async def _consume() -> None:
        try:
            async for chunk in relay.stream_turn(conv_id, "hi"):
                chunks.append(chunk)
        except ChatRelayError as exc:
            raised.append(exc)

    await asyncio.gather(_consume(), _drive())

    assert chunks == ["partial"]
    assert len(raised) == 1
    assert raised[0].code == "DISCONNECTED"


@pytest.mark.asyncio
async def test_stream_turn_emits_keepalive_during_silent_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#129 follow-up: on tool-heavy turns the gateway can go silent for
    tens of seconds between chat.send ack and the first assistant delta.
    HA Assist's ~30s read timeout closes the stream during that gap and
    the user sees 'node not responding' even though the real reply
    arrives later. The relay emits one short visible progress delta
    after a configurable silence window, then transport-only keepalive
    sentinels so HA's read timer resets and the stream stays alive long
    enough for the real reply.
    """
    from openclaw_node import chat_relay as _cr

    monkeypatch.setattr(_cr, "_STREAM_FIRST_KEEPALIVE_S", 0.05)
    monkeypatch.setattr(_cr, "_STREAM_KEEPALIVE_INTERVAL_S", 0.05)

    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "01KVH_KEEPALIVE_GAP"
    canonical_session_key = f"agent:clawd:{_SESSION_KEY_PREFIX}{conv_id.lower()}"

    async def _drive() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.005)
        relay.handle_response(
            _ok_response(
                sender.frames[1]["id"],
                {"subscribed": True, "key": canonical_session_key},
            )
        )
        await asyncio.sleep(0.005)
        relay.handle_response(_ok_response(sender.frames[2]["id"], {"runId": "run-slow"}))
        # Long silence — simulates the gateway running tool calls
        # before generating any assistant text.
        await asyncio.sleep(0.20)
        relay.handle_event(
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "sessionKey": canonical_session_key,
                    "state": "delta",
                    "deltaText": "Real reply",
                    "runId": "run-slow",
                    "message": {"role": "assistant", "content": "Real reply"},
                },
            }
        )
        await asyncio.sleep(0.005)
        relay.handle_event(
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "sessionKey": canonical_session_key,
                    "state": "final",
                    "runId": "run-slow",
                    "message": {"role": "assistant", "content": "Real reply"},
                },
            }
        )

    chunks: list[str | StreamKeepalive] = []

    async def _consume() -> None:
        async for chunk in relay.stream_turn(conv_id, "long question"):
            chunks.append(chunk)

    await asyncio.gather(_consume(), _drive())

    progress_idxs = [i for i, c in enumerate(chunks) if c == "Working on it...\n\n"]
    keepalive_idxs = [i for i, c in enumerate(chunks) if isinstance(c, StreamKeepalive)]
    real_idxs = [i for i, c in enumerate(chunks) if c == "Real reply"]
    assert progress_idxs == [0], (
        f"expected one visible progress delta during silent gap, got: {chunks!r}"
    )
    assert keepalive_idxs, (
        f"expected at least one StreamKeepalive sentinel during silent gap, got: {chunks!r}"
    )
    assert real_idxs == [chunks.index("Real reply")], f"real reply lost: {chunks!r}"
    # Progress + keepalive(s) precede the real reply.
    assert progress_idxs[0] < keepalive_idxs[0] < real_idxs[0]
    # The only visible status text is the single initial progress delta;
    # repeated keepalives stay transport-only.
    str_chunks = [c for c in chunks if isinstance(c, str)]
    assert str_chunks == ["Working on it...\n\n", "Real reply"], chunks


@pytest.mark.asyncio
async def test_stream_turn_no_keepalive_on_fast_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fast turns (deltas streaming continuously) must NOT see any
    keepalive deltas — they would be noise in HA's chat UI.
    """
    from openclaw_node import chat_relay as _cr

    monkeypatch.setattr(_cr, "_STREAM_FIRST_KEEPALIVE_S", 5.0)
    monkeypatch.setattr(_cr, "_STREAM_KEEPALIVE_INTERVAL_S", 5.0)

    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "01KVH_FAST_TURN"
    canonical_session_key = f"agent:clawd:{_SESSION_KEY_PREFIX}{conv_id.lower()}"

    async def _drive() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.005)
        relay.handle_response(
            _ok_response(
                sender.frames[1]["id"],
                {"subscribed": True, "key": canonical_session_key},
            )
        )
        await asyncio.sleep(0.005)
        relay.handle_response(_ok_response(sender.frames[2]["id"], {"runId": "run-fast"}))
        await asyncio.sleep(0.005)
        for delta in ["Hi", " there", "!"]:
            relay.handle_event(
                {
                    "type": "event",
                    "event": "chat",
                    "payload": {
                        "sessionKey": canonical_session_key,
                        "state": "delta",
                        "deltaText": delta,
                        "runId": "run-fast",
                        "message": {"role": "assistant", "content": "ignored"},
                    },
                }
            )
            await asyncio.sleep(0.005)
        relay.handle_event(
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "sessionKey": canonical_session_key,
                    "state": "final",
                    "runId": "run-fast",
                    "message": {"role": "assistant", "content": "Hi there!"},
                },
            }
        )

    chunks: list[str | StreamKeepalive] = []

    async def _consume() -> None:
        async for chunk in relay.stream_turn(conv_id, "hi"):
            chunks.append(chunk)

    await asyncio.gather(_consume(), _drive())

    assert chunks == ["Hi", " there", "!"], f"keepalive leaked into fast turn: {chunks!r}"


@pytest.mark.asyncio
async def test_relay_turn_delta_before_ack_does_not_truncate() -> None:
    """Codex review catch on #118 part 2: a partial delta arriving BEFORE
    the chat.send ack lands must not short-circuit the fast-path return.
    Without separate terminal-text tracking, the post-ack check sees the
    delta in _last_assistant_text and returns it as the reply."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "01KVH9DELTAFIRST"
    canonical_session_key = f"agent:clawd:{_SESSION_KEY_PREFIX}{conv_id.lower()}"

    async def _simulate_gateway() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.01)
        relay.handle_response(
            _ok_response(
                sender.frames[1]["id"],
                {"subscribed": True, "key": canonical_session_key},
            )
        )
        await asyncio.sleep(0.01)
        # Streaming delta BEFORE chat.send ack arrives.
        relay.handle_event(
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "sessionKey": canonical_session_key,
                    "state": "delta",
                    "message": {"role": "assistant", "content": "OK"},
                },
            }
        )
        await asyncio.sleep(0.005)
        # NOW the chat.send ack.
        relay.handle_response(_ok_response(sender.frames[2]["id"]))
        await asyncio.sleep(0.005)
        # Then the terminal event with full text.
        relay.handle_event(
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "sessionKey": canonical_session_key,
                    "state": "final",
                    "message": {
                        "role": "assistant",
                        "content": "OK, here is the full answer.",
                    },
                },
            }
        )

    task = asyncio.create_task(_simulate_gateway())
    reply = await relay.relay_turn(conv_id, "Q")
    await task

    assert reply == "OK, here is the full answer."


@pytest.mark.asyncio
async def test_relay_turn_uses_canonical_key_from_subscribe_response() -> None:
    """Regression for #118: the gateway's subscribe response carries a
    canonical sessionKey (e.g. ``agent:clawd:ha-assist:...`` lowercased) and
    subsequent events arrive under that canonical form, NOT the raw key the
    addon sent. The relay must capture the canonical key and use it for
    internal state, otherwise the receive-side lookup fails and every turn
    times out with NO_REPLY even though the agent replied."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "01KVH867BX32BYWJBQVGDCCG0V"
    canonical_session_key = f"agent:clawd:{_SESSION_KEY_PREFIX}{conv_id.lower()}"

    async def _simulate_gateway() -> None:
        await asyncio.sleep(0.01)
        # sessions.create
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.01)
        # sessions.messages.subscribe — RESPONSE carries canonical key
        sub_frame = sender.frames[1]
        assert sub_frame["method"] == "sessions.messages.subscribe"
        relay.handle_response(
            _ok_response(
                sub_frame["id"],
                {"subscribed": True, "key": canonical_session_key},
            )
        )
        await asyncio.sleep(0.01)
        # Event arrives under the canonical key (NOT the raw key).
        relay.handle_event(
            _session_message_event(canonical_session_key, "assistant", "Reply via canonical key")
        )
        relay.handle_response(_ok_response(sender.frames[2]["id"]))

    task = asyncio.create_task(_simulate_gateway())
    reply = await relay.relay_turn(conv_id, "hey")
    await task

    assert reply == "Reply via canonical key"


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
        relay.handle_event(_session_message_event(session_key, "assistant", "First reply"))
        relay.handle_response(_ok_response(sender.frames[2]["id"]))

    task = asyncio.create_task(_first())
    await relay.relay_turn(conv_id, "Turn 1")
    await task

    first_count = len(sender.frames)
    assert first_count == 3

    # Second turn -- only chat.send, no create/subscribe
    async def _second() -> None:
        await asyncio.sleep(0.01)
        relay.handle_event(_session_message_event(session_key, "assistant", "Second reply"))
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
        relay.handle_response(_error_response(sender.frames[2]["id"], "SESSION_LOCKED", "busy"))

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
    relay.handle_event(_session_message_event("ha-assist:x", "user", "user text"))
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

    relay._reply_events[key] = asyncio.Event()
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
        relay.handle_response(_error_response(sender.frames[0]["id"], "ALREADY_EXISTS", "exists"))
        await asyncio.sleep(0.01)
        # subscribe succeeds
        relay.handle_response(_ok_response(sender.frames[1]["id"]))
        await asyncio.sleep(0.01)
        # chat.send succeeds
        relay.handle_event(_session_message_event(session_key, "assistant", "Still works"))
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

    relay.handle_response(
        {
            "type": "res",
            "id": "req-legacy",
            "ok": False,
            "error": "SOME_ERROR",
        }
    )

    with pytest.raises(ChatRelayError) as exc_info:
        future.result()
    assert exc_info.value.code == "SOME_ERROR"


@pytest.mark.asyncio
async def test_handle_event_chat_family() -> None:
    """Chat events (not just session.message) are captured."""
    relay = ChatRelay(FakeSender().send)
    relay._reply_events["ha-assist:chat-ev"] = asyncio.Event()
    relay.handle_event(
        {
            "type": "event",
            "event": "chat",
            "payload": {
                "sessionKey": "ha-assist:chat-ev",
                "role": "assistant",
                "message": "Chat family reply",
            },
        }
    )
    assert relay._last_assistant_text["ha-assist:chat-ev"] == "Chat family reply"


@pytest.mark.asyncio
async def test_handle_event_nested_message_object() -> None:
    """Nested message object in session.message is handled."""
    relay = ChatRelay(FakeSender().send)
    relay._reply_events["ha-assist:nested"] = asyncio.Event()
    relay.handle_event(
        {
            "type": "event",
            "event": "session.message",
            "payload": {
                "sessionKey": "ha-assist:nested",
                "role": "assistant",
                "message": {"content": "Nested content", "role": "assistant"},
            },
        }
    )
    assert relay._last_assistant_text["ha-assist:nested"] == "Nested content"


@pytest.mark.asyncio
async def test_handle_event_chat_nested_message() -> None:
    """Chat event with nested message object is handled."""
    relay = ChatRelay(FakeSender().send)
    relay._reply_events["ha-assist:chat-nested"] = asyncio.Event()
    relay.handle_event(
        {
            "type": "event",
            "event": "chat.delta",
            "payload": {
                "sessionKey": "ha-assist:chat-nested",
                "message": {
                    "role": "assistant",
                    "content": "Chat nested content",
                },
            },
        }
    )
    assert relay._last_assistant_text["ha-assist:chat-nested"] == "Chat nested content"


@pytest.mark.asyncio
async def test_deferred_reply_via_event() -> None:
    """Reply arrives after chat.send ack (deferred reply flow)."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "conv-deferred"
    session_key = f"{_SESSION_KEY_PREFIX}{conv_id}"

    async def _respond() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[1]["id"]))
        await asyncio.sleep(0.01)
        # chat.send ack comes back immediately (no reply yet)
        relay.handle_response(_ok_response(sender.frames[2]["id"], {"runId": "run-123"}))
        # Reply arrives later via event
        await asyncio.sleep(0.05)
        relay.handle_event(_session_message_event(session_key, "assistant", "Deferred reply!"))

    task = asyncio.create_task(_respond())
    reply = await relay.relay_turn(conv_id, "Hello")
    await task

    assert reply == "Deferred reply!"


@pytest.mark.asyncio
async def test_reply_event_signals_on_assistant_message() -> None:
    """The reply event is set when an assistant message arrives."""
    relay = ChatRelay(FakeSender().send)
    key = "ha-assist:signal"

    evt = asyncio.Event()
    relay._reply_events[key] = evt

    relay.handle_event(_session_message_event(key, "assistant", "Reply!"))
    assert evt.is_set()
    assert relay._last_assistant_text[key] == "Reply!"


@pytest.mark.asyncio
async def test_create_session_non_benign_error_raises() -> None:
    """Non-ALREADY_EXISTS create errors propagate."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    async def _respond() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_error_response(sender.frames[0]["id"], "UNAUTHORIZED", "bad token"))

    task = asyncio.create_task(_respond())
    with pytest.raises(ChatRelayError) as exc_info:
        await relay.relay_turn("conv-unauth", "Hello")
    await task

    assert exc_info.value.code == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_content_block_array() -> None:
    """Content as array of text blocks is extracted correctly."""
    relay = ChatRelay(FakeSender().send)
    relay._reply_events["ha-assist:blocks"] = asyncio.Event()
    relay.handle_event(
        {
            "type": "event",
            "event": "session.message",
            "payload": {
                "sessionKey": "ha-assist:blocks",
                "role": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Hello"},
                        {"type": "text", "text": "world"},
                    ],
                },
            },
        }
    )
    assert relay._last_assistant_text["ha-assist:blocks"] == "Hello\nworld"


@pytest.mark.asyncio
async def test_stale_run_id_ignored() -> None:
    """Events with a mismatched runId are ignored."""
    relay = ChatRelay(FakeSender().send)
    key = "ha-assist:stale"

    relay._reply_events[key] = asyncio.Event()
    relay._active_run_id[key] = "run-current"

    relay.handle_event(
        {
            "type": "event",
            "event": "session.message",
            "payload": {
                "sessionKey": key,
                "role": "assistant",
                "message": "Stale reply",
                "runId": "run-old",
            },
        }
    )
    assert key not in relay._last_assistant_text

    relay.handle_event(
        {
            "type": "event",
            "event": "session.message",
            "payload": {
                "sessionKey": key,
                "role": "assistant",
                "message": "Current reply",
                "runId": "run-current",
            },
        }
    )
    assert relay._last_assistant_text[key] == "Current reply"


@pytest.mark.asyncio
async def test_concurrent_turns_serialized() -> None:
    """Concurrent turns for the same conversation are serialized."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "conv-serial"
    session_key = f"{_SESSION_KEY_PREFIX}{conv_id}"

    order: list[str] = []

    async def _respond_all() -> None:
        await asyncio.sleep(0.01)
        # First turn: create
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.01)
        # First turn: subscribe
        relay.handle_response(_ok_response(sender.frames[1]["id"]))
        await asyncio.sleep(0.01)
        # First turn: chat.send reply + ack
        relay.handle_event(_session_message_event(session_key, "assistant", "Reply 1"))
        relay.handle_response(_ok_response(sender.frames[2]["id"]))
        order.append("turn1_done")

        await asyncio.sleep(0.05)
        # Second turn: chat.send (no create/subscribe)
        idx = len(sender.frames) - 1
        relay.handle_event(_session_message_event(session_key, "assistant", "Reply 2"))
        relay.handle_response(_ok_response(sender.frames[idx]["id"]))
        order.append("turn2_done")

    task = asyncio.create_task(_respond_all())
    t1 = asyncio.create_task(relay.relay_turn(conv_id, "Turn 1"))
    t2 = asyncio.create_task(relay.relay_turn(conv_id, "Turn 2"))

    r1 = await t1
    r2 = await t2
    await task

    assert r1 == "Reply 1"
    assert r2 == "Reply 2"
    assert order == ["turn1_done", "turn2_done"]


@pytest.mark.asyncio
async def test_pre_ack_event_with_new_run_id() -> None:
    """Pre-ack event with a new runId on turn 2 is not rejected as stale."""
    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "conv-preack"
    session_key = f"{_SESSION_KEY_PREFIX}{conv_id}"

    # Turn 1: sets _active_run_id to "run-old"
    async def _first() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[1]["id"]))
        await asyncio.sleep(0.01)
        relay.handle_event(_session_message_event(session_key, "assistant", "T1"))
        relay.handle_response(_ok_response(sender.frames[2]["id"], {"runId": "run-old"}))

    task = asyncio.create_task(_first())
    await relay.relay_turn(conv_id, "Turn 1")
    await task

    assert relay._active_run_id.get(session_key) == "run-old"

    # Turn 2: event with run-new arrives BEFORE chat.send ack
    async def _second() -> None:
        await asyncio.sleep(0.01)
        # Pre-ack event with new runId (active_run_id cleared at turn start)
        relay.handle_event(
            {
                "type": "event",
                "event": "session.message",
                "payload": {
                    "sessionKey": session_key,
                    "role": "assistant",
                    "message": "T2 reply",
                    "runId": "run-new",
                },
            }
        )
        send_frame = sender.frames[3]
        relay.handle_response(_ok_response(send_frame["id"], {"runId": "run-new"}))

    task = asyncio.create_task(_second())
    reply = await relay.relay_turn(conv_id, "Turn 2")
    await task

    assert reply == "T2 reply"


@pytest.mark.asyncio
async def test_no_reply_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing reply raises ChatRelayError instead of returning empty string."""
    import openclaw_node.chat_relay as _mod

    monkeypatch.setattr(_mod, "_TURN_TIMEOUT_S", 0.1)

    sender = FakeSender()
    relay = ChatRelay(sender.send)

    conv_id = "conv-noreply"

    async def _respond() -> None:
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[0]["id"]))
        await asyncio.sleep(0.01)
        relay.handle_response(_ok_response(sender.frames[1]["id"]))
        await asyncio.sleep(0.01)
        # chat.send ack but no assistant event ever arrives
        relay.handle_response(_ok_response(sender.frames[2]["id"]))

    task = asyncio.create_task(_respond())
    with pytest.raises(ChatRelayError) as exc_info:
        await relay.relay_turn(conv_id, "Hello")
    await task

    assert exc_info.value.code == "NO_REPLY"


@pytest.mark.asyncio
async def test_rpc_send_failure_cleans_pending() -> None:
    """If the send callback raises, pending future is cleaned up."""

    async def _broken_send(frame: dict[str, Any]) -> None:
        raise ConnectionError("WS closed")

    relay = ChatRelay(_broken_send)

    with pytest.raises(ConnectionError):
        await relay._rpc("test.method", {})

    assert len(relay._pending) == 0


@pytest.mark.asyncio
async def test_event_ignored_when_no_turn_active() -> None:
    """Assistant events are ignored when no turn is waiting for a reply."""
    relay = ChatRelay(FakeSender().send)
    key = "ha-assist:no-turn"

    relay.handle_event(_session_message_event(key, "assistant", "Stale!"))
    assert key not in relay._last_assistant_text
