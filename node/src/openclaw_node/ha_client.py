"""Async REST client for the Home Assistant API.

Thin wrapper around :mod:`aiohttp` that picks the correct base URL and bearer
token from the environment.  In add-on mode, the Supervisor token addresses
the Supervisor API; in standalone mode, the long-lived access token addresses
the user's HA instance directly.

The module is environment-driven (reads HASS_URL/HASS_TOKEN/SUPERVISOR_TOKEN
on each call) rather than carrying NodeConfig so that command handlers stay
config-free.  Tests can override the env via :func:`pytest.MonkeyPatch.setenv`.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Final

import aiohttp

_LOG: Final[logging.Logger] = logging.getLogger(__name__)
_DEFAULT_TIMEOUT_S: Final[float] = 10.0


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


def _ha_url() -> str:
    """Return the Home Assistant base URL.

    Returns:
        The configured HA URL.

    Raises:
        HAClientError: If no URL is configured.
    """
    if os.environ.get("SUPERVISOR_TOKEN"):
        return os.environ.get("HASS_URL", "http://supervisor/core")
    url = os.environ.get("HASS_URL", "")
    if not url:
        raise HAClientError("HA_NOT_CONFIGURED", "HASS_URL is not set")
    return url


def _ha_token() -> str:
    """Return the bearer token for the HA REST API.

    Raises:
        HAClientError: If no token is configured.
    """
    token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASS_TOKEN", "")
    if not token:
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
