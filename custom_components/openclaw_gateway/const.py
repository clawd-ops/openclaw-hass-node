"""Constants for the openclaw_gateway custom component."""

from __future__ import annotations

from typing import Final

DOMAIN: Final[str] = "openclaw_gateway"
# Hostname Supervisor exposes for the add-on inside the add-on network.
# The ``a0d7b954-`` prefix is the deterministic-but-install-specific hash
# Supervisor derives from the repository URL — it's NOT stable across
# every HA install, so this is a starting suggestion the config flow
# can override. Users running the node as a standalone Docker container
# point this at ``http://<node-host>:8099`` instead.
DEFAULT_SOCKET_URL: Final[str] = "http://a0d7b954-openclaw-hass-node:8099"
CONF_SOCKET_URL: Final[str] = "socket_url"
CONF_API_TOKEN: Final[str] = "local_api_token"
CONVERSATION_ENDPOINT: Final[str] = "/v1/conversation"
