# pragma: no cover -- P2 scaffold; exercised once a gateway protocol harness lands.
"""Gateway WebSocket client for the OpenClaw node.

**Canonical wire schema lives at:**
``/app/node_modules/openclaw/dist/plugin-sdk/packages/gateway-protocol/src/schema/protocol-schemas.d.ts``
(see ``ProtocolSchemas.*Params``). When changing any RPC payload here,
read that file FIRST. The .md docs at /app/docs/gateway/protocol.md
are descriptive, not authoritative — they list method names but not
field shapes.

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
import contextlib
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
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
_RECONNECT_DELAY_S: Final[float] = 5.0


def _decode_error_code(raw: Any) -> str | None:
    """Return the canonical ``error.code`` string for a response frame.

    The canonical ``ResponseFrame.error`` shape is ``{"code", "message"}``,
    but earlier gateway builds (and some fixtures) put a bare string code at
    ``error``. Accept both so the pairing machine sees the same code in
    either case.
    """
    if isinstance(raw, dict):
        code = raw.get("code")
        return code if isinstance(code, str) else None
    if isinstance(raw, str):
        return raw
    return None


class InvalidInvokeParamsError(ValueError):
    """Raised when a node.invoke.request carries malformed canonical params.

    Surfaces a schema violation to the invoker rather than silently running
    the command with ``{}``. Missing ``paramsJSON`` is *not* an error; the
    legacy ``params`` field is used and may itself be empty.
    """


def _decode_invoke_params(payload: dict[str, Any]) -> dict[str, Any]:
    """Return invoke params from a canonical or legacy ``node.invoke.request`` payload.

    Canonical: ``paramsJSON`` is a stringified JSON object. Legacy: ``params``
    is the dict inline. We prefer the canonical shape and fall back to the
    legacy field for back-compat with existing test fixtures.

    Raises:
        InvalidInvokeParamsError: When ``paramsJSON`` is present but is not a
            string of a JSON object (malformed JSON, JSON array, JSON
            primitive, etc.).
    """
    raw_json = payload.get("paramsJSON")
    if raw_json is not None:
        if not isinstance(raw_json, str) or not raw_json:
            raise InvalidInvokeParamsError("paramsJSON must be a non-empty string")  # noqa: TRY003
        try:
            decoded = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise InvalidInvokeParamsError(f"paramsJSON is not valid JSON: {exc}") from exc  # noqa: TRY003
        if not isinstance(decoded, dict):
            raise InvalidInvokeParamsError(  # noqa: TRY003
                f"paramsJSON must decode to a JSON object, got {type(decoded).__name__}"
            )
        return dict(decoded)
    legacy = payload.get("params")
    return dict(legacy) if isinstance(legacy, dict) else {}


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
                self._maybe_drop_invalid_device_token(exc)
                self._pairing.on_reconnect()
                self._notify_pairing_state()
                if self._runtime is not None:
                    self._runtime.gateway_connected = False
                await asyncio.sleep(_RECONNECT_DELAY_S)

    def _maybe_drop_invalid_device_token(self, exc: BaseException) -> None:
        """If the persisted device_token was rejected, fall back to pairing_token.

        Gateway rejects an unknown / evicted device_token with one of:
        - NOT_PAIRED / PAIRING_REQUIRED (device record gone entirely)
        - AUTH_TOKEN_MISMATCH / token_mismatch (device exists but the token
          doesn't match — e.g. after gateway removed + re-paired with a
          different token)

        Without this fallback the addon loops forever sending the same bad
        token, getting the same rejection, until the user manually clears
        /data/openclaw/device-token.
        """
        haystack = repr(exc)
        triggers = ("NOT_PAIRED", "PAIRING_REQUIRED", "AUTH_TOKEN_MISMATCH", "token_mismatch")
        if not any(t in haystack for t in triggers):
            return
        if not self._device_token:
            return
        path = self._config.device_token_path
        if self._device_token == (self._config.pairing_token or ""):
            return  # already using the pairing_token
        _LOG.warning(
            "Persisted device_token rejected by gateway; clearing and "
            "falling back to pairing_token. The gateway will create a new "
            "pairing request on the next connect.",
        )
        if self._token_path_safe_to_unlink(path):
            try:
                path.unlink(missing_ok=True)
            except OSError as os_exc:
                _LOG.warning("Could not remove %s: %s", path, os_exc)
        self._device_token = self._config.pairing_token or _EMPTY

    def _token_path_safe_to_unlink(self, path: Path) -> bool:
        """Return True only if *path* resolves inside the configured data dir.

        Refuses to follow symlinks outside ``config.data_dir`` and skips the
        unlink when ``data_dir`` itself is missing. The token file is on the
        node's own private data mount; anything resolving elsewhere means
        someone has tampered with it and we should leave it alone.
        """
        try:
            data_dir = self._config.data_dir.resolve(strict=False)
            resolved = path.resolve(strict=False)
        except OSError as exc:
            _LOG.warning("Refusing to unlink %s: cannot resolve (%s)", path, exc)
            return False
        try:
            resolved.relative_to(data_dir)
        except ValueError:
            _LOG.warning("Refusing to unlink %s: resolved outside data_dir %s", resolved, data_dir)
            return False
        if path.is_symlink():
            _LOG.warning("Refusing to unlink %s: path is a symlink", path)
            return False
        return True

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
                    # platform + deviceFamily are pulled from connectParams.client
                    # when the gateway reconstructs the v3 signature payload
                    # (see /app/dist/message-handler-Du1uvc4A.js). They must
                    # match identity._PLATFORM / _DEVICE_FAMILY exactly or the
                    # signature verify fails.
                    "deviceFamily": "hass-node",
                    # displayName is the friendly name shown in the gateway UI
                    # and `openclaw nodes describe` output. Falls back to the
                    # device-id fingerprint when absent.
                    "displayName": self._config.node_name
                    or f"openclaw-hass-node@{self._identity.device_id[:12]}",
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
        req_id: str,
    ) -> None:
        """Receive the ``connect`` response and drive the pairing machine.

        Accepts canonical ``ResponseFrame.error = {"code", "message"}`` as
        well as the legacy ``error = "<code>"`` string. Validates that the
        response id matches the request id so an interleaved frame cannot
        be mis-correlated.

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
        if msg.get("id") != req_id:
            raise ValueError(  # noqa: TRY003
                f"Connect response id mismatch: expected {req_id!r}, got {msg.get('id')!r}"
            )
        ok: bool = bool(msg.get("ok"))
        payload: dict[str, Any] | None = msg.get("payload") if ok else None
        error: str | None = None if ok else _decode_error_code(msg.get("error"))
        self._pairing.on_connect_response(ok=ok, payload=payload, error=error)
        self._notify_pairing_state()
        # On successful connect the gateway may issue a long-lived device_token
        # in hello-ok.auth.deviceToken. Persist it and use it for subsequent
        # connects — the original pairing_token from add-on options is one-shot
        # and rejected once the pairing has been approved.
        if ok and payload is not None:
            auth = payload.get("auth") or {}
            issued = auth.get("deviceToken")
            if isinstance(issued, str) and issued and issued != self._device_token:
                _LOG.info("Gateway issued a new device_token; persisting.")
                self._device_token = issued
                self._persist_device_token(issued)

    def _persist_device_token(self, token: str) -> None:
        """Atomically write the issued device token with mode 0o600.

        The bearer token gates every gateway invoke; a 0o644 file under
        ``/data`` is readable by any other process sharing the namespace,
        and a symlink at the token (or temp) path could redirect the write
        outside the data dir. Hardening:

        - O_NOFOLLOW on the temp open so an attacker-planted symlink at
          ``device-token.tmp`` is rejected rather than written through.
        - ``os.fchmod`` immediately after open to force 0o600 even if the
          temp file already existed at a looser mode.
        - ``fsync`` data, ``replace`` atomically, then clean up the temp
          file on a failed replace.
        - Refuse to ``chmod`` the final path if it is a symlink.
        """
        path = self._config.device_token_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        if tmp.is_symlink():
            tmp.unlink()
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(tmp), flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(token)
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
        try:
            tmp.replace(path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
        # Replace preserves the temp file's mode, but be defensive in case
        # an earlier 0o644 file already existed at `path`. Never chmod
        # through a symlink.
        if not path.is_symlink():
            with contextlib.suppress(OSError):
                path.chmod(0o600)

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
        # Gateway rejects 'limit' param; current schema takes empty params.
        req = _make_req("node.pending.pull", {})
        await ws.send(json.dumps(req))
        raw = await ws.recv()
        msg: dict[str, Any] = json.loads(raw)
        if not msg.get("ok"):
            _LOG.warning("node.pending.pull failed: %r", msg.get("error"))
            return
        items: list[dict[str, Any]] = msg.get("payload", {}).get("items", [])
        _LOG.debug("Pulled %d pending items", len(items))
        for item in items:
            # Canonical NodePendingDrainResult.items[] is an envelope:
            #   {id, payload: {invoke}, ...}
            # Legacy test fixtures pass the invoke payload directly with no
            # outer `payload` key. A non-dict `payload` is a malformed item;
            # skip it (and skip the ack, so the gateway can retry/expire it).
            if "payload" not in item:
                invoke_payload: dict[str, Any] = item
            elif isinstance(item["payload"], dict):
                invoke_payload = item["payload"]
            else:
                _LOG.warning(
                    "skipping malformed pending item id=%r: payload is %s",
                    item.get("id"),
                    type(item["payload"]).__name__,
                )
                continue
            await self._handle_invoke(ws, invoke_payload)
            await self._ack_pending(ws, str(item.get("id") or ""))

    async def _ack_pending(
        self, ws: websockets.asyncio.client.ClientConnection, invoke_id: str
    ) -> None:
        """Send ``node.pending.ack`` for a processed pending item.

        Args:
            ws: The open WebSocket connection.
            invoke_id: The invoke ID to acknowledge.
        """
        # Canonical NodePendingAckParams takes an array of ids.
        ack = _make_req("node.pending.ack", {"ids": [invoke_id]})
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

        Canonical node.invoke.result shape per /app/dist/node-cli-D-_DNWjG.js
        buildNodeInvokeResultParams: {id, nodeId, ok, payload?, error?}.

        Args:
            ws: The open WebSocket connection.
            payload: The ``node.invoke.request`` event payload containing
                ``id``, ``nodeId``, ``command``, and ``params``.
        """
        invoke_id: str = str(payload.get("id", payload.get("invokeId", "")))
        node_id: str = str(payload.get("nodeId", ""))
        command: str = str(payload.get("command", ""))
        _LOG.info("invoke ▶ %s id=%s", command, invoke_id[:8])
        start_ms = time.monotonic()

        base = {"id": invoke_id, "nodeId": node_id}
        try:
            params: dict[str, Any] = _decode_invoke_params(payload)
        except InvalidInvokeParamsError as exc:
            _LOG.warning("invoke ◀ %s INVALID_PARAMS id=%s: %s", command, invoke_id[:8], exc)
            resp = _make_req(
                "node.invoke.result",
                {
                    **base,
                    "ok": False,
                    "error": {"code": "INVALID_PARAMS", "message": "Malformed invoke params"},
                },
            )
            await ws.send(json.dumps(resp))
            return
        try:
            result = await dispatch_async(command, params)
            elapsed_ms = int((time.monotonic() - start_ms) * 1000)
            _LOG.info("invoke ◀ %s ok id=%s %dms", command, invoke_id[:8], elapsed_ms)
            resp = _make_req(
                "node.invoke.result",
                {**base, "ok": True, "payload": result},
            )
        except UnknownCommandError as exc:
            elapsed_ms = int((time.monotonic() - start_ms) * 1000)
            _LOG.warning(
                "invoke ◀ %s UNKNOWN_COMMAND id=%s %dms",
                command,
                invoke_id[:8],
                elapsed_ms,
            )
            resp = _make_req(
                "node.invoke.result",
                {
                    **base,
                    "ok": False,
                    "error": {"code": "UNKNOWN_COMMAND", "message": exc.command},
                },
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start_ms) * 1000)
            _LOG.exception(
                "invoke ◀ %s COMMAND_ERROR id=%s %dms: %s",
                command,
                invoke_id[:8],
                elapsed_ms,
                exc,
            )
            resp = _make_req(
                "node.invoke.result",
                {
                    **base,
                    "ok": False,
                    "error": {"code": "COMMAND_ERROR", "message": "Internal command error"},
                },
            )

        await ws.send(json.dumps(resp))

    def _notify_pairing_state(self) -> None:
        """Notify the optional callback of the current pairing state."""
        if self._pairing_state_callback is not None:
            self._pairing_state_callback(self._pairing.state)
