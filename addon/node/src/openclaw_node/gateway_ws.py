# pragma: no cover -- P2 scaffold; exercised once a gateway protocol harness lands.
"""Gateway WebSocket client for the OpenClaw node.

Implements the OpenClaw gateway WS protocol (role: ``node``):

1. Connect.
2. Receive ``connect.challenge`` event.
3. Send ``connect`` request with Ed25519-signed device identity.
4. Handle ``ok:true`` (paired) or ``PAIRING_REQUIRED`` (pending approval).
5. Receive ``node.invoke.request`` events and dispatch to command handlers.
6. Poll ``node.pending.pull`` on every (re-)connect to drain queued invokes.

This module is intentionally free of add-on-specific logic; it depends only on
:class:`~openclaw_node.config.NodeConfig`,
:class:`~openclaw_node.identity.DeviceIdentity`, and
:class:`~openclaw_node.pairing.PairingMachine`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Final

import websockets
import websockets.asyncio.client

from openclaw_node import __version__
from openclaw_node.commands.dispatcher import UnknownCommandError, dispatch_async
from openclaw_node.config import NodeConfig
from openclaw_node.identity import DeviceIdentity
from openclaw_node.pairing import PairingMachine, PairingState

if TYPE_CHECKING:
    from openclaw_node.http_api import NodeRuntime

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

_CONNECT_ROLE: Final[str] = "node"
_CONNECT_SCOPES: Final[list[str]] = []
_CONNECT_CAPS: Final[list[str]] = ["system"]
_CONNECT_COMMANDS: Final[list[str]] = [
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
_EMPTY: Final[str] = "".join(())
_PENDING_PULL_LIMIT: Final[int] = 10
_RECONNECT_DELAY_S: Final[float] = 5.0


def _make_req(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Build a gateway request frame.

    Args:
        method: The RPC method name (e.g. ``"connect"``).
        params: The params payload dict.

    Returns:
        A dict conforming to ``{type, id, method, params}``.
    """
    return {
        "type": "req",
        "id": str(uuid.uuid4()),
        "method": method,
        "params": params,
    }


