"""Minimal WebSocket gateway server for openclaw-hass-node.

Accepts a single node WS connection, runs a trial-mode handshake (no
signature verification yet — that lands in P5.7 hardening), and:

- For every ``node.conversation.request`` it receives, runs
  :meth:`openclaw_gateway.brain.Brain.handle_turn` and emits
  ``node.conversation.result``.
- Routes the brain's tool calls back to the node as
  ``node.invoke.request`` events and pairs replies via
  :class:`openclaw_gateway.invoke_dispatcher.InvokeDispatcher`.

This is a *trial* server. It is intentionally permissive on auth so the
two halves can be wired end-to-end. The real auth path (Ed25519
verification + pairing approval flow) belongs in a follow-up — the
node-side client already implements the handshake, the gateway just has
to validate signatures instead of trusting them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Final

import websockets
from websockets.asyncio.server import ServerConnection, serve

from openclaw_gateway.auth import AuthError, verify_connect_signature
from openclaw_gateway.brain import Brain
from openclaw_gateway.device_registry import DeviceRegistry, DeviceState
from openclaw_gateway.invoke_dispatcher import InvokeDispatcher
from openclaw_gateway.providers import Provider
from openclaw_gateway.tools import HA_TOOLS

_LOG: Final[logging.Logger] = logging.getLogger(__name__)


def _make_event(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Build a gateway → node event frame."""
    return {"type": "event", "event": event, "payload": payload}


