"""Async REST client for the Home Assistant API.

Thin wrapper around :mod:`aiohttp` that picks the correct base URL and bearer
token from the environment.  In add-on mode, the Supervisor token addresses
the Supervisor API; in standalone mode, the long-lived access token addresses
the user's HA instance directly.

The module is environment-driven (reads HASS_URL/HASS_TOKEN/SUPERVISOR_TOKEN
on each call) rather than carrying NodeConfig so that command handlers stay
config-free.  Tests can override the env via :func:`pytest.MonkeyPatch.setenv`.

Add-on mode detection mirrors :func:`openclaw_node.config._is_addon_mode`:
``SUPERVISOR_TOKEN`` is the primary signal, but a writable ``/data`` directory
is an accepted fallback (Supervisor may not inject the token despite config
flags).  When addon mode is detected without ``SUPERVISOR_TOKEN``, the HA URL
defaults to ``http://homeassistant`` and a ``HASS_TOKEN`` must be set.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Final

import aiohttp

_LOG: Final[logging.Logger] = logging.getLogger(__name__)
_DEFAULT_TIMEOUT_S: Final[float] = 10.0
_SUPERVISOR_TEXT_MAX_BYTES: Final[int] = 1_048_576


class HAClientError(Exception):
    """Raised when the HA REST call fails (network, HTTP, or auth)."""

    def __init__(self, code: str, message: str) -> None:
        """Initialise with a stable error code and human message.

        Args:
            code: Stable error code suitable for use in the wire result.
            message: Human-readable detail (for logs and responses).
        """
        super().__init__(message)
        self.code = code
        self.message = message


def _is_addon_mode() -> bool:
    """Return True when running inside a Home Assistant add-on."""
    if os.environ.get("SUPERVISOR_TOKEN"):
        return True
    data = Path("/data")
    return data.is_dir() and os.access(data, os.W_OK)


def _ha_url() -> str:
    """Return the Home Assistant base URL.

    Returns:
        The configured HA URL.

    Raises:
        HAClientError: If no URL is configured.
    """
    if os.environ.get("SUPERVISOR_TOKEN"):
        # Always use the Supervisor internal URL when the Supervisor token is
        # present. Never combine a Supervisor-issued token with a user-supplied
        # URL -- that would send a privileged token to an arbitrary endpoint.
        return "http://supervisor/core"
    if _is_addon_mode():
        return os.environ.get("HASS_URL", "http://homeassistant")
    url = os.environ.get("HASS_URL", "")
    if not url:
        raise HAClientError("HA_NOT_CONFIGURED", "HASS_URL is not set")
    return url


def _ha_token() -> str:
    """Return the bearer token for the HA REST API.

    In add-on mode with SUPERVISOR_TOKEN, uses that token. In add-on mode
    without SUPERVISOR_TOKEN (``/data`` fallback), or standalone mode,
    requires HASS_TOKEN.

    Raises:
        HAClientError: If no token is configured.
    """
    token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASS_TOKEN", "")
    if not token:
        if _is_addon_mode():
            raise HAClientError(
                "HA_NOT_CONFIGURED",
                "Running in add-on mode but SUPERVISOR_TOKEN is missing and "
                "HASS_TOKEN is not set. Set HASS_TOKEN or check add-on config "
                "flags (hassio_api/homeassistant_api/auth_api).",
            )
        raise HAClientError("HA_NOT_CONFIGURED", "No HA token configured")
    return token


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_ha_token()}",
        "Content-Type": "application/json",
    }


async def ha_get(path: str, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> Any:
    """GET *path* on the HA REST API and return the JSON-decoded body.

    Args:
        path: The path component, e.g. ``"/api/states"``.
        timeout_s: Per-request timeout.

    Returns:
        The decoded JSON body (list, dict, or scalar).

    Raises:
        HAClientError: On network, HTTP non-2xx, or auth failure.
    """
    base = _ha_url().rstrip("/")
    headers = _headers()
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    url = f"{base}{path}"
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(url, headers=headers) as resp,
        ):
            return await _decode(resp)
    except aiohttp.ClientError as exc:
        _LOG.error("ha_get network error %s: %s", url, exc)
        raise HAClientError("HA_NETWORK", f"Network error contacting HA: {exc}") from exc


async def ha_post(
    path: str,
    body: dict[str, Any] | list[Any] | None = None,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> Any:
    """POST JSON *body* to *path* on the HA REST API.

    Args:
        path: The path component.
        body: The JSON body to send.
        timeout_s: Per-request timeout.

    Returns:
        The decoded JSON body of the response.

    Raises:
        HAClientError: On network, HTTP non-2xx, or auth failure.
    """
    base = _ha_url().rstrip("/")
    headers = _headers()
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    url = f"{base}{path}"
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(url, headers=headers, json=body) as resp,
        ):
            return await _decode(resp)
    except aiohttp.ClientError as exc:
        _LOG.error("ha_post network error %s: %s", url, exc)
        raise HAClientError("HA_NETWORK", f"Network error contacting HA: {exc}") from exc


async def ha_ws_call(
    msg_type: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> Any:
    """Make a single authenticated WebSocket call to the HA WS API.

    Connects, completes the auth handshake, sends one request (id=1), awaits
    the result, then closes.  Suitable for one-shot registry list calls.

    Args:
        msg_type: The WS message type, e.g. ``"config/area_registry/list"``.
        payload: Optional extra fields merged into the request (after id/type).
        timeout_s: Per-call total timeout.

    Returns:
        The ``result`` field from the WS success response.

    Raises:
        HAClientError: On network error, auth rejection, or WS-level error.
    """
    base = _ha_url().rstrip("/")
    ws_url = base.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
    token = _ha_token()
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.ws_connect(ws_url) as ws,
        ):
            first = await ws.receive_json()
            if first.get("type") != "auth_required":
                raise HAClientError(
                    "HA_WS_ERROR",
                    f"Expected auth_required, got {first.get('type')!r}",
                )
            await ws.send_json({"type": "auth", "access_token": token})
            auth_resp = await ws.receive_json()
            if auth_resp.get("type") != "auth_ok":
                raise HAClientError("HA_AUTH", "WS authentication rejected")
            request: dict[str, Any] = {"id": 1, "type": msg_type}
            if payload:
                request.update(payload)
            await ws.send_json(request)
            resp = await ws.receive_json()
            if not resp.get("success", False):
                err = resp.get("error", {})
                code = str(err.get("code", "WS_ERROR")).upper()
                detail = str(err.get("message", "WS call failed"))
                raise HAClientError(f"HA_{code}", detail)
            return resp.get("result")
    except aiohttp.ClientError as exc:
        _LOG.error("ha_ws_call network error %s: %s", msg_type, exc)
        raise HAClientError("HA_NETWORK", f"WS network error: {exc}") from exc


async def supervisor_get_text(
    path: str,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_bytes: int = _SUPERVISOR_TEXT_MAX_BYTES,
) -> str:
    """GET *path* on the Supervisor REST API and return bounded text.

    Used for endpoints that return non-JSON payloads (notably ``/addons/<slug>/logs``,
    which returns plain text journald output). Requires ``SUPERVISOR_TOKEN`` — the
    long-lived HA bearer token cannot reach the Supervisor proper. For large
    responses, only the trailing ``max_bytes`` are retained.

    Args:
        path: Path under the Supervisor root, e.g. ``"/addons/self/logs"``.
            Must begin with ``/``.
        timeout_s: Per-request timeout.
        max_bytes: Maximum trailing bytes to retain before UTF-8 decoding.

    Returns:
        The bounded response body as text.

    Raises:
        HAClientError: ``SUPERVISOR_UNAVAILABLE`` when ``SUPERVISOR_TOKEN`` is not
            set; otherwise network/HTTP error codes mirroring :func:`ha_get`.
    """
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        raise HAClientError(
            "SUPERVISOR_UNAVAILABLE",
            "Supervisor API requires SUPERVISOR_TOKEN; not set in this environment.",
        )
    url = f"http://supervisor{path}"
    headers = {"Authorization": f"Bearer {token}"}
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(url, headers=headers) as resp,
        ):
            if resp.status == 401:
                raise HAClientError("HA_AUTH", "Supervisor rejected the token (401)")
            if resp.status == 404:
                raise HAClientError("HA_NOT_FOUND", f"Supervisor returned 404 for {path}")
            if resp.status >= 400:
                body = (await resp.text())[:512]
                raise HAClientError("HA_HTTP_ERROR", f"Supervisor returned {resp.status}: {body}")
            tail = bytearray()
            async for chunk in resp.content.iter_chunked(65_536):
                tail.extend(chunk)
                if len(tail) > max_bytes:
                    del tail[: len(tail) - max_bytes]
            return tail.decode("utf-8", errors="replace")
    except aiohttp.ClientError as exc:
        _LOG.error("supervisor_get_text network error %s: %s", url, exc)
        raise HAClientError("HA_NETWORK", f"Network error contacting Supervisor: {exc}") from exc


async def supervisor_get_json(path: str, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> Any:
    """GET *path* on the Supervisor REST API and return the decoded JSON body.

    Companion to :func:`supervisor_get_text` for endpoints that return JSON
    (e.g. ``/addons``). Same auth/URL safety rules: requires ``SUPERVISOR_TOKEN``
    and the URL is always ``http://supervisor{path}`` (never combined with a
    user-supplied URL).

    Args:
        path: Path under the Supervisor root, e.g. ``"/addons"``. Must begin
            with ``/``.
        timeout_s: Per-request timeout.

    Returns:
        The decoded JSON body (typically a ``dict`` with a ``result`` field).

    Raises:
        HAClientError: ``SUPERVISOR_UNAVAILABLE`` when ``SUPERVISOR_TOKEN`` is
            not set; otherwise network/HTTP error codes mirroring
            :func:`supervisor_get_text`. ``HA_BAD_RESPONSE`` if the body is not
            valid JSON.
    """
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        raise HAClientError(
            "SUPERVISOR_UNAVAILABLE",
            "Supervisor API requires SUPERVISOR_TOKEN; not set in this environment.",
        )
    url = f"http://supervisor{path}"
    headers = {"Authorization": f"Bearer {token}"}
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(url, headers=headers) as resp,
        ):
            if resp.status == 401:
                raise HAClientError("HA_AUTH", "Supervisor rejected the token (401)")
            if resp.status == 404:
                raise HAClientError("HA_NOT_FOUND", f"Supervisor returned 404 for {path}")
            if resp.status >= 400:
                body = (await resp.text())[:512]
                raise HAClientError("HA_HTTP_ERROR", f"Supervisor returned {resp.status}: {body}")
            try:
                return await resp.json()
            except (ValueError, aiohttp.ContentTypeError) as exc:
                raise HAClientError(
                    "HA_BAD_RESPONSE", f"Supervisor returned non-JSON body for {path}"
                ) from exc
    except aiohttp.ClientError as exc:
        _LOG.error("supervisor_get_json network error %s: %s", url, exc)
        raise HAClientError("HA_NETWORK", f"Network error contacting Supervisor: {exc}") from exc


async def _decode(resp: aiohttp.ClientResponse) -> Any:
    if resp.status == 401:
        raise HAClientError("HA_AUTH", "HA rejected the bearer token (401)")
    if resp.status == 404:
        raise HAClientError("HA_NOT_FOUND", f"HA returned 404 for {resp.url.path}")
    if resp.status >= 400:
        text = (await resp.text())[:512]
        raise HAClientError("HA_HTTP_ERROR", f"HA returned {resp.status}: {text}")
    if resp.content_type and "json" in resp.content_type:
        return await resp.json()
    return await resp.text()
