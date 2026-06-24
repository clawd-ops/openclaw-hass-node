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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
DEFAULT_ADDON_LIFECYCLE_DENYLIST = frozenset({"homeassistant", "supervisor"})


@dataclass(frozen=True)
class ForbiddenCommandPatch:
    """Per-role add/remove patch for generated forbidden commands."""

    add: frozenset[str] = field(default_factory=frozenset)
    remove: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class IdentityConfig:
    """Addon-side identity and agent-routing options."""

    super_admins: frozenset[str] = field(default_factory=frozenset)
    user_agent_map: dict[str, str] = field(default_factory=dict)
    default_agent_id: str = ""
    actor_secret: str = ""
    forbidden_commands: dict[str, ForbiddenCommandPatch] = field(default_factory=dict)
    addon_lifecycle_allowlist: frozenset[str] = field(default_factory=frozenset)
    addon_lifecycle_denylist: frozenset[str] = field(
        default_factory=lambda: DEFAULT_ADDON_LIFECYCLE_DENYLIST
    )


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
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    # One of "none" | "token" | "identity":
    #   "none"     -do nothing on startup (default).
    #   "token"    -wipe device-token only; keep the Ed25519 identity so
    #                a freshly-issued bootstrap from the SAME device record
    #                can re-pair this device.
    #   "identity" -wipe device-token AND node-key.json. The node becomes
    #                a brand-new device on the gateway, so the user MUST
    #                generate a fresh setup-code AFTER this wipe (any
    #                bootstrap minted for the old identity will be rejected
    #                with AUTH_TOKEN_MISMATCH).
    reset_pairing: str = "none"

    @property
    def key_path(self) -> Path:
        """Return the path to the persisted Ed25519 identity file.

        Returns:
            Absolute path to ``node-key.json`` under ``data_dir``.
        """
        return self.data_dir / "node-key.json"

    @property
    def device_token_path(self) -> Path:
        """Return the legacy (pre-dual-role) device-token path.

        Retained for migration only — older add-on installs persisted a
        single token to this path, before the gateway started issuing
        per-role tokens. New code should use ``device_token_path_for(role)``;
        ``__main__`` migrates any token at this path to the node-role path
        on startup, then deletes it.
        """
        return self.data_dir / "device-token"

    def device_token_path_for(self, role: str) -> Path:
        """Return the per-role device-token path.

        The gateway issues distinct ``deviceToken`` values for the ``node``
        and ``operator`` roles on a dual-role pairing profile. Older builds
        persisted both to ``data_dir/device-token``; whichever role's
        ``_persist_device_token`` finished second clobbered the first, and
        the loser looped ``AUTH_TOKEN_MISMATCH`` on the next restart (#98
        part 3). Separate files keep each role's token intact.

        Args:
            role: Either ``"node"`` or ``"operator"``.
        """
        return self.data_dir / f"device-token.{role}"


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
        identity=_parse_identity_config(),
        reset_pairing=_parse_reset_pairing_env("OPENCLAW_RESET_PAIRING"),
    )


_RESET_PAIRING_MODES = frozenset({"none", "token", "identity"})
_RESET_PAIRING_TOKEN_ALIASES = frozenset({"1", "true", "yes", "on", "y", "token"})
_RESET_PAIRING_IDENTITY_ALIASES = frozenset({"identity", "full"})
_RESET_PAIRING_NONE_ALIASES = frozenset({"", "0", "false", "no", "off", "n", "none"})


