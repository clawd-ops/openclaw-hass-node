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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import websockets
import websockets.asyncio.client

from openclaw_node import __version__
from openclaw_node.chat_relay import ChatRelay
from openclaw_node.commands.dispatcher import UnknownCommandError, dispatch_async
from openclaw_node.config import NodeConfig
from openclaw_node.identity import DeviceIdentity
from openclaw_node.pairing import PairingError, PairingMachine, PairingState

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
    "ha.addon_logs",
    "ha.list_addons",
    "ha.addon_info",
    "ha.addon_stats",
    "ha.addon_changelog",
    "ha.addon_documentation",
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
# When the gateway rejects auth with AUTH_RATE_LIMITED, hammering at the
# base 5s interval just keeps extending the rate-limit window. Use
# exponential backoff starting at this value, capped by the max below.
_RATE_LIMITED_BACKOFF_BASE_S: Final[float] = 30.0
_RATE_LIMITED_BACKOFF_MAX_S: Final[float] = 300.0
_RATE_LIMITED_BACKOFF_FACTOR: Final[float] = 2.0
_ACK_RESPONSE_TIMEOUT_S: Final[float] = 5.0
# Timeout for awaiting a specific `res` frame by request id when the
# gateway may interleave events with the response. Bounded so a missing
# response cannot wedge the connect handshake forever.
_RES_CORRELATION_TIMEOUT_S: Final[float] = 5.0
# Per-await cap on intermediate frames consumed while waiting for a
# specific `res` id. A pathological flood of events shouldn't trap the
# loop here indefinitely.
_RES_CORRELATION_MAX_FRAMES: Final[int] = 256
# Sibling roles whose tokens may be persisted from auth.deviceTokens. Used
# as a filename component, so the set is strictly allowlisted to prevent
# a malformed/forged role string from escaping the device-token namespace.
_PERSISTABLE_SIBLING_ROLES: Final[frozenset[str]] = frozenset({"node", "operator"})


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


def _decode_error_retry_after_ms(raw: Any) -> float | None:
    """Return gateway-provided retry delay from a ResponseFrame.error object."""
    if not isinstance(raw, dict):
        return None
    retry_after_ms = raw.get("retryAfterMs")
    if retry_after_ms is None:
        details = raw.get("details")
        retry_after_ms = details.get("retryAfterMs") if isinstance(details, dict) else None
    if not isinstance(retry_after_ms, int | float) or retry_after_ms < 0:
        return None
    return float(retry_after_ms)


