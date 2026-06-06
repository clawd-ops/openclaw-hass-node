"""Tests for openclaw_gateway.server.GatewayServer."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from openclaw_gateway.brain import BrainError
from openclaw_gateway.server import GatewayServer


class _FakeWS:
    """Bare minimum ServerConnection look-alike for unit tests.

    Supplies an outbound ``send`` capture and an inbound queue of frames
    that the server iterates with ``async for raw in ws``.
    """

    def __init__(self) -> None:
        self.sent: list[str] = []
        self._in: list[str] = []
        self._closed = asyncio.Event()
        self._first_recv_done = asyncio.Event()

    def queue(self, *frames: dict[str, Any]) -> None:
        for f in frames:
            self._in.append(json.dumps(f))

    async def recv(self) -> str:
        # Used during handshake.
        return self._in.pop(0)

    async def send(self, data: str) -> None:
        self.sent.append(data)

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[str]:
        while self._in:
            yield self._in.pop(0)
            await asyncio.sleep(0)  # let other tasks run

    def sent_events(self, event_name: str) -> list[dict[str, Any]]:
        out = []
        for raw in self.sent:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("type") == "event" and msg.get("event") == event_name:
                out.append(msg["payload"])
        return out

    def sent_messages(self) -> list[dict[str, Any]]:
        return [json.loads(s) for s in self.sent]


def _server(model_responses: list[Any]) -> GatewayServer:
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(side_effect=model_responses)
    return GatewayServer(client)


def _text_resp(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


async def test_handshake_sends_challenge_and_response() -> None:
    server = _server([])
    ws = _FakeWS()
    ws.queue(
        {
            "type": "req",
            "id": "r1",
            "method": "connect",
            "params": {"device": {"id": "node-a"}},
        }
    )
    await server._send_challenge(ws)  # type: ignore[arg-type]
    await server._handshake(ws)  # type: ignore[arg-type]

    msgs = ws.sent_messages()
    assert msgs[0]["event"] == "connect.challenge"
    assert "nonce" in msgs[0]["payload"]
    assert msgs[1]["type"] == "res"
    assert msgs[1]["id"] == "r1"
    assert msgs[1]["ok"] is True


async def test_handshake_rejects_wrong_method() -> None:
    import pytest

    server = _server([])
    ws = _FakeWS()
    ws.queue({"type": "req", "id": "r1", "method": "other", "params": {}})
    with pytest.raises(ValueError, match="connect"):
        await server._handshake(ws)  # type: ignore[arg-type]


async def test_pending_pull_returns_empty_list() -> None:
    server = _server([])
    ws = _FakeWS()
    ws.queue({"type": "req", "id": "p1", "method": "node.pending.pull", "params": {}})
    from openclaw_gateway.invoke_dispatcher import InvokeDispatcher

    invoker = InvokeDispatcher(send=AsyncMock())
    await server._event_loop(ws, invoker)  # type: ignore[arg-type]
    res = next(m for m in ws.sent_messages() if m["type"] == "res")
    assert res["payload"] == {"items": []}


async def test_invoke_result_completes_pending_future() -> None:
    server = _server([])
    ws = _FakeWS()
    from openclaw_gateway.invoke_dispatcher import InvokeDispatcher

    invoker = InvokeDispatcher(send=AsyncMock(), timeout_s=2.0)

    task = asyncio.create_task(invoker.invoke("ha.x", {}))
    await asyncio.sleep(0.01)
    invoke_id = next(iter(invoker._pending))  # noqa: SLF001

    ws.queue(
        {
            "type": "req",
            "id": "r1",
            "method": "node.invoke.result",
            "params": {"invokeId": invoke_id, "ok": True, "result": "done"},
        }
    )
    await server._event_loop(ws, invoker)  # type: ignore[arg-type]
    out = await task
    assert out["ok"] is True
    assert out["result"] == "done"


async def test_conversation_request_runs_brain_and_emits_result() -> None:
    server = _server([_text_resp("hello back")])
    ws = _FakeWS()
    ws.queue(
        {
            "type": "req",
            "id": "c1",
            "method": "node.conversation.request",
            "params": {"conversationId": "conv-1", "text": "hi"},
        }
    )
    from openclaw_gateway.invoke_dispatcher import InvokeDispatcher

    invoker = InvokeDispatcher(send=AsyncMock())
    await server._event_loop(ws, invoker)  # type: ignore[arg-type]
    # _run_conversation runs as a background task; wait for it
    await asyncio.sleep(0.05)
    results = ws.sent_events("node.conversation.result")
    assert results
    assert results[0]["conversationId"] == "conv-1"
    assert results[0]["response"] == "hello back"


async def test_conversation_brain_failure_emits_error_payload() -> None:
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(side_effect=ConnectionError("api down"))
    server = GatewayServer(client)
    ws = _FakeWS()
    ws.queue(
        {
            "type": "req",
            "id": "c2",
            "method": "node.conversation.request",
            "params": {"conversationId": "conv-2", "text": "hi"},
        }
    )
    from openclaw_gateway.invoke_dispatcher import InvokeDispatcher

    invoker = InvokeDispatcher(send=AsyncMock())
    await server._event_loop(ws, invoker)  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    results = ws.sent_events("node.conversation.result")
    assert results
    assert results[0]["error"] == "MODEL_CALL_FAILED"


async def test_unknown_request_replies_not_ok() -> None:
    server = _server([])
    ws = _FakeWS()
    ws.queue({"type": "req", "id": "u1", "method": "something.weird", "params": {}})
    from openclaw_gateway.invoke_dispatcher import InvokeDispatcher

    invoker = InvokeDispatcher(send=AsyncMock())
    await server._event_loop(ws, invoker)  # type: ignore[arg-type]
    res = next(m for m in ws.sent_messages() if m["type"] == "res")
    assert res["ok"] is False


async def test_event_loop_drops_non_json_frames() -> None:
    server = _server([])
    ws = _FakeWS()
    ws._in.append("not json")  # noqa: SLF001
    ws._in.append(json.dumps({"type": "req", "id": "r", "method": "node.pending.pull"}))  # noqa: SLF001
    from openclaw_gateway.invoke_dispatcher import InvokeDispatcher

    invoker = InvokeDispatcher(send=AsyncMock())
    await server._event_loop(ws, invoker)  # type: ignore[arg-type]
    # The pending.pull reply still made it
    assert any(m["type"] == "res" for m in ws.sent_messages())


async def test_brain_error_attribute_carried_through_to_payload() -> None:
    """When brain raises BrainError, the error code is preserved in result."""

    class _RaisingClient:
        class _Messages:
            async def create(self, **kwargs: Any) -> Any:
                raise BrainError("PROTOCOL_ERROR", "bad shape")

        messages = _Messages()

    server = GatewayServer(_RaisingClient())  # type: ignore[arg-type]
    ws = _FakeWS()
    from openclaw_gateway.invoke_dispatcher import InvokeDispatcher

    invoker = InvokeDispatcher(send=AsyncMock())
    await server._run_conversation(  # noqa: SLF001
        ws,  # type: ignore[arg-type]
        invoker,
        {"conversationId": "c3", "text": "hi"},
    )
    results = ws.sent_events("node.conversation.result")
    # Brain raised → server emits result with error field set to the exc code.
    assert results
    assert results[0]["error"] == "PROTOCOL_ERROR"
