"""Runtime configuration for the OpenClaw HASS Node.

Detects add-on vs standalone mode from environment variables and exposes a
single :class:`NodeConfig` dataclass consumed by all subsystems.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from openclaw_node.safe_path import allowed_roots

_DEFAULT_GATEWAY_URL = "wss://gateway.example.com/ws"
_ADDON_DATA_DIR = Path("/data/openclaw")
_STANDALONE_DATA_DIR = Path.home() / ".openclaw" / "hass-node"
_EMPTY = "".join(())


def _is_addon_mode() -> bool:
    """Return True when running inside a Home Assistant add-on.

    Returns:
        True if the ``SUPERVISOR_TOKEN`` environment variable is set,
        indicating the process is running as an HA add-on.
    """
    return bool(os.environ.get("SUPERVISOR_TOKEN"))


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
        pairing_token=os.environ.get("PAIRING_TOKEN", ""),
        node_name=os.environ.get("NODE_NAME", ""),
        hass_url=hass_url,
        hass_token=hass_token,
        supervisor_token=supervisor_token,
        data_dir=data_dir,
    )


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
