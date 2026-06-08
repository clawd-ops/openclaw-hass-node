"""Local HTTP API exposed by the OpenClaw Home Assistant node.

The API is intentionally small for the P2 vertical slice.  It gives the Home
Assistant companion integration a local surface to call and gives Rob a simple
thing to verify from add-on logs or curl-like diagnostics: health, ping,
read-only Home Assistant snapshot, and Assist turn forwarding placeholder.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any, Final

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from openclaw_node import __version__
from openclaw_node.chat_relay import ChatRelay, ChatRelayError
from openclaw_node.commands.dispatcher import UnknownCommandError, dispatch_async
from openclaw_node.config import NodeConfig
from openclaw_node.pairing import PairingState

_LOG: Final[logging.Logger] = logging.getLogger(__name__)
_JSON_HEADERS: Final[dict[str, str]] = {"Cache-Control": "no-store"}

# Paths reachable without an Authorization header. They never return secret
# state and are needed for HA add-on health checks / HACS config-flow
# discovery before the user has configured the shared token.
_UNAUTHED_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/health",
        "/v1/health",
        "/v1/conversation/info",
    }
)

# Allowlist for commands callable over the local HTTP surface (#88/3).
# The HACS shim never needs more than these for normal operation, and the
# gateway invokes powerful commands like fs.write / system.run over the WS
# path (operator-authorized). Restricting the HTTP surface limits the blast
# radius if `local_api_token` ever leaks.
_HTTP_COMMAND_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "ping",
        "system.which",
    }
)

_Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


class NodeRuntime:
    """Shared runtime state for the local HTTP API.

    Args:
        config: Immutable node configuration loaded at process start.

    Attributes:
        config: Runtime configuration.
        pairing_state: Current gateway pairing state. The gateway WS client can
            update this later; the HTTP API defaults to UNKNOWN until then.
    """

    def __init__(self, config: NodeConfig) -> None:
        """Initialise the runtime with UNKNOWN pairing state.

        Args:
            config: Runtime configuration for this process.
        """
        self.config = config
        self.pairing_state: PairingState = PairingState.UNKNOWN
        # Per-role liveness — the node runs two parallel gateway WS
        # connections (P5.13 #84) and either can be up while the other
        # is reconnecting. A single shared boolean would be racy
        # (last-writer-wins).
        self.node_connected: bool = False
        self.operator_connected: bool = False
        self.chat_relay: ChatRelay | None = None

    @property
    def gateway_connected(self) -> bool:
        """Back-compat: True when EITHER role connection is up.

        Used by the /health response. Specific surfaces (Assist) gate
        on the more precise flag they actually need.
        """
        return self.node_connected or self.operator_connected

    @property
    def is_paired(self) -> bool:
        """Return whether the gateway connection is currently paired.

        Returns:
            True when :attr:`pairing_state` is :attr:`PairingState.PAIRED`.
        """
        return self.pairing_state is PairingState.PAIRED


@web.middleware
async def _bearer_auth_middleware(request: web.Request, handler: _Handler) -> web.StreamResponse:
    """Reject requests missing/wrong bearer token.

    Fail-closed by default: the local HTTP API exposes the full ``fs.*`` /
    ``ha.*`` / ``system.*`` command surface, so an unconfigured token
    must NOT mean an open API. Only the paths in :data:`_UNAUTHED_PATHS`
    (``/health``, ``/v1/health``, ``/v1/conversation/info``) are reachable
    without a token — they're needed by HA add-on health checks and the
    HACS shim config-flow probe before the user has configured the
    shared token.

    When ``runtime.config.local_api_token`` is empty, every non-public
    path returns ``401 NO_TOKEN_CONFIGURED`` with a hint telling the
    operator which option to set. When set, every non-public endpoint
    must present a matching ``Authorization: Bearer <token>`` header;
    the compare uses :func:`hmac.compare_digest` to avoid leaking the
    token via timing.
    """
    runtime: NodeRuntime = request.app["runtime"]
    expected = runtime.config.local_api_token
    if request.path in _UNAUTHED_PATHS:
        return await handler(request)
    if not expected:
        return web.json_response(
            {
                "ok": False,
                "error": "NO_TOKEN_CONFIGURED",
                "response": (
                    "Local API token is not configured. Set "
                    "`local_api_token` in the add-on options (or "
                    "`OPENCLAW_LOCAL_API_TOKEN` standalone) before "
                    "calling the local API."
                ),
            },
            status=401,
            headers={**_JSON_HEADERS, "WWW-Authenticate": "Bearer"},
        )
    auth = request.headers.get("Authorization", "")
    scheme, _, presented = auth.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return web.json_response(
            {"ok": False, "error": "UNAUTHORIZED"},
            status=401,
            headers={**_JSON_HEADERS, "WWW-Authenticate": "Bearer"},
        )
    # Compare as bytes — hmac.compare_digest raises on non-ASCII str inputs,
    # and HA's `password?` option accepts arbitrary Unicode.
    if not hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
        return web.json_response(
            {"ok": False, "error": "FORBIDDEN"},
            status=403,
            headers=_JSON_HEADERS,
        )
    return await handler(request)


def create_app(runtime: NodeRuntime) -> web.Application:
    """Create the aiohttp application for the local node API.

    Args:
        runtime: Mutable runtime state shared with the gateway WS client.

    Returns:
        Configured :class:`aiohttp.web.Application`.
    """
    app = web.Application(middlewares=[_bearer_auth_middleware])
    app["runtime"] = runtime
    app.router.add_get("/health", health)
    app.router.add_get("/v1/health", health)
    app.router.add_post("/commands/ping", command_ping)
    app.router.add_post("/v1/commands/ping", command_ping)
    app.router.add_post("/v1/commands/{command:.+}", command_dispatch)
    app.router.add_get("/ha/snapshot", ha_snapshot)
    app.router.add_get("/v1/ha/snapshot", ha_snapshot)
    app.router.add_post("/assist/turn", assist_turn)
    app.router.add_post("/v1/conversation", assist_turn)
    app.router.add_get("/v1/conversation/info", conversation_info)
    return app


def _runtime(request: web.Request) -> NodeRuntime:
    """Return runtime state from an aiohttp request.

    Args:
        request: Incoming aiohttp request.

    Returns:
        The :class:`NodeRuntime` stored on the application.
    """
    runtime: NodeRuntime = request.app["runtime"]
    return runtime


def _safe_config(config: NodeConfig) -> dict[str, Any]:
    """Return non-secret config values safe for health responses.

    Args:
        config: Runtime configuration.

    Returns:
        Dict without tokens or pairing secrets.
    """
    data = asdict(config)
    for key in ("hass_token", "supervisor_token", "pairing_token"):
        data[key] = bool(data.get(key))
    data["data_dir"] = str(config.data_dir)
    return data


async def health(request: web.Request) -> web.Response:
    """Return node health and mode information.

    Args:
        request: Incoming aiohttp request.

    Returns:
        JSON response with version, mode, pairing state, and safe config.
    """
    runtime = _runtime(request)
    return web.json_response(
        {
            "ok": True,
            "version": __version__,
            "addon_mode": runtime.config.addon_mode,
            "pairing_state": runtime.pairing_state.name.lower(),
            "paired": runtime.is_paired,
            "config": _safe_config(runtime.config),
        },
        headers=_JSON_HEADERS,
    )


async def command_ping(request: web.Request) -> web.Response:
    """Handle the local ping endpoint.

    Args:
        request: Incoming aiohttp request.

    Returns:
        JSON pong payload.
    """
    params = await _json_body(request)
    return web.json_response(await dispatch_async("ping", params), headers=_JSON_HEADERS)


async def command_dispatch(request: web.Request) -> web.Response:
    """Dispatch a local command by URL path.

    Uses :func:`dispatch_async` so async handlers (every ``ha.*`` command
    and a growing share of the registry) work alongside sync ones.

    Args:
        request: Incoming aiohttp request with ``command`` path variable.

    Returns:
        JSON command result or a structured error.
    """
    command = request.match_info["command"]
    if command not in _HTTP_COMMAND_ALLOWLIST:
        # Defense-in-depth: the bearer token gates *access*, this allowlist
        # gates *blast radius*. Return UNKNOWN_COMMAND (not 403) so the
        # response shape matches a missing handler — the surface doesn't
        # advertise which commands exist behind it.
        return web.json_response(
            {"ok": False, "error": "UNKNOWN_COMMAND", "command": command},
            status=404,
            headers=_JSON_HEADERS,
        )
    params = await _json_body(request)
    try:
        result = await dispatch_async(command, params)
    except UnknownCommandError as exc:
        return web.json_response(
            {"ok": False, "error": "UNKNOWN_COMMAND", "command": exc.command},
            status=404,
            headers=_JSON_HEADERS,
        )
    return web.json_response({"ok": True, "result": result}, headers=_JSON_HEADERS)


async def _json_body(request: web.Request) -> dict[str, Any]:
    """Parse an optional JSON body as a dict.

    Args:
        request: Incoming request.

    Returns:
        Parsed dict, or an empty dict for empty bodies.

    Raises:
        web.HTTPBadRequest: If the JSON body is not an object.
    """
    if not request.can_read_body:  # pragma: no cover — GET/HEAD requests only
        return {}
    body = await request.json()
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="JSON body must be an object")
    return body


async def ha_snapshot(request: web.Request) -> web.Response:
    """Return a read-only Home Assistant status snapshot.

    The endpoint calls ``/api/config`` and ``/api/states`` using either the
    add-on Supervisor token or a standalone long-lived token. It never mutates
    Home Assistant.

    Args:
        request: Incoming aiohttp request.

    Returns:
        JSON snapshot. If HA is unreachable, returns HTTP 503 with a structured
        error instead of raising a traceback.
    """
    runtime = _runtime(request)
    config = runtime.config
    token = config.supervisor_token or config.hass_token
    if not config.hass_url or not token:
        return web.json_response(
            {
                "ok": False,
                "error": "HA_TOKEN_OR_URL_MISSING",
                "addon_mode": config.addon_mode,
            },
            status=503,
            headers=_JSON_HEADERS,
        )

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        timeout = aiohttp_timeout()
        async with ClientSession(timeout=timeout) as session:
            config_task = session.get(f"{config.hass_url.rstrip('/')}/api/config", headers=headers)
            states_task = session.get(f"{config.hass_url.rstrip('/')}/api/states", headers=headers)
            async with config_task as config_res, states_task as states_res:
                ha_config = await config_res.json() if config_res.ok else {}
                states = await states_res.json() if states_res.ok else []
                sample = states[0] if isinstance(states, list) and states else None
                status = 200 if config_res.ok and states_res.ok else 503
                return web.json_response(
                    {
                        "ok": config_res.ok and states_res.ok,
                        "ha_version": (
                            ha_config.get("version") if isinstance(ha_config, dict) else None
                        ),
                        "entity_count": len(states) if isinstance(states, list) else 0,
                        "sample_entity": sample,
                        "config_status": config_res.status,
                        "states_status": states_res.status,
                    },
                    status=status,
                    headers=_JSON_HEADERS,
                )
    except (TimeoutError, ClientError, OSError) as exc:
        _LOG.warning("HA snapshot failed: %s", exc)
        return web.json_response(
            {"ok": False, "error": "HA_REST_UNREACHABLE", "detail": str(exc)},
            status=503,
            headers=_JSON_HEADERS,
        )


def aiohttp_timeout() -> ClientTimeout:
    """Return the timeout object for outbound HA calls.

    Returns:
        A short :class:`aiohttp.ClientTimeout` suitable for health checks.
    """
    return ClientTimeout(total=8)


async def assist_turn(request: web.Request) -> web.Response:
    """Handle an Assist turn forwarded by the HA companion integration.

    When the ChatRelay is available (gateway connected + paired), relays
    the user's text into an OpenClaw agent session via ``chat.send`` and
    returns the assistant's reply. Falls back to diagnostic messages when
    the gateway connection is not ready.

    Args:
        request: Incoming aiohttp request.

    Returns:
        JSON response with the assistant ``response`` text and runtime
        diagnostics.
    """
    runtime = _runtime(request)
    body = await _json_body(request)
    text = str(body.get("text", "")).strip()
    conversation_id = str(body.get("conversation_id", ""))
    language = str(body.get("language", "en"))

    if not runtime.is_paired:
        return _assist_error(
            runtime,
            "OpenClaw Node is installed and reachable, but it is not paired "
            "to the OpenClaw gateway yet. Pair the node, then retry Assist.",
            text,
        )

    if not runtime.operator_connected:
        return _assist_error(
            runtime,
            "OpenClaw Node is paired but the operator gateway connection "
            "(required for ChatRelay) is not currently up.",
            text,
        )

    relay = runtime.chat_relay
    if relay is None:
        return _assist_error(
            runtime,
            "OpenClaw Node is connected but the chat relay is not initialised.",
            text,
        )

    if not text:
        return _assist_error(runtime, "Empty text — nothing to relay.", text)

    if not conversation_id:
        return _assist_error(
            runtime,
            "Missing conversation_id — the HA shim must send one.",
            text,
        )

    try:
        reply = await relay.relay_turn(conversation_id, text, language)
    except ChatRelayError as exc:
        _LOG.warning("Assist relay failed: %s %s", exc.code, exc.message)
        return web.json_response(
            {
                "ok": False,
                "paired": True,
                "gateway_connected": True,
                "response": f"Relay error: {exc.code}",
                "echo": text,
                "error": exc.code,
            },
            status=502,
            headers=_JSON_HEADERS,
        )
    except Exception as exc:
        _LOG.exception("Unexpected error in assist relay: %s", exc)
        return web.json_response(
            {
                "ok": False,
                "paired": True,
                "gateway_connected": True,
                "response": "Internal relay error.",
                "echo": text,
                "error": "INTERNAL_ERROR",
            },
            status=500,
            headers=_JSON_HEADERS,
        )

    return web.json_response(
        {
            "ok": True,
            "paired": True,
            "gateway_connected": True,
            "response": reply,
            "echo": text,
        },
        status=200,
        headers=_JSON_HEADERS,
    )


def _assist_error(runtime: NodeRuntime, message: str, echo: str) -> web.Response:
    """Return a structured diagnostic error for Assist turns.

    Args:
        runtime: Current node runtime.
        message: Human-readable diagnostic message.
        echo: The user's original input text.

    Returns:
        JSON response with ``ok=False`` and runtime diagnostics.
    """
    return web.json_response(
        {
            "ok": False,
            "paired": runtime.is_paired,
            "gateway_connected": runtime.gateway_connected,
            "response": message,
            "echo": echo,
        },
        status=200,
        headers=_JSON_HEADERS,
    )


async def conversation_info(request: web.Request) -> web.Response:
    """Return Assist-shim diagnostic metadata.

    Used by the HACS shim's config flow to show pairing and gateway status
    without sending a full conversation turn.

    Args:
        request: Incoming aiohttp request.

    Returns:
        JSON response with node version, pairing state, and connection status.
    """
    runtime = _runtime(request)
    return web.json_response(
        {
            "ok": True,
            "version": __version__,
            "paired": runtime.is_paired,
            "pairing_state": runtime.pairing_state.name,
            "gateway_connected": runtime.gateway_connected,
        },
        status=200,
        headers=_JSON_HEADERS,
    )


async def run_http_api(  # pragma: no cover
    runtime: NodeRuntime,
    host: str = "0.0.0.0",  # nosec B104 - intentional: HA add-on binds inside container network
    port: int = 8099,
) -> None:
    """Run the local HTTP API until cancelled.

    Not unit-testable: binds a real TCP port and blocks on an asyncio.Event.
    Covered by integration smoke tests.

    Args:
        runtime: Runtime state shared with other subsystems.
        host: Interface to bind.
        port: TCP port to listen on.
    """
    app = create_app(runtime)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    _LOG.info("openclaw-node local api listening on http://%s:%d", host, port)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
