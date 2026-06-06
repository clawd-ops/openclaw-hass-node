"""Tests for openclaw_gateway.server.GatewayServer."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
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


def _server(model_responses: list[Any], *, auto_approve: bool = True) -> GatewayServer:
    """Build a server with an Anthropic provider backed by a mocked client."""
    from openclaw_gateway.providers_anthropic import AnthropicProvider

    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(side_effect=model_responses)
    provider = AnthropicProvider(client, model="test-model")
    return GatewayServer(provider, auto_approve=auto_approve)


def _text_resp(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


async def test_handshake_with_valid_signature_and_autoapprove_succeeds() -> None:
    """Valid signature + auto_approve=True → ok=True with token."""
    from conftest import signed_connect_params as _signed_connect_params

    server = _server([], auto_approve=True)
    ws = _FakeWS()
    nonce = "test-nonce"
    params = _signed_connect_params(device_id="node-a", nonce=nonce)
    ws.queue({"type": "req", "id": "r1", "method": "connect", "params": params})
    # Send challenge first to match the protocol order.
    await server._send_challenge(ws)  # type: ignore[arg-type]
    paired = await server._handshake(ws, nonce)  # type: ignore[arg-type]
    assert paired is True
    res = next(m for m in ws.sent_messages() if m.get("type") == "res")
    assert res["ok"] is True
    assert res["payload"]["token"]


async def test_handshake_rejects_wrong_method() -> None:
    server = _server([])
    ws = _FakeWS()
    ws.queue({"type": "req", "id": "r1", "method": "other", "params": {}})
    with pytest.raises(ValueError, match="connect"):
        await server._handshake(ws, "nonce")  # type: ignore[arg-type]


async def test_handshake_rejects_invalid_signature() -> None:
    """Tampered signature → ok=False with AUTH_BAD_SIGNATURE."""
    from conftest import b64url as _b64url
    from conftest import signed_connect_params as _signed_connect_params

    server = _server([], auto_approve=True)
    ws = _FakeWS()
    nonce = "n2"
    params = _signed_connect_params(device_id="bad", nonce=nonce)
    bad = bytearray(b"\x00" * 64)
    params["device"]["signature"] = _b64url(bytes(bad))
    ws.queue({"type": "req", "id": "r1", "method": "connect", "params": params})
    paired = await server._handshake(ws, nonce)  # type: ignore[arg-type]
    assert paired is False
    res = next(m for m in ws.sent_messages() if m.get("type") == "res")
    assert res["ok"] is False
    assert res["error"] == "AUTH_BAD_SIGNATURE"


async def test_handshake_pairing_required_when_not_autoapprove() -> None:
    """Valid signature but auto_approve=False → PAIRING_REQUIRED."""
    from conftest import signed_connect_params as _signed_connect_params

    server = _server([], auto_approve=False)
    ws = _FakeWS()
    nonce = "n3"
    params = _signed_connect_params(device_id="new-node", nonce=nonce)
    ws.queue({"type": "req", "id": "r1", "method": "connect", "params": params})
    paired = await server._handshake(ws, nonce)  # type: ignore[arg-type]
    assert paired is False
    res = next(m for m in ws.sent_messages() if m.get("type") == "res")
    assert res["error"] == "PAIRING_REQUIRED"


async def test_approve_device_then_handshake_succeeds() -> None:
    """approve_device promotes a pending device; subsequent connect is ok."""
    from conftest import signed_connect_params as _signed_connect_params

    server = _server([], auto_approve=False)
    # First attempt — registers as PENDING.
    nonce = "n4"
    params = _signed_connect_params(device_id="node-pending", nonce=nonce)
    ws1 = _FakeWS()
    ws1.queue({"type": "req", "id": "r1", "method": "connect", "params": params})
    paired = await server._handshake(ws1, nonce)  # type: ignore[arg-type]
    assert paired is False

    # Operator approves.
    server.approve_device("node-pending")

    # Second connect with same key — must succeed. Use a fresh nonce signed
    # at "now" to keep the signature valid against the new request.
    nonce2 = "n5"
    # Need to use the SAME private key as the first signature, which is
    # only known to _signed_connect_params helper. Generate one and reuse.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    params_a = _signed_connect_params(device_id="reused", nonce=nonce, private=priv)
    server._devices.register_or_get("reused", params_a["device"]["publicKey"])
    server.approve_device("reused")
    params_b = _signed_connect_params(device_id="reused", nonce=nonce2, private=priv)
    ws2 = _FakeWS()
    ws2.queue({"type": "req", "id": "r2", "method": "connect", "params": params_b})
    paired2 = await server._handshake(ws2, nonce2)  # type: ignore[arg-type]
    assert paired2 is True


async def test_handshake_rejects_changed_public_key() -> None:
    """Re-connecting under same device_id but different key is rejected."""
    from conftest import signed_connect_params as _signed_connect_params

    server = _server([], auto_approve=False)
    nonce_a = "na"
    params_a = _signed_connect_params(device_id="same-id", nonce=nonce_a)
    server._devices.register_or_get("same-id", params_a["device"]["publicKey"])

    nonce_b = "nb"
    params_b = _signed_connect_params(device_id="same-id", nonce=nonce_b)
    # Different priv key inside, so publicKey differs.
    ws = _FakeWS()
    ws.queue({"type": "req", "id": "r1", "method": "connect", "params": params_b})
    paired = await server._handshake(ws, nonce_b)  # type: ignore[arg-type]
    assert paired is False
    res = next(m for m in ws.sent_messages() if m.get("type") == "res")
    assert res["error"] == "AUTH_KEY_CHANGED"


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
    invoke_id = next(iter(invoker._pending))

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
    ws._in.append("not json")
    ws._in.append(json.dumps({"type": "req", "id": "r", "method": "node.pending.pull"}))
    from openclaw_gateway.invoke_dispatcher import InvokeDispatcher

    invoker = InvokeDispatcher(send=AsyncMock())
    await server._event_loop(ws, invoker)  # type: ignore[arg-type]
    # The pending.pull reply still made it
    assert any(m["type"] == "res" for m in ws.sent_messages())


async def test_brain_error_attribute_carried_through_to_payload() -> None:
    """When the provider raises BrainError, the error code is preserved."""
    from openclaw_gateway.providers import Round

    class _RaisingProvider:
        name = "raising"
        model = "x"

        def translate_tools(self, tools: list[Any]) -> list[Any]:
            return tools

        async def run_round(self, *args: Any, **kwargs: Any) -> Round:
            raise BrainError("PROTOCOL_ERROR", "bad shape")

        def append_tool_results(
            self, messages: list[Any], block: Any, results: list[Any]
        ) -> list[Any]:
            return messages

    server = GatewayServer(_RaisingProvider())
    ws = _FakeWS()
    from openclaw_gateway.invoke_dispatcher import InvokeDispatcher

    invoker = InvokeDispatcher(send=AsyncMock())
    await server._run_conversation(
        ws,  # type: ignore[arg-type]
        invoker,
        {"conversationId": "c3", "text": "hi"},
    )
    results = ws.sent_events("node.conversation.result")
    assert results
    assert results[0]["error"] == "PROTOCOL_ERROR"