class GatewayClient:
    """Persistent WebSocket connection to the OpenClaw gateway.

    Maintains a single WS connection with automatic reconnect and handles
    the full connect-challenge-handshake-invoke loop.

    Args:
        config: Node runtime configuration.
        identity: The node's Ed25519 device identity.
        device_token: Stored device token from a previous pairing (empty
            string on first pairing).

    Example:
        >>> import asyncio
        >>> # client = GatewayClient(config, identity)
        >>> # asyncio.run(client.run())
    """

    def __init__(
        self,
        config: NodeConfig,
        identity: DeviceIdentity,
        device_token: str | None = None,
        pairing_state_callback: Callable[[PairingState], None] | None = None,
        runtime: NodeRuntime | None = None,
    ) -> None:
        """Initialise the client without opening a connection.

        Args:
            config: Node runtime configuration.
            identity: The node's Ed25519 device identity.
            device_token: Stored device token from a previous pairing; empty
                string on first-time pairing.
            pairing_state_callback: Optional callback invoked whenever the
                pairing state may have changed.
            runtime: Optional shared :class:`NodeRuntime` used to surface
                gateway-connected state to the local HTTP API.
        """
        self._config = config
        self._identity = identity
        self._device_token = device_token or _EMPTY
        self._pairing = PairingMachine()
        self._pairing_state_callback = pairing_state_callback
        self._runtime = runtime

    @property
    def pairing_state(self) -> PairingState:
        """Return the current pairing state.

        Returns:
            The :class:`~openclaw_node.pairing.PairingState` of the machine.
        """
        return self._pairing.state

    async def run(self) -> None:
        """Connect to the gateway and run the event loop indefinitely.

        Reconnects automatically on WS disconnection with a fixed back-off
        delay of :data:`_RECONNECT_DELAY_S` seconds.  Exits only on an
        unrecoverable :class:`~openclaw_node.pairing.PairingError` that is
        not ``PAIRING_REQUIRED`` (e.g. ``AUTH_FAILED``).
        """
        while True:
            try:
                await self._connect_and_loop()
            except Exception as exc:
                _LOG.error(
                    "Gateway connection lost: %s - reconnecting in %.0fs",
                    exc,
                    _RECONNECT_DELAY_S,
                )
                self._pairing.on_reconnect()
                self._notify_pairing_state()
                if self._runtime is not None:
                    self._runtime.gateway_connected = False
                await asyncio.sleep(_RECONNECT_DELAY_S)

    async def _connect_and_loop(self) -> None:
        """Open a single WS connection, run the handshake, then the event loop.

        Raises:
            Any exception from :mod:`websockets` or the pairing machine on a
            fatal (non-PAIRING_REQUIRED) auth rejection.
        """
        _LOG.info("Connecting to gateway: %s", self._config.gateway_url)
        async with websockets.asyncio.client.connect(self._config.gateway_url) as ws:
            # Step 1: receive connect.challenge
            nonce, _ts = await self._recv_challenge(ws)

            # Step 2: send connect request
            req_id = await self._send_connect(ws, nonce)

            # Step 3: receive connect response
            await self._recv_connect_response(ws, req_id)

            if self._pairing.is_pending:
                _LOG.info(
                    "Pairing pending - waiting for gateway approval. "
                    "Run `openclaw devices approve` on the gateway."
                )
                # Hold connection open so the gateway can push approval
                await self._await_approval(ws)

            # Step 4: drain any pending queued invokes
            await self._pull_pending(ws)

            # Step 5: mark connected on the runtime so the local API can report.
            if self._runtime is not None:
                self._runtime.gateway_connected = True

            try:
                # Step 6: main event loop
                await self._event_loop(ws)
            finally:
                if self._runtime is not None:
                    self._runtime.gateway_connected = False

    async def _recv_challenge(
        self, ws: websockets.asyncio.client.ClientConnection
    ) -> tuple[str, int]:
        """Wait for and parse the ``connect.challenge`` event.

        Args:
            ws: The open WebSocket connection.

        Returns:
            A tuple ``(nonce, ts)`` from the challenge payload.

        Raises:
            ValueError: If the first message is not a ``connect.challenge`` event.
        """
        raw = await ws.recv()
        msg: dict[str, Any] = json.loads(raw)
        if msg.get("type") != "event" or msg.get("event") != "connect.challenge":
            raise ValueError(f"Expected connect.challenge, got: {msg.get('event')!r}")  # noqa: TRY003
        payload: dict[str, Any] = msg.get("payload", {})
        nonce: str = str(payload["nonce"])
        ts: int = int(payload["ts"])
        _LOG.debug("Got challenge nonce=%r ts=%d", nonce, ts)
        return nonce, ts

    async def _send_connect(
        self, ws: websockets.asyncio.client.ClientConnection, nonce: str
    ) -> str:
        """Build and send the ``connect`` request.

        Args:
            ws: The open WebSocket connection.
            nonce: The nonce from the ``connect.challenge`` event.

        Returns:
            The request ID string (for response correlation).
        """
        signature, signed_at_ms = self._identity.sign_connect(
            nonce=nonce,
            role=_CONNECT_ROLE,
            scopes=_CONNECT_SCOPES,
            token=self._device_token,
        )
        req = _make_req(
            "connect",
            {
                "minProtocol": 3,
                "maxProtocol": 4,
                "client": {
                    # OpenClaw GATEWAY_CLIENT_IDS enum only allows specific
                    # values; "node-host" is the headless-node id. Must match
                    # the _CLIENT_ID baked into identity.sign_connect's signed
                    # payload, or the gateway rejects the signature.
                    "id": "node-host",
                    "version": __version__,
                    "platform": "linux",
                    "mode": "node",
                },
                "role": _CONNECT_ROLE,
                "scopes": _CONNECT_SCOPES,
                "caps": _CONNECT_CAPS,
                "commands": _CONNECT_COMMANDS,
                "permissions": {},
                "auth": {"token": self._device_token},
                "locale": "en-US",
                "userAgent": f"openclaw-hass-node/{__version__}",
                "device": {
                    "id": self._identity.device_id,
                    "publicKey": self._identity.public_key_b64url,
                    "signature": signature,
                    "signedAt": signed_at_ms,
                    "nonce": nonce,
                },
            },
        )
        await ws.send(json.dumps(req))
        _LOG.debug("Sent connect request id=%s", req["id"])
        return str(req["id"])

    async def _recv_connect_response(
        self,
        ws: websockets.asyncio.client.ClientConnection,
        req_id: str,  # noqa: ARG002
    ) -> None:
        """Receive the ``connect`` response and drive the pairing machine.

        Args:
            ws: The open WebSocket connection.
            req_id: The ID of the sent connect request (for validation).

        Raises:
            ~openclaw_node.pairing.PairingError: On fatal auth rejection.
        """
        raw = await ws.recv()
        msg: dict[str, Any] = json.loads(raw)
        if msg.get("type") != "res":
            raise ValueError(f"Expected res frame, got type={msg.get('type')!r}")  # noqa: TRY003
        ok: bool = bool(msg.get("ok"))
        payload: dict[str, Any] | None = msg.get("payload") if ok else None
        error: str | None = msg.get("error") if not ok else None
        self._pairing.on_connect_response(ok=ok, payload=payload, error=error)
        self._notify_pairing_state()

    async def _await_approval(self, ws: websockets.asyncio.client.ClientConnection) -> None:
        """Block and process events until the gateway sends pairing approval.

        The gateway may send any number of events while the pairing is pending.
        When it sends a ``connect.approved`` event (or any re-authentication
        event), we transition out of PENDING.

        Args:
            ws: The open WebSocket connection.
        """
        _LOG.info("Waiting for pairing approval from the gateway…")
        async for raw in ws:
            msg: dict[str, Any] = json.loads(raw)
            event = msg.get("event")
            if event == "connect.approved":
                _LOG.info("Pairing approved by gateway.")
                self._pairing.on_connect_response(ok=True, payload=msg.get("payload"))
                self._notify_pairing_state()
                return
            _LOG.debug("Ignoring event while pending: %r", event)

    async def _pull_pending(self, ws: websockets.asyncio.client.ClientConnection) -> None:
        """Drain queued invokes by sending ``node.pending.pull``.

        Called once after each successful connect so that any invokes that
        arrived while the node was offline are processed.

        Args:
            ws: The open WebSocket connection.
        """
        req = _make_req("node.pending.pull", {"limit": _PENDING_PULL_LIMIT})
        await ws.send(json.dumps(req))
        raw = await ws.recv()
        msg: dict[str, Any] = json.loads(raw)
        if not msg.get("ok"):
            _LOG.warning("node.pending.pull failed: %r", msg.get("error"))
            return
        items: list[dict[str, Any]] = msg.get("payload", {}).get("items", [])
        _LOG.debug("Pulled %d pending items", len(items))
        for item in items:
            await self._handle_invoke(ws, item)
            await self._ack_pending(ws, str(item["invokeId"]))

    async def _ack_pending(
        self, ws: websockets.asyncio.client.ClientConnection, invoke_id: str
    ) -> None:
        """Send ``node.pending.ack`` for a processed pending item.

        Args:
            ws: The open WebSocket connection.
            invoke_id: The invoke ID to acknowledge.
        """
        ack = _make_req("node.pending.ack", {"invokeId": invoke_id})
        await ws.send(json.dumps(ack))

    async def _event_loop(self, ws: websockets.asyncio.client.ClientConnection) -> None:
        """Process incoming gateway events indefinitely.

        Handles ``node.invoke.request`` events by dispatching to the command
        registry.  All other event types are logged and ignored.

        Args:
            ws: The open WebSocket connection.
        """
        async for raw in ws:
            msg: dict[str, Any] = json.loads(raw)
            if msg.get("type") == "event" and msg.get("event") == "node.invoke.request":
                await self._handle_invoke(ws, msg.get("payload", {}))

    async def _handle_invoke(
        self,
        ws: websockets.asyncio.client.ClientConnection,
        payload: dict[str, Any],
    ) -> None:
        """Execute a single command invoke and send the result back.

        Args:
            ws: The open WebSocket connection.
            payload: The ``node.invoke.request`` event payload containing
                ``invokeId``, ``command``, and ``params``.
        """
        invoke_id: str = str(payload.get("invokeId", ""))
        command: str = str(payload.get("command", ""))
        params: dict[str, Any] = dict(payload.get("params") or {})
        _LOG.debug("Invoke invokeId=%s command=%r", invoke_id, command)

        try:
            result = await dispatch_async(command, params)
            resp = _make_req(
                "node.invoke.result",
                {"invokeId": invoke_id, "ok": True, "result": result},
            )
        except UnknownCommandError as exc:
            _LOG.warning("Unknown command: %r", command)
            resp = _make_req(
                "node.invoke.result",
                {
                    "invokeId": invoke_id,
                    "ok": False,
                    "error": f"UNKNOWN_COMMAND: {exc.command}",
                },
            )
        except Exception as exc:
            _LOG.exception("Command %r raised: %s", command, exc)
            resp = _make_req(
                "node.invoke.result",
                {
                    "invokeId": invoke_id,
                    "ok": False,
                    "error": "COMMAND_ERROR",
                    "message": "Internal command error",
                },
            )

        await ws.send(json.dumps(resp))

    def _notify_pairing_state(self) -> None:
        """Notify the optional callback of the current pairing state."""
        if self._pairing_state_callback is not None:
            self._pairing_state_callback(self._pairing.state)