def _parse_reset_pairing_env(name: str) -> str:
    """Parse the ``reset_pairing`` env var into a canonical mode string.

    Accepts:
      - Truthy booleans (``1``, ``true``, ``yes``, ``on``, ``y``) -> ``"token"``.
        This is the safe default because wiping only the device token lets
        the user re-pair the SAME device record with a fresh bootstrap.
      - ``"token"`` -> ``"token"``.
      - ``"identity"`` / ``"full"`` -> ``"identity"``. Also wipes node-key.json.
        The user MUST generate a brand-new setup-code AFTER this wipe.
      - Falsy / empty -> ``"none"``.

    Anything else is treated as ``"none"`` and logs a warning so a typo
    cannot trigger a destructive operation. Comparison is case-insensitive.
    """
    raw = os.environ.get(name, "").strip().casefold()
    if raw in _RESET_PAIRING_TOKEN_ALIASES:
        return "token"
    if raw in _RESET_PAIRING_IDENTITY_ALIASES:
        return "identity"
    if raw in _RESET_PAIRING_NONE_ALIASES:
        return "none"
    _LOG.warning(
        "%s=%r is not a recognized reset_pairing mode; treating as 'none'. "
        "Use one of: token (or true/1/yes), identity, false/0.",
        name,
        raw,
    )
    return "none"


def _parse_identity_config() -> IdentityConfig:
    """Parse identity options from environment variables emitted by run.sh."""
    return IdentityConfig(
        super_admins=frozenset(_parse_string_list_env("OPENCLAW_IDENTITY_SUPER_ADMINS")),
        user_agent_map=_parse_string_map_env("OPENCLAW_IDENTITY_USER_AGENT_MAP"),
        default_agent_id=os.environ.get("OPENCLAW_IDENTITY_DEFAULT_AGENT_ID", "").strip(),
        actor_secret=os.environ.get("OPENCLAW_IDENTITY_ACTOR_SECRET", "").strip(),
        forbidden_commands=_parse_forbidden_patches_env("OPENCLAW_IDENTITY_FORBIDDEN_COMMANDS"),
        addon_lifecycle_allowlist=frozenset(
            _parse_string_list_env("OPENCLAW_ADDON_LIFECYCLE_ALLOWLIST")
        ),
        addon_lifecycle_denylist=frozenset(
            _parse_string_list_env(
                "OPENCLAW_ADDON_LIFECYCLE_DENYLIST",
                default=tuple(sorted(DEFAULT_ADDON_LIFECYCLE_DENYLIST)),
            )
        ),
    )


def _parse_json_env(name: str) -> Any | None:
    """Return JSON-decoded env var value, logging and ignoring bad JSON."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        _LOG.warning("%s is not valid JSON; ignoring identity option", name)
        return None


def _parse_string_list_env(name: str, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Parse a JSON list or comma-separated env var into cleaned strings."""
    parsed = _parse_json_env(name)
    values: list[str] = []
    if isinstance(parsed, list):
        values = [str(item).strip() for item in parsed]
    elif parsed is None:
        raw = os.environ.get(name, "").strip()
        if raw:
            values = [part.strip() for part in raw.split(",")]
    else:
        _LOG.warning("%s must be a JSON list of strings; ignoring", name)
    cleaned = tuple(value for value in values if value)
    return cleaned or default


def _parse_string_map_env(name: str) -> dict[str, str]:
    """Parse a JSON object env var into a string-to-string map."""
    parsed = _parse_json_env(name)
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        _LOG.warning("%s must be a JSON object; ignoring", name)
        return {}
    result: dict[str, str] = {}
    for key, value in parsed.items():
        if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip():
            result[key.strip()] = value.strip()
    return result


def _parse_forbidden_patches_env(name: str) -> dict[str, ForbiddenCommandPatch]:
    """Parse optional per-role forbidden command add/remove patches."""
    parsed = _parse_json_env(name)
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        _LOG.warning("%s must be a JSON object; ignoring", name)
        return {}
    patches: dict[str, ForbiddenCommandPatch] = {}
    for role, raw_patch in parsed.items():
        if role not in {"user", "admin", "super_admin"} or not isinstance(raw_patch, dict):
            continue
        add = raw_patch.get("add", [])
        remove = raw_patch.get("remove", [])
        if not isinstance(add, list) or not isinstance(remove, list):
            continue
        patches[role] = ForbiddenCommandPatch(
            add=frozenset(str(item).strip() for item in add if str(item).strip()),
            remove=frozenset(str(item).strip() for item in remove if str(item).strip()),
        )
    return patches


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
