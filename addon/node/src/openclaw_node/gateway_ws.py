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
from openclaw_node.chat_relay import ChatRelay
from openclaw_node.commands.dispatcher import UnknownCommandError, dispatch_async
from openclaw_node.config import NodeConfig
from openclaw_node.identity import DeviceIdentity
from openclaw_node.pairing import PairingMachine, PairingState

if TYPE_CHECKING:
    from openclaw_node.http_api import NodeRuntime

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

# Default role / scopes / caps / commands for a node-role connection.
# These are class-level defaults; each GatewayClient instance can override
# via constructor args (see P5.13 dual-WS refactor #84 — operator-role
# connections use a different role + the four operator scopes the QR
# pairing flow grants, and advertise NO caps/commands).
_NODE_ROLE: Final[str] = "node"
_NODE_SCOPES: Final[list[str]] = []
_NODE_CAPS: Final[list[str]] = ["system"]
_NODE_COMMANDS: Final[list[str]] = [
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
# The operator-scope quartet granted by PAIRING_SETUP_BOOTSTRAP_PROFILE
# in /app/node_modules/openclaw/dist/device-bootstrap-RTH5XJTg.js.
# Required for chat.send + sessions.messages.subscribe.
_OPERATOR_SCOPES: Final[list[str]] = [
    "operator.approvals",
    "operator.read",
    "operator.talk.secrets",
    "operator.write",
]
_EMPTY: Final[str] = "".join(())
_RECONNECT_DELAY_S: Final[float] = 5.0
_PENDING_PULL_BATCH: Final[int] = 50
# Cap on hasMore drain iterations per connect — keeps a misbehaving gateway
# from livelocking the connect handshake. 20 iterations * 50 items per batch
# = 1000 items per connect (anything beyond that arrives on next reconnect).
_PENDING_PULL_MAX_ITERATIONS: Final[int] = 20
_ACK_RESPONSE_TIMEOUT_S: Final[float] = 5.0


def _decode_error_code(raw: Any) -> str | None:
    """Return ``error.code`` from a canonical ``ResponseFrame.error`` object.

    Per the canonical schema ``error`` is always ``{"code", "message"}``.
    Anything else (bare string, missing, wrong type) is a schema violation
    and is reported as ``None`` so the caller surfaces a generic failure.
    """
    if not isinstance(raw, dict):
        return None
    code = raw.get("code")
    return code if isinstance(code, str) else None


def _decode_error_message(raw: Any) -> str:
    """Return ``error.message`` (and details) from a ResponseFrame.error.

    Always returns a string; empty string when no useful detail is present.
    Includes ``details`` when present so the gateway-side rejection reason
    surfaces in node logs.
    """
    if not isinstance(raw, dict):
        return str(raw) if raw else ""
    parts: list[str] = []
    message = raw.get("message")
    if isinstance(message, str) and message:
        parts.append(message)
    details = raw.get("details")
    if details:
        parts.append(f"details={details!r}")
    return " | ".join(parts)


class InvalidInvokeParamsError(ValueError):
    """Raised when a node.invoke.request carries malformed canonical params.

    The canonical ``NodeInvokeRequestEvent.paramsJSON`` must be a string
    that decodes to a JSON object. Any other shape is a schema violation
    and the invoker is told ``INVALID_PARAMS`` rather than running with
    silently-degraded input.
    """


def _decode_invoke_params(payload: dict[str, Any]) -> dict[str, Any]:
    """Return invoke params from a canonical ``node.invoke.request`` payload.

    Raises:
        InvalidInvokeParamsError: When ``paramsJSON`` is missing, is not a
            non-empty string, decodes to non-JSON, or decodes to anything
            other than a JSON object.
    """
    raw_json = payload.get("paramsJSON")
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
        *,
        role: str = _NODE_ROLE,
        scopes: list[str] | None = None,
        caps: list[str] | None = None,
        commands: list[str] | None = None,
        chat_relay_enabled: bool = True,
        invoke_dispatch_enabled: bool = True,
        token_persist_path: Path | None = None,
        pair_fallback_enabled: bool = True,
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
            role: Gateway connection role (``"node"`` or ``"operator"``).
                Determines which RPCs the connection is authorized to call;
                see the gateway role-policy notes in ``docs/LESSONS.md``.
            scopes: Operator scopes to advertise at connect. Ignored for
                node-role connections (gateway rejects scopes on node connect).
            caps: Device capabilities to advertise. Empty list for operator.
            commands: Command names to advertise. Empty list for operator.
            chat_relay_enabled: When True, the connection owns a
                :class:`ChatRelay` instance and routes ``session.message`` /
                ``chat*`` events through it. Set False on the node-role
                connection in the P5.13 dual-WS world.
            invoke_dispatch_enabled: When True, the connection drains
                ``node.pending.pull`` on connect and dispatches
                ``node.invoke.request`` events. Set False on the
                operator-role connection.
            token_persist_path: Override the path where the gateway-issued
                device token is persisted. Defaults to
                ``config.device_token_path``. Both role connections may
                share the same path safely — the gateway issues one
                device token per device record (the dual-role pairing
                profile grants both roles on a single token), so writes
                are idempotent.
            pair_fallback_enabled: When True, an INVALID_REQUEST /
                NOT_PAIRED rejection clears the persisted device token
                and falls back to the one-shot pairing_token. Disable on
                the operator-role connection — it must NOT clear the
                shared token file when its connect is rejected (which
                would happen if the device is only single-role paired),
                because that would break the node-role connection too.
        """
        self._config = config
        self._identity = identity
        self._device_token = device_token or _EMPTY
        self._pairing = PairingMachine()
        self._pairing_state_callback = pairing_state_callback
        self._runtime = runtime
        self._role = role
        self._scopes = list(scopes) if scopes is not None else list(_NODE_SCOPES)
        self._caps = list(caps) if caps is not None else list(_NODE_CAPS)
        self._commands = list(commands) if commands is not None else list(_NODE_COMMANDS)
        self._chat_relay_enabled = chat_relay_enabled
        self._invoke_dispatch_enabled = invoke_dispatch_enabled
        self._token_persist_path = token_persist_path or config.device_token_path
        self._pair_fallback_enabled = pair_fallback_enabled

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
                    self._set_runtime_connected(False)
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
        if not self._pair_fallback_enabled:
            return
        haystack = repr(exc)
        triggers = ("NOT_PAIRED", "PAIRING_REQUIRED", "AUTH_TOKEN_MISMATCH", "token_mismatch")
        if not any(t in haystack for t in triggers):
            return
        if not self._device_token:
            return
        path = self._token_persist_path
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

    def _reload_device_token(self) -> None:
        """Re-read the persisted device token before each connect attempt.

        In the dual-WS world the node-role connection may persist a new
        token after pairing while the operator-role connection still holds
        the stale bootstrap token in memory.  Re-reading the file ensures
        the operator picks up the freshly persisted token on its next
        reconnect cycle.
        """
        path = self._token_persist_path
        try:
            if path.is_file():
                value = path.read_text().strip()
                if value:
                    self._device_token = value
        except OSError as exc:
            _LOG.debug("Could not reload device token from %s: %s", path, exc)

    async def _connect_and_loop(self) -> None:
        """Open a single WS connection, run the handshake, then the event loop.

        Raises:
            Any exception from :mod:`websockets` or the pairing machine on a
            fatal (non-PAIRING_REQUIRED) auth rejection.
        """
        self._reload_device_token()
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

            # Step 4: drain any pending queued invokes (node-role only).
            if self._invoke_dispatch_enabled:
                await self._pull_pending(ws)

            # Step 5: create chat relay bound to this WS connection
            # (operator-role only — chat.send + sessions.messages.subscribe
            # are operator.write scope; node-role connections cannot call them).
            async def _ws_send(frame: dict[str, Any]) -> None:
                await ws.send(json.dumps(frame))

            relay: ChatRelay | None = ChatRelay(_ws_send) if self._chat_relay_enabled else None

            # Step 6: mark per-role connected on the runtime so the local
            # API can report. Each connection sets its own role-specific
            # flag — node_connected vs operator_connected — to avoid the
            # last-writer-wins race a single shared boolean would create.
            if self._runtime is not None:
                self._set_runtime_connected(True)
                if relay is not None:
                    self._runtime.chat_relay = relay

            try:
                # Step 7: main event loop
                await self._event_loop(ws, relay)
            finally:
                if relay is not None:
                    relay.reset()
                if self._runtime is not None:
                    self._set_runtime_connected(False)
                    if relay is not None:
                        self._runtime.chat_relay = None

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
            role=self._role,
            scopes=self._scopes,
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
                "role": self._role,
                "scopes": self._scopes,
                "caps": self._caps,
                "commands": self._commands,
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
        error_raw = msg.get("error")
        error: str | None = None if ok else _decode_error_code(error_raw)
        error_message: str = "" if ok else _decode_error_message(error_raw)
        self._pairing.on_connect_response(
            ok=ok, payload=payload, error=error, error_message=error_message
        )
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
        path = self._token_persist_path
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
            fh = os.fdopen(fd, "w", encoding="utf-8")
        except BaseException:
            # fdopen failed before it took ownership of fd; close it ourselves
            # so we don't leak a raw fd into the long-running addon process.
            with contextlib.suppress(OSError):
                os.close(fd)
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
        try:
            with fh:
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

        If the WebSocket iterator exits without delivering an approval — for
        example the gateway closes the connection mid-pairing — raise so the
        caller's reconnect loop can re-establish the connection rather than
        silently proceeding through ``_pull_pending`` against a dead socket.

        Args:
            ws: The open WebSocket connection.

        Raises:
            ConnectionError: When the iterator exits before approval.
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
        raise ConnectionError(  # noqa: TRY003
            "WebSocket iterator exited before pairing approval; "
            "gateway likely closed the connection — reconnecting."
        )

    async def _pull_pending(self, ws: websockets.asyncio.client.ClientConnection) -> None:
        """Drain queued invokes by sending ``node.pending.pull``.

        Called once after each successful connect so that any invokes that
        arrived while the node was offline are processed.

        Sends the canonical ``NodePendingDrainParams.maxItems`` field so a
        large backlog is paged rather than dumped in one frame. The gateway
        is documented to clamp values it doesn't like; ``_PENDING_PULL_BATCH``
        is the canonical upper bound this client is willing to receive in a
        single drain.

        Loops on the canonical ``NodePendingDrainResult.hasMore`` flag so a
        backlog larger than one batch is fully drained before the event loop
        starts (#88/2). Bounded by ``_PENDING_PULL_MAX_ITERATIONS`` to keep
        a misbehaving gateway from livelocking the connect handshake.

        Args:
            ws: The open WebSocket connection.
        """
        for iteration in range(_PENDING_PULL_MAX_ITERATIONS):
            req = _make_req("node.pending.pull", {"maxItems": _PENDING_PULL_BATCH})
            await ws.send(json.dumps(req))
            raw = await ws.recv()
            msg: dict[str, Any] = json.loads(raw)
            if not msg.get("ok"):
                _LOG.warning("node.pending.pull failed: %r", msg.get("error"))
                return
            payload_obj_raw = msg.get("payload")
            if not isinstance(payload_obj_raw, dict):
                _LOG.warning(
                    "node.pending.pull returned non-object payload (got %s); "
                    "treating as empty drain",
                    type(payload_obj_raw).__name__,
                )
                return
            items: list[Any] = payload_obj_raw.get("items", []) or []
            has_more = bool(payload_obj_raw.get("hasMore", False))
            _LOG.debug(
                "Pulled %d pending items (iteration=%d, hasMore=%s)",
                len(items),
                iteration,
                has_more,
            )
            for item in items:
                # Canonical NodePendingDrainResult.items[] is an envelope:
                #   {id: str, payload: {invoke}, ...}
                # Anything off that shape (non-dict item, missing/non-string
                # id, missing/non-dict payload) is a schema violation — skip
                # without ack so the gateway can retry/expire it. Log the
                # specific reason so operators can debug the gateway side.
                if not isinstance(item, dict):
                    _LOG.warning(
                        "skipping malformed pending item: not an object, got %s",
                        type(item).__name__,
                    )
                    continue
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id:
                    _LOG.warning(
                        "skipping malformed pending item: id missing or not a string (got %r)",
                        item_id,
                    )
                    continue
                payload = item.get("payload")
                if not isinstance(payload, dict):
                    _LOG.warning(
                        "skipping malformed pending item id=%s: "
                        "payload missing or non-dict (got %s)",
                        item_id,
                        type(payload).__name__,
                    )
                    continue
                await self._handle_invoke(ws, payload)
                await self._ack_pending(ws, item_id)
            if not has_more:
                return
        _LOG.warning(
            "node.pending.pull bailing after %d iterations (gateway still reports hasMore); "
            "remaining items will be redelivered on next connect",
            _PENDING_PULL_MAX_ITERATIONS,
        )

    async def _ack_pending(
        self, ws: websockets.asyncio.client.ClientConnection, invoke_id: str
    ) -> None:
        """Send ``node.pending.ack`` for a processed pending item.

        Waits for the matching response frame and logs a warning if the
        gateway returns ``ok: false`` so a silently-failed ack doesn't
        leave processed items queued for redelivery on the next connect.
        Frames that arrive with a different ``id`` (e.g. an interleaved
        ``node.invoke.request`` event) are dropped here — the full
        invoke-during-pull case is rare and the next connect's drain
        re-fires the missed item. A frame-correlation queue is the
        proper long-term fix and is out of scope for this bundle.

        Args:
            ws: The open WebSocket connection.
            invoke_id: The queue-item id to acknowledge.
        """
        ack = _make_req("node.pending.ack", {"ids": [invoke_id]})
        await ws.send(json.dumps(ack))
        try:
            async with asyncio.timeout(_ACK_RESPONSE_TIMEOUT_S):
                raw = await ws.recv()
        except TimeoutError:
            _LOG.warning(
                "node.pending.ack response timeout (%ss) for id=%s; "
                "drain continues but the gateway may redeliver on next connect",
                _ACK_RESPONSE_TIMEOUT_S,
                invoke_id,
            )
            return
        except (OSError, ConnectionError, websockets.exceptions.ConnectionClosed) as exc:
            _LOG.warning(
                "node.pending.ack connection closed before response for id=%s: %s",
                invoke_id,
                exc,
            )
            return
        msg: dict[str, Any] = json.loads(raw)
        if msg.get("type") != "res" or msg.get("id") != ack["id"]:
            # TODO: tiny one-frame pending buffer so an interleaved
            # node.invoke.request arriving here isn't lost. For now we
            # rely on the gateway redelivering on the next connect drain.
            _LOG.warning(
                "node.pending.ack got unexpected frame for id=%s (type=%r id=%r); "
                "next connect drain will retry if it was an ack failure",
                invoke_id,
                msg.get("type"),
                msg.get("id"),
            )
            return
        if not msg.get("ok"):
            _LOG.warning(
                "node.pending.ack rejected by gateway for id=%s: %r",
                invoke_id,
                msg.get("error"),
            )

    async def _event_loop(
        self,
        ws: websockets.asyncio.client.ClientConnection,
        relay: ChatRelay | None,
    ) -> None:
        """Process incoming gateway frames indefinitely.

        Routes ``res`` frames to the :class:`ChatRelay` for RPC correlation,
        ``session.*`` / ``chat*`` events for assistant reply capture, and
        ``node.invoke.request`` events to the command dispatcher.

        Args:
            ws: The open WebSocket connection.
            relay: The chat relay bound to this connection, or ``None`` on
                connections where ``chat_relay_enabled=False``.
        """
        async for raw in ws:
            msg: dict[str, Any] = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "res" and relay is not None and relay.handle_response(msg):
                continue

            if msg_type == "event":
                event = msg.get("event", "")
                if event == "node.invoke.request" and self._invoke_dispatch_enabled:
                    await self._handle_invoke(ws, msg.get("payload", {}))
                elif (
                    relay is not None
                    and isinstance(event, str)
                    and (event.startswith("session.") or event.startswith("chat"))
                ):
                    relay.handle_event(msg)

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
        invoke_id: str = str(payload.get("id", ""))
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

    def _set_runtime_connected(self, value: bool) -> None:
        """Write this client's role-specific connected flag on the runtime.

        Each role connection owns its own flag so the two reconnect loops
        cannot race a shared boolean. The runtime's ``gateway_connected``
        property derives from ``node_connected or operator_connected`` for
        the /health back-compat surface.
        """
        if self._runtime is None:
            return
        if self._role == "operator":
            self._runtime.operator_connected = value
        else:
            self._runtime.node_connected = value
