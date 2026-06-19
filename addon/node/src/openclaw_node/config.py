"""Runtime configuration for the OpenClaw HASS Node.

Detects add-on vs standalone mode from environment variables and exposes a
single :class:`NodeConfig` dataclass consumed by all subsystems.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from openclaw_node.safe_path import allowed_roots

_LOG = logging.getLogger(__name__)

_DEFAULT_GATEWAY_URL = "wss://gateway.example.com/ws"


def normalize_pairing_token(raw: str) -> str:
    """Accept either a raw bootstrap token or an ``openclaw qr`` setup code.

    The mobile/QR pairing flow emits a base64url-encoded JSON envelope:

        {"url": "wss://gw/", "bootstrapToken": "<token>"}

    Users on a headless gateway can paste the output of ``openclaw qr
    --setup-code-only --no-ascii`` directly into the ``pairing_token``
    field — this helper detects that shape and extracts the inner
    ``bootstrapToken``. Anything that does not parse as a base64-encoded
    JSON object with the expected key is treated as a raw token and
    returned unchanged.
    """
    value = raw.strip()
    if not value:
        return value
    try:
        # base64url alphabet — pad to a multiple of 4 because the CLI
        # output drops trailing padding.
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError):
        return value
    try:
        envelope = json.loads(decoded)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return value
    if not isinstance(envelope, dict):
        return value
    token = envelope.get("bootstrapToken")
    if isinstance(token, str) and token:
        _LOG.info("pairing_token detected as setup-code envelope; extracted bootstrapToken")
        return token
    return value


_ADDON_DATA_DIR = Path("/data/openclaw")
_STANDALONE_DATA_DIR = Path.home() / ".openclaw" / "hass-node"
_EMPTY = "".join(())


def _is_addon_mode() -> bool:
    """Return True when running inside a Home Assistant add-on.

    Primary signal is the ``SUPERVISOR_TOKEN`` env var, which Supervisor
    injects when ``hassio_api: true`` is set in ``addon/config.yaml``.

    Fallback: HA add-on containers always get a writable ``/data`` mount
    (regardless of whether hassio_api is enabled), so an existing
    writable ``/data`` directory is a reliable secondary signal. This
    matters when Supervisor doesn't inject SUPERVISOR_TOKEN despite
    config flags asking for it (observed 2026-06-08 install) — without
    this fallback the addon writes to ``/root/.openclaw/hass-node``
    which doesn't persist across container restarts.
    """
    if os.environ.get("SUPERVISOR_TOKEN"):
        return True
    data = Path("/data")
    return data.is_dir() and os.access(data, os.W_OK)


@dataclass(frozen=True)
class NodeConfig:
    """Immutable runtime configuration for the node process.

    Attributes:
        addon_mode: True when running as a Home Assistant add-on.
        gateway_url: WebSocket URL of the OpenClaw gateway.
        pairing_token: One-time token used on first pairing; empty after pairing.
        node_name: Optional human-readable display name for this node.
        hass_url: Base URL of the Home Assistant REST/WS API.
        hass_token: Long-lived access token for HA (standalone mode only).
        supervisor_token: Supervisor API token (add-on mode only).
        data_dir: Directory where persistent node data (e.g. key) is stored.
    """

    addon_mode: bool
    gateway_url: str
    pairing_token: str
    node_name: str
    hass_url: str
    hass_token: str
    supervisor_token: str
    data_dir: Path
    local_api_token: str = ""
    reset_pairing: bool = False

    @property
    def key_path(self) -> Path:
        """Return the path to the persisted Ed25519 identity file.

        Returns:
            Absolute path to ``node-key.json`` under ``data_dir``.
        """
        return self.data_dir / "node-key.json"

    @property
    def device_token_path(self) -> Path:
        """Return the path to the persisted device-token file.

        The token is written on first successful connect (when the gateway
        issues one in the hello-ok payload) and reused for every subsequent
        connect, replacing the one-shot pairing_token from add-on options.
        """
        return self.data_dir / "device-token"


def load_config() -> NodeConfig:
    """Build a :class:`NodeConfig` from environment variables.

    Detects add-on vs standalone mode by checking for ``SUPERVISOR_TOKEN``.
    In add-on mode the HA URL is always ``http://homeassistant`` and the
    Supervisor token is read from ``SUPERVISOR_TOKEN``.  In standalone mode
    ``HASS_URL`` and ``HASS_TOKEN`` must be provided.

    Returns:
        A fully-populated :class:`NodeConfig` instance.

    Example:
        >>> import os
        >>> os.environ["SUPERVISOR_TOKEN"] = "tok"
        >>> cfg = load_config()
        >>> cfg.addon_mode
        True
        >>> del os.environ["SUPERVISOR_TOKEN"]
    """
    addon = _is_addon_mode()

    if addon:
        hass_url = os.environ.get("HASS_URL", "http://homeassistant")
        hass_token = _EMPTY
        supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
        data_dir = _ADDON_DATA_DIR
    else:
        hass_url = os.environ.get("HASS_URL", "")
        hass_token = os.environ.get("HASS_TOKEN", "")
        supervisor_token = _EMPTY
        data_dir = _STANDALONE_DATA_DIR

    return NodeConfig(
        addon_mode=addon,
        gateway_url=os.environ.get("GATEWAY_URL", _DEFAULT_GATEWAY_URL),
        pairing_token=normalize_pairing_token(os.environ.get("PAIRING_TOKEN", "")),
        node_name=os.environ.get("NODE_NAME", ""),
        hass_url=hass_url,
        hass_token=hass_token,
        supervisor_token=supervisor_token,
        data_dir=data_dir,
        local_api_token=os.environ.get("OPENCLAW_LOCAL_API_TOKEN", ""),
        reset_pairing=_parse_bool_env("OPENCLAW_RESET_PAIRING"),
    )


_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on", "y"})
_FALSY_ENV_VALUES = frozenset({"", "0", "false", "no", "off", "n"})


def _parse_bool_env(name: str) -> bool:
    """Parse a boolean env var with explicit truthy/falsy allowlists.

    Strict by design: anything outside the known truthy/falsy sets is
    treated as ``False`` and logs a warning so a typo cannot trigger a
    destructive operation. Comparison is case-insensitive.
    """
    raw = os.environ.get(name, "").strip().casefold()
    if raw in _TRUTHY_ENV_VALUES:
        return True
    if raw in _FALSY_ENV_VALUES:
        return False
    _LOG.warning(
        "%s=%r is not a recognized boolean; treating as false. "
        "Use one of: 1/0, true/false, yes/no, on/off.",
        name,
        raw,
    )
    return False


def allowed_roots_for_env() -> tuple[Path, ...]:
    """Return the filesystem roots permitted for read-only commands.

    Detects add-on vs standalone mode from the environment (same logic as
    :func:`load_config`) and delegates to
    :func:`openclaw_node.safe_path.allowed_roots`.

    Returns:
        Tuple of resolved :class:`pathlib.Path` roots. Empty in standalone
        mode when ``OPENCLAW_ALLOWED_ROOTS`` is unset.

    Example:
        >>> import os
        >>> os.environ.pop("OPENCLAW_ALLOWED_ROOTS", None)  # doctest: +SKIP
        >>> allowed_roots_for_env()  # doctest: +SKIP
        ()
    """
    return allowed_roots(addon_mode=_is_addon_mode())
