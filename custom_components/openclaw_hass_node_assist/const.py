"""Constants for the openclaw_hass_node_assist custom component."""

from __future__ import annotations

from typing import Final

DOMAIN: Final[str] = "openclaw_hass_node_assist"
BOOTSTRAP_ENDPOINT: Final[str] = "/v1/bootstrap"
BOOTSTRAP_CLAIM_ENDPOINT: Final[str] = "/v1/bootstrap/claim"
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
CONVERSATION_INFO_ENDPOINT: Final[str] = "/v1/conversation/info"

# Probe candidates tried at config-flow time so the user doesn't have to
# guess the install-specific Supervisor hostname (#88/1). Ordering: slug
# form first (Supervisor's friendly DNS alias for HA add-ons), then the
# dashed repository-hash form, then the underscored Docker container name,
# then a local bridge fallback for standalone Docker installs. The
# PROBE_TIMEOUT is intentionally short — config flow blocks the UI while
# probing.
PROBE_CANDIDATE_HOSTS: Final[tuple[str, ...]] = (
    "openclaw-hass-node",
    "a0d7b954-openclaw-hass-node",
    "openclaw_hass_node",
    "localhost",
    "homeassistant.local",
)
PROBE_TIMEOUT_S: Final[float] = 1.0
NODE_LOCAL_PORT: Final[int] = 8099

# Image description (see ``vision.py``).
SERVICE_DESCRIBE_IMAGE: Final[str] = "describe_image"
DESCRIBE_IMAGE_ENDPOINT: Final[str] = "/v1/vision/describe"
CONF_DEFAULT_STYLE: Final[str] = "default_style"
STYLE_PRESETS: Final[tuple[str, ...]] = ("straightforward", "serious", "funny")
DEFAULT_STYLE: Final[str] = "straightforward"
MAX_STYLE_CHARS: Final[int] = 200
MAX_INSTRUCTIONS_CHARS: Final[int] = 300
# Longest side after downscaling, in pixels.
IMAGE_MAX_DIMENSION: Final[int] = 1280
IMAGE_JPEG_QUALITY: Final[int] = 85
# Largest source image accepted before downscaling.
IMAGE_MAX_SOURCE_BYTES: Final[int] = 25 * 1024 * 1024
# A description takes tens of seconds; the node enforces its own (shorter,
# configurable) limit, so this only bounds a node that stops answering.
DESCRIBE_IMAGE_TIMEOUT_S: Final[float] = 135.0