def _make_res(req_id: str, *, ok: bool, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a response frame for a request."""
    body: dict[str, Any] = {"type": "res", "id": req_id, "ok": ok}
    if ok and payload is not None:
        body["payload"] = payload
    return body


class GatewayServer:
    """Trial-mode WS gateway that connects nodes to the brain.

    Args:
        provider: Concrete :class:`openclaw_gateway.providers.Provider`
            (Anthropic / OpenAI / future runtimes).
        host: Bind host.
        port: Bind port.
        system_prompt: System prompt prepended to every Assist turn.
    """

    def __init__(
        self,
        provider: Provider,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        system_prompt: str = "",
        device_registry: DeviceRegistry | None = None,
        auto_approve: bool = False,
    ) -> None:
        """Initialise the server without binding the socket.

        Args:
            provider: Configured model provider.
            host: Bind host.
            port: Bind port.
            system_prompt: System prompt for every Assist turn.
            device_registry: Optional shared registry; one is created if absent.
            auto_approve: If True (trial mode), unknown devices are paired on
                first connect without operator approval.
        """
        self._provider = provider
        self._host = host
        self._port = port
        self._system = system_prompt
        self._bg_tasks: set[asyncio.Task[None]] = set()
        self._devices = device_registry or DeviceRegistry()
        self._auto_approve = auto_approve

    async def serve_forever(self) -> None:  # pragma: no cover — runtime entrypoint
        """Bind the WS port and accept connections indefinitely."""
        async with serve(self.handle_connection, self._host, self._port):
            _LOG.info("Gateway server listening on ws://%s:%d", self._host, self._port)
            await asyncio.Future()

    async def handle_connection(self, ws: ServerConnection) -> None:
        """Run one node session: handshake → event loop → teardown.

        Args:
            ws: The accepted WebSocket connection.
        """
        try:
            nonce = await self._send_challenge(ws)
            paired = await self._handshake(ws, nonce)
        except websockets.ConnectionClosed:
            _LOG.info("Node connection closed during handshake")
            return
        except (ValueError, KeyError) as exc:
            _LOG.warning("Handshake error: %s", exc)
            return

        if not paired:
            # PAIRING_REQUIRED was already sent; hold the connection open
            # briefly so the node can log the message, then close.
            await asyncio.sleep(0.1)
            return

        invoker = InvokeDispatcher(send=self._make_invoke_sender(ws))
        try:
            await self._event_loop(ws, invoker)
        finally:
            invoker.cancel_all("Node disconnected")

    async def _send_challenge(self, ws: ServerConnection) -> str:
        """Send the initial ``connect.challenge`` event and return the nonce."""
        nonce = uuid.uuid4().hex
        await ws.send(
            json.dumps(
                _make_event(
                    "connect.challenge",
                    {"nonce": nonce, "ts": int(time.time() * 1000)},
                )
            )
        )
        return nonce

    async def _handshake(self, ws: ServerConnection, nonce: str) -> bool:
        """Receive the connect request, verify, and respond.

        Args:
            ws: The accepted WebSocket connection.
            nonce: The nonce issued in the connect.challenge.

        Returns:
            True if the device is PAIRED and the event loop should run,
            False if PAIRING_REQUIRED was sent and the connection should close.
        """
        raw = await ws.recv()
        msg = json.loads(raw)
        if msg.get("type") != "req" or msg.get("method") != "connect":
            raise ValueError(f"Expected connect req, got {msg.get('method')!r}")  # noqa: TRY003
        req_id = str(msg.get("id", ""))
        params = msg.get("params") or {}

        try:
            device_id = verify_connect_signature(
                params,
                nonce_expected=nonce,
                now_ms=int(time.time() * 1000),
            )
        except AuthError as exc:
            _LOG.warning("Auth failed: %s (%s)", exc.code, exc.message)
            await ws.send(
                json.dumps(
                    {
                        "type": "res",
                        "id": req_id,
                        "ok": False,
                        "error": exc.code,
                        "message": exc.message,
                    }
                )
            )
            return False

        public_key = str((params.get("device") or {}).get("publicKey", ""))
        try:
            record = self._devices.register_or_get(device_id, public_key)
        except ValueError as exc:
            _LOG.warning("Device key conflict: %s", exc)
            await ws.send(
                json.dumps(
                    {
                        "type": "res",
                        "id": req_id,
                        "ok": False,
                        "error": "AUTH_KEY_CHANGED",
                        "message": str(exc),
                    }
                )
            )
            return False

        if record.state is DeviceState.PENDING:
            if self._auto_approve:
                record = self._devices.approve(device_id)
            else:
                _LOG.info("Device %r registered; awaiting approval", device_id)
                await ws.send(
                    json.dumps(
                        {
                            "type": "res",
                            "id": req_id,
                            "ok": False,
                            "error": "PAIRING_REQUIRED",
                            "message": "Approve this device with gateway.approve_device(id)",
                        }
                    )
                )
                return False

        _LOG.info("Node paired: id=%r", device_id)
        await ws.send(
            json.dumps(
                _make_res(
                    req_id,
                    ok=True,
                    payload={"token": record.token, "node": {"id": device_id}},
                )
            )
        )
        return True

    def approve_device(self, device_id: str) -> None:
        """Operator-facing helper: approve a pending device.

        Args:
            device_id: The device to approve. Raises KeyError if not present.
        """
        self._devices.approve(device_id)

    def _make_invoke_sender(self, ws: ServerConnection) -> Any:
        """Build a send callback the InvokeDispatcher can use."""

        async def _send(payload: dict[str, Any]) -> None:
            await ws.send(json.dumps(_make_event("node.invoke.request", payload)))

        return _send

    async def _event_loop(self, ws: ServerConnection, invoker: InvokeDispatcher) -> None:
        """Process inbound frames until the connection closes.

        Args:
            ws: The active WebSocket connection.
            invoker: The per-connection invoke dispatcher.
        """
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                _LOG.warning("Dropping non-JSON frame")
                continue

            if msg.get("type") == "req":
                await self._handle_request(ws, invoker, msg)

    async def _handle_request(
        self,
        ws: ServerConnection,
        invoker: InvokeDispatcher,
        msg: dict[str, Any],
    ) -> None:
        """Route a node-originated request to the right handler.

        Args:
            ws: The active WebSocket connection.
            invoker: Invoke dispatcher for any tool calls the brain makes.
            msg: The incoming frame.
        """
        method = str(msg.get("method", ""))
        req_id = str(msg.get("id", ""))
        params = msg.get("params") or {}

        if method == "node.invoke.result":
            invoker.handle_result(params)
            await ws.send(json.dumps(_make_res(req_id, ok=True, payload={})))
            return

        if method == "node.pending.pull":
            await ws.send(json.dumps(_make_res(req_id, ok=True, payload={"items": []})))
            return

        if method == "node.conversation.request":
            await ws.send(json.dumps(_make_res(req_id, ok=True, payload={})))
            task = asyncio.create_task(self._run_conversation(ws, invoker, params))
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
            return

        _LOG.debug("Ignoring unknown request method=%r", method)
        await ws.send(json.dumps(_make_res(req_id, ok=False)))

    async def _run_conversation(
        self,
        ws: ServerConnection,
        invoker: InvokeDispatcher,
        params: dict[str, Any],
    ) -> None:
        """Run a single Assist turn through the brain and emit the result.

        Args:
            ws: The WebSocket to emit the result on.
            invoker: Invoke dispatcher the brain uses for tool calls.
            params: The ``node.conversation.request`` payload.
        """
        conversation_id = str(params.get("conversationId", ""))
        text = str(params.get("text", ""))
        language = params.get("language")
        brain = Brain(
            self._provider,
            invoke_tool=invoker.invoke,
            tools=HA_TOOLS,
            system_prompt=self._system,
        )
        try:
            result = await brain.handle_turn(text, conversation_id or None, language)
            payload: dict[str, Any] = {
                "conversationId": conversation_id,
                "response": result["response"],
                "model": result["model"],
                "provider": result["provider"],
                "rounds": result["rounds"],
            }
        except Exception as exc:
            _LOG.warning("Brain failed for conv=%r: %s", conversation_id, exc)
            payload = {
                "conversationId": conversation_id,
                "error": getattr(exc, "code", "BRAIN_ERROR"),
                "message": str(exc),
            }
        try:
            await ws.send(json.dumps(_make_event("node.conversation.result", payload)))
        except websockets.ConnectionClosed:
            _LOG.info("Node disconnected before conversation result could be sent")
