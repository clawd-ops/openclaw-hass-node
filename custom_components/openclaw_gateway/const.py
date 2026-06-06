"""Constants for the openclaw_gateway custom component."""

from __future__ import annotations

from typing import Final

DOMAIN: Final[str] = "openclaw_gateway"
DEFAULT_SOCKET_URL: Final[str] = "http://a0d7b954-openclaw-hass-node:8099"
CONF_SOCKET_URL: Final[str] = "socket_url"
CONVERSATION_ENDPOINT: Final[str] = "/v1/conversation"