def _format_retry_at_utc(delay_s: float, *, now: datetime | None = None) -> str:
    """Return the UTC wall-clock time when a reconnect will be attempted."""
    base = now or datetime.now(UTC)
    retry_at = base + timedelta(seconds=max(0.0, delay_s))
    return retry_at.isoformat(timespec="seconds").replace("+00:00", "Z")


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
        self._rate_limited_delay_s: float = 0.0

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
                delay = self._next_reconnect_delay(exc)
                retry_at = _format_retry_at_utc(delay)
                _LOG.error(
                    "Gateway connection lost: %s - reconnecting in %.1fs (retry at %s)",
                    exc,
                    delay,
                    retry_at,
                )
                self._maybe_drop_invalid_device_token(exc)
                self._pairing.on_reconnect()
                self._notify_pairing_state()
                if self._runtime is not None:
                    self._set_runtime_connected(False)
                await asyncio.sleep(delay)

    def _next_reconnect_delay(self, exc: BaseException) -> float:
        """Return the wait before the next reconnect attempt.

        Default is the flat :data:`_RECONNECT_DELAY_S`. When the gateway
        rejects auth with ``AUTH_RATE_LIMITED`` (or recommends
        ``wait_then_retry``), grow the delay exponentially up to
        :data:`_RATE_LIMITED_BACKOFF_MAX_S` so the node stops extending the
        rate-limit window with retries. On a successful connect the caller
        resets the backoff to zero.
        """
        if isinstance(exc, PairingError) and exc.retry_after_ms is not None:
            self._rate_limited_delay_s = 0.0
            return exc.retry_after_ms / 1000.0

        haystack = repr(exc)
        is_rate_limited = "AUTH_RATE_LIMITED" in haystack or "wait_then_retry" in haystack
        if not is_rate_limited:
            self._rate_limited_delay_s = 0.0
            return _RECONNECT_DELAY_S
        if self._rate_limited_delay_s <= 0.0:
            self._rate_limited_delay_s = _RATE_LIMITED_BACKOFF_BASE_S
        else:
            self._rate_limited_delay_s = min(
                self._rate_limited_delay_s * _RATE_LIMITED_BACKOFF_FACTOR,
                _RATE_LIMITED_BACKOFF_MAX_S,
            )
        return self._rate_limited_delay_s

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
        retry_after_ms: float | None = None if ok else _decode_error_retry_after_ms(error_raw)
        self._pairing.on_connect_response(
            ok=ok,
            payload=payload,
            error=error,
            error_message=error_message,
            retry_after_ms=retry_after_ms,
        )
        self._notify_pairing_state()
        if ok:
            # Auth succeeded — drop any rate-limit backoff so a transient
            # disconnect after a healthy session doesn't carry the stale
            # rate-limited delay forward.
            self._rate_limited_delay_s = 0.0
        # On successful connect the gateway may issue device tokens in two
        # shapes:
        #   - hello-ok.auth.deviceToken   (singular): token for THIS role.
        #   - hello-ok.auth.deviceTokens  (plural map): {role: token} pairs,
        #     typically emitted during a dual-role bootstrap so the node-role
        #     connect can also seed the operator-role file. Without this
        #     handling, the operator role only learns its token when its own
        #     connect succeeds — which may not happen if the original
        #     bootstrap was single-use (#98 part 3 / Codex review on
        #     auth.deviceTokens).
        if ok and payload is not None:
            auth = payload.get("auth") or {}
            issued = auth.get("deviceToken")
            if isinstance(issued, str) and issued and issued != self._device_token:
                _LOG.info("Gateway issued a new device_token; persisting.")
                self._device_token = issued
                self._persist_device_token(issued)
            self._persist_dual_role_tokens(auth.get("deviceTokens"))

    def _persist_dual_role_tokens(self, tokens: Any) -> None:
        """Persist any sibling-role tokens carried in ``auth.deviceTokens``.

        Best-effort: parses entries like ``{role: token}`` or ``[{role, token}, ...]``
        and writes each role's token to ``data_dir/device-token.<role>``,
        respecting the same on-disk safety as :meth:`_persist_device_token`
        (atomic replace, 0o600, no symlink follow). Errors are logged and
        swallowed — failing to seed the sibling shouldn't block this
        connection.

        The current role's own token is skipped here; the singular
        ``deviceToken`` branch already handled it via
        :meth:`_persist_device_token`.
        """
        if not tokens:
            return
        entries: list[tuple[str, str]] = []
        if isinstance(tokens, dict):
            for role, tok in tokens.items():
                if isinstance(role, str) and isinstance(tok, str) and role and tok:
                    entries.append((role, tok))
        elif isinstance(tokens, list):
            for raw in tokens:
                if not isinstance(raw, dict):
                    continue
                role = raw.get("role")
                tok = raw.get("token")
                if isinstance(role, str) and isinstance(tok, str) and role and tok:
                    entries.append((role, tok))
        for role, tok in entries:
            if role == self._role:
                continue  # already handled by the singular deviceToken branch
            # Allowlist sibling roles. The role string is used as a filename
            # suffix, so an attacker-controlled `auth.deviceTokens` with a
            # role like `../node-key.json` would otherwise escape the
            # device-token namespace and clobber arbitrary files under
            # data_dir. Only the two roles the addon actually participates in
            # are accepted.
            if role not in _PERSISTABLE_SIBLING_ROLES:
                _LOG.warning("ignoring deviceTokens entry with unsupported role=%r", role)
                continue
            sibling_path = self._config.device_token_path_for(role)
            try:
                GatewayClient._atomic_write_token(sibling_path, tok)
                _LOG.info(
                    "Gateway issued a %s device_token via deviceTokens; persisted to %s",
                    role,
                    sibling_path,
                )
            except OSError as exc:
                _LOG.warning(
                    "Could not persist sibling %s deviceToken to %s: %s",
                    role,
                    sibling_path,
                    exc,
                )

    @staticmethod
    def _atomic_write_token(path: Path, token: str) -> None:
        """Write *token* to *path* with hardened on-disk safety.

        - O_NOFOLLOW on the temp open so an attacker-planted symlink at
          ``<path>.tmp`` is rejected rather than written through.
        - ``os.fchmod`` immediately after open to force 0o600 even if the
          temp file already existed at a looser mode.
        - ``fsync`` data, ``replace`` atomically, then clean up the temp
          file on a failed replace.
        - Refuse to ``chmod`` the final path if it is a symlink.

        Shared by ``_persist_device_token`` (current role) and
        ``_persist_dual_role_tokens`` (sibling roles) so they apply the
        same hardening, and by the startup legacy-migration path in
        ``__main__`` so the migrated file lands at 0o600 instead of
        carrying forward whatever mode the legacy file had.
        """
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
        if not path.is_symlink():
            with contextlib.suppress(OSError):
                path.chmod(0o600)

    def _persist_device_token(self, token: str) -> None:
        """Atomically write THIS client's role token to ``self._token_persist_path``.

        Thin wrapper that delegates to :meth:`_atomic_write_token`, which
        owns the on-disk safety (O_NOFOLLOW, 0o600, fsync + atomic replace,
        symlink refusal). Kept as a named method because the call sites
        treat persistence of the current role's token as a distinct concept
        from seeding a sibling role's token.
        """
        GatewayClient._atomic_write_token(self._token_persist_path, token)

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

    async def _await_res(
        self,
        ws: websockets.asyncio.client.ClientConnection,
        req_id: str,
        label: str,
    ) -> dict[str, Any] | None:
        """Wait for the ``res`` frame matching *req_id*, tolerating events.

        The gateway may interleave unsolicited events (e.g. ``connect.approved``,
        ``node.invoke.request``) between the request and its response. Naively
        treating the next ``ws.recv()`` as the response misreads an event as a
        failed response — the bug behind the ``node.pending.pull failed: None``
        log line on b#98 (the event has no ``ok`` field, so ``msg.get("ok")``
        is falsy and the code declares the call failed).

        Behaviour:
          - Non-``res`` frames (events, etc.) are logged at debug and dropped.
            They are re-delivered to the event loop on the next reconnect; the
            connect-handshake path intentionally stays focused on pairing +
            pending drain. A small message-buffer queue is the proper long-term
            home for "process events received during handshake," but a buffer
            without a reader leaks; the redelivery contract closes the gap
            without growing surface area.
          - ``res`` frames with a different ``id`` are also dropped with a
            warning (likely a stale response from a prior request).
          - Bounded by ``_RES_CORRELATION_MAX_FRAMES`` and
            ``_RES_CORRELATION_TIMEOUT_S`` so a misbehaving gateway cannot
            wedge the handshake.

        Returns:
            The matching ``res`` frame as a dict, or ``None`` on timeout or
            when the bound is exhausted (caller treats as a soft failure).
        """
        try:
            async with asyncio.timeout(_RES_CORRELATION_TIMEOUT_S):
                for _ in range(_RES_CORRELATION_MAX_FRAMES):
                    raw = await ws.recv()
                    msg: dict[str, Any] = json.loads(raw)
                    msg_type = msg.get("type")
                    if msg_type == "res":
                        if msg.get("id") == req_id:
                            return msg
                        _LOG.debug(
                            "%s: dropping stale res id=%r while awaiting %s",
                            label,
                            msg.get("id"),
                            req_id,
                        )
                        continue
                    if msg_type == "event":
                        _LOG.debug(
                            "%s: dropping interleaved event %r while awaiting res id=%s",
                            label,
                            msg.get("event"),
                            req_id,
                        )
                        continue
                    # Frames with no canonical type field are accepted as the
                    # response. The production gateway always sets type=res,
                    # so this lenient path only matters for test fakes and
                    # legacy/intermediate protocol shapes.
                    return msg
                _LOG.warning(
                    "%s: exhausted %d frames waiting for res id=%s; treating as soft failure",
                    label,
                    _RES_CORRELATION_MAX_FRAMES,
                    req_id,
                )
                return None
        except TimeoutError:
            _LOG.warning(
                "%s: timed out after %ss waiting for res id=%s; treating as soft failure",
                label,
                _RES_CORRELATION_TIMEOUT_S,
                req_id,
            )
            return None

    async def _pull_pending(self, ws: websockets.asyncio.client.ClientConnection) -> None:
        """Drain queued invokes by sending ``node.pending.pull``.

        Called once after each successful connect so that any invokes that
        arrived while the node was offline are processed.

        Canonical gateway schema (verified 2026-06-19 against
        ``/app/dist/schema-BwaBORnA.js`` — Issue #108):

        - Params: ``NodeListParamsSchema`` — empty object ``{}``. The
          gateway used to accept ``maxItems`` but no longer does;
          ``additionalProperties: false`` rejects it as INVALID_REQUEST.
          The drain is single-shot — every queued action for this node
          comes back in one frame.
        - Result: ``{nodeId: str, actions: NodePendingActionItem[]}`` where
          each action is a FLAT invoke envelope ``{id, command,
          paramsJSON, enqueuedAtMs}`` (the ``id`` is the queue-item id,
          NOT an inner invoke id — they happen to be the same dispatch
          target). No ``hasMore`` / paging.

        Older addon builds called ``node.pending.pull`` with
        ``{maxItems: 50}`` and parsed an ``{items: [...], hasMore}`` envelope.
        Both shapes were wrong against the current gateway; the symptom was
        ``INVALID_REQUEST: unexpected property "maxItems"``.

        Args:
            ws: The open WebSocket connection.
        """
        req = _make_req("node.pending.pull", {})
        await ws.send(json.dumps(req))
        msg = await self._await_res(ws, req["id"], "node.pending.pull")
        if msg is None:
            # Correlation timed out; treat as empty drain and let the
            # event loop continue. Whatever was queued will redeliver
            # on the next connect.
            return
        if not msg.get("ok"):
            _LOG.warning("node.pending.pull failed: %r", msg.get("error"))
            return
        payload_obj_raw = msg.get("payload")
        if not isinstance(payload_obj_raw, dict):
            _LOG.warning(
                "node.pending.pull returned non-object payload (got %s); treating as empty drain",
                type(payload_obj_raw).__name__,
            )
            return
        # Accept both the canonical "actions" field and the legacy
        # "items" field so the addon survives a gateway that hasn't
        # rolled out the rename yet (or a future re-rename).
        actions_raw = payload_obj_raw.get("actions")
        if actions_raw is None:
            actions_raw = payload_obj_raw.get("items", [])
        actions: list[Any] = actions_raw or []
        _LOG.debug("Pulled %d pending actions", len(actions))
        for action in actions:
            # Canonical action envelope is the invoke payload itself:
            #   {id, command, paramsJSON, enqueuedAtMs}
            # Anything off that shape is a schema violation -- skip without
            # ack so the gateway can retry/expire.
            if not isinstance(action, dict):
                _LOG.warning(
                    "skipping malformed pending action: not an object, got %s",
                    type(action).__name__,
                )
                continue
            action_id = action.get("id")
            if not isinstance(action_id, str) or not action_id:
                _LOG.warning(
                    "skipping malformed pending action: id missing or not a string (got %r)",
                    action_id,
                )
                continue
            if not isinstance(action.get("command"), str) or not action["command"]:
                _LOG.warning(
                    "skipping malformed pending action id=%s: command missing or not a string",
                    action_id,
                )
                continue
            # Canonical pending.pull may carry `paramsJSON: null` for
            # commands without params (NodeInvokeParamsSchema.params is
            # Optional; the gateway maps that to JSON null). The live
            # invoke-event schema requires a non-empty string, so
            # `_decode_invoke_params` rejects null. Normalize to "{}" so
            # the dispatcher sees an empty-params invoke instead of
            # INVALID_PARAMS — otherwise the ack below would silently drop
            # a valid queued no-param invoke.
            params_json = action.get("paramsJSON")
            if params_json is None or params_json == "":
                action = {**action, "paramsJSON": "{}"}
            await self._handle_invoke(ws, action)
            await self._ack_pending(ws, action_id)

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
