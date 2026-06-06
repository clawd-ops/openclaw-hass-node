"""Tests for openclaw_node.conversation_dispatcher."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openclaw_node.conversation_dispatcher import (
    ConversationDispatcher,
    ConversationDispatcherError,
)


async def test_forward_completes_when_result_arrives() -> None:
    """Awaiting forward() returns once handle_result fires for that id."""
    sent: list[dict[str, Any]] = []

    async def _send(req: dict[str, Any]) -> None:
        sent.append(req)

    d = ConversationDispatcher(send=_send, timeout_s=2.0)

    async def _later_reply() -> None:
        # Give forward() a moment to register the future.
        await asyncio.sleep(0.01)
        d.handle_result({"conversationId": sent[0]["conversationId"], "response": "hi"})

    reply_task = asyncio.create_task(_later_reply())
    result = await d.forward("hello", None, None)
    await reply_task
    assert result["response"] == "hi"


async def test_forward_passes_through_context() -> None:
    """forward() forwards conversation_id and language verbatim."""
    sent: list[dict[str, Any]] = []

    async def _send(req: dict[str, Any]) -> None:
        sent.append(req)
        d.handle_result({"conversationId": req["conversationId"], "response": "x"})

    d = ConversationDispatcher(send=_send, timeout_s=2.0)
    await d.forward("text", "conv-1", "en-US")
    assert sent[0]["text"] == "text"
    assert sent[0]["conversationContextId"] == "conv-1"
    assert sent[0]["language"] == "en-US"


async def test_forward_times_out() -> None:
    """forward() raises TIMEOUT when no reply arrives in time."""

    async def _send(req: dict[str, Any]) -> None:
        return None

    d = ConversationDispatcher(send=_send, timeout_s=0.05)
    with pytest.raises(ConversationDispatcherError) as ei:
        await d.forward("hi", None, None)
    assert ei.value.code == "TIMEOUT"
    assert d.pending_count == 0


async def test_forward_send_failure() -> None:
    """A failure in the inject send callback raises SEND_FAILED."""

    async def _bad_send(req: dict[str, Any]) -> None:
        raise RuntimeError("ws closed")

    d = ConversationDispatcher(send=_bad_send, timeout_s=2.0)
    with pytest.raises(ConversationDispatcherError) as ei:
        await d.forward("hi", None, None)
    assert ei.value.code == "SEND_FAILED"
    assert d.pending_count == 0


async def test_handle_result_for_unknown_id_is_safe() -> None:
    """An unsolicited reply does not raise."""

    async def _send(req: dict[str, Any]) -> None:
        return None

    d = ConversationDispatcher(send=_send)
    d.handle_result({"conversationId": "no-such-id"})


async def test_handle_result_on_completed_future_is_idempotent() -> None:
    """Double-replies on an already-completed future do not error."""
    sent: list[dict[str, Any]] = []

    async def _send(req: dict[str, Any]) -> None:
        sent.append(req)

    d = ConversationDispatcher(send=_send, timeout_s=2.0)

    async def _reply_twice() -> None:
        await asyncio.sleep(0.01)
        rid = sent[0]["conversationId"]
        d.handle_result({"conversationId": rid, "response": "a"})
        d.handle_result({"conversationId": rid, "response": "b"})  # ignored

    task = asyncio.create_task(_reply_twice())
    result = await d.forward("hi", None, None)
    await task
    assert result["response"] == "a"


async def test_cancel_all_fails_pending_with_disconnected() -> None:
    """cancel_all() rejects every outstanding future as DISCONNECTED."""
    sent: list[dict[str, Any]] = []

    async def _send(req: dict[str, Any]) -> None:
        sent.append(req)

    d = ConversationDispatcher(send=_send, timeout_s=5.0)

    forward_task = asyncio.create_task(d.forward("hi", None, None))
    await asyncio.sleep(0.01)
    assert d.pending_count == 1
    d.cancel_all("test disconnect")
    with pytest.raises(ConversationDispatcherError) as ei:
        await forward_task
    assert ei.value.code == "DISCONNECTED"
    assert "test disconnect" in ei.value.message
    assert d.pending_count == 0


async def test_cancel_all_safe_when_no_pending() -> None:
    """cancel_all() with no pending requests is a no-op."""

    async def _send(req: dict[str, Any]) -> None:
        return None

    d = ConversationDispatcher(send=_send)
    d.cancel_all()
    assert d.pending_count == 0
