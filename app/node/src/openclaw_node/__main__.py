"""Entrypoint for ``python -m openclaw_node``.

Detects add-on vs standalone mode, loads or generates the device identity,
constructs the shared :class:`NodeRuntime`, and runs the gateway WebSocket
client and the local HTTP API concurrently under one task group.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from openclaw_node.config import NodeConfig, load_config
from openclaw_node.gateway_ws import _OPERATOR_SCOPES, GatewayClient
from openclaw_node.ha_client import HAClientError, ha_ws_call
from openclaw_node.http_api import (
    NodeRuntime,
    _bootstrap_claimed_path,
    _bootstrap_consumed_path,
    run_http_api,
)
from openclaw_node.identity import DeviceIdentity, load_or_generate
from openclaw_node.pairing import PairingState
from openclaw_node.token_store import load_or_generate_local_api_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

_LOG = logging.getLogger(__name__)


_RESET_CONSUMED_FILENAME = "reset-consumed"


def _reset_pairing_state(config: NodeConfig) -> None:
    """Delete persisted pairing artifacts based on ``config.reset_pairing``.

    Modes:

    - ``"none"``: no-op. Clears any existing one-shot consumed marker so that
      a subsequent toggle back to an active mode triggers a fresh wipe.
    - ``"token"``: wipe ``device-token`` only. Identity (``node-key.json``)
      stays so the SAME device record can re-pair with a freshly issued
      bootstrap. This is the safe default — a brand-new identity invalidates
      any bootstrap minted for the prior identity (gateway rejects with
      ``AUTH_TOKEN_MISMATCH``).
    - ``"identity"``: wipe both ``device-token`` and ``node-key.json``. The
      node becomes a brand-new device. The user MUST generate a fresh
      setup-code AFTER this wipe; any previously-issued bootstrap is tied
      to the now-deleted identity and will be rejected.

    Each target is constrained to ``data_dir``: a symlink at the target, or
    a resolved location outside ``data_dir``, is refused and logged rather
    than followed. Per-file failures are logged and swallowed.

    One-shot behaviour: after the wipe runs, a marker file
    ``<data_dir>/reset-consumed`` is written containing the mode string.
    On the next start with the same mode value the wipe is skipped and a
    warning is logged instead. The marker is cleared when ``reset_pairing``
    is set back to ``"none"``, so toggling the option off then back on
    requests a fresh wipe.
    """
    mode = config.reset_pairing
    consumed_path = config.data_dir / _RESET_CONSUMED_FILENAME

    if mode == "none":
        # Clear the consumed marker so a subsequent toggle-on fires a fresh wipe.
        try:
            if consumed_path.exists() and not consumed_path.is_symlink():
                consumed_path.unlink()
        except OSError as exc:
            _LOG.warning("reset_pairing: failed to clear consumed marker: %s", exc)
        return

    if mode not in ("token", "identity"):
        _LOG.warning("reset_pairing: unknown mode %r; skipping wipe", mode)
        return

    # One-shot guard: skip if this exact mode was already consumed on a prior start.
    try:
        if consumed_path.exists() and consumed_path.read_text().strip() == mode:
            _LOG.warning(
                "reset_pairing=%r already consumed for this value; set it back to"
                " 'none' then to the desired mode again to request another wipe.",
                mode,
            )
            return
    except OSError as exc:
        _LOG.warning(
            "reset_pairing: cannot read consumed marker (%s); proceeding with wipe",
            exc,
        )

    if mode == "token":
        targets: tuple[Path, ...] = (
            config.device_token_path,
            config.device_token_path_for("node"),
            config.device_token_path_for("operator"),
        )
    else:
        targets = (
            config.device_token_path,
            config.device_token_path_for("node"),
            config.device_token_path_for("operator"),
            config.key_path,
        )
    try:
        data_dir = config.data_dir.resolve(strict=False)
    except OSError as exc:
        _LOG.warning(
            "reset_pairing: cannot resolve data_dir %s (%s); skipping wipe",
            config.data_dir,
            exc,
        )
        return
    wipe_ok = True
    for path in targets:
        if not _safe_to_unlink_under(path, data_dir):
            continue
        try:
            if path.exists():
                path.unlink()
                _LOG.warning("reset_pairing[%s]: removed %s", mode, path)
        except OSError as exc:
            _LOG.warning("reset_pairing[%s]: failed to remove %s: %s", mode, path, exc)
            wipe_ok = False

    if not wipe_ok:
        _LOG.warning(
            "reset_pairing[%s]: one or more files could not be removed; consumed"
            " marker NOT written — wipe will retry on next start.",
            mode,
        )
        return

    # Write consumed marker so subsequent starts skip the wipe.
    try:
        consumed_path.write_text(f"{mode}\n")
    except OSError as exc:
        _LOG.warning("reset_pairing: failed to write consumed marker: %s", exc)

    if mode == "identity":
        _LOG.warning(
            "reset_pairing=identity wiped the device identity. The previously "
            "issued bootstrap is no longer valid for this device — generate "
            "a NEW setup-code on the gateway and paste it as `pairing_token` "
            "before the next restart."
        )
    _LOG.warning(
        "reset_pairing=%r consumed. The add-on can be left at this setting; the"
        " wipe will not repeat unless you toggle the option to 'none' and back.",
        mode,
    )


def _safe_to_unlink_under(path: Path, data_dir: Path) -> bool:
    """Return True iff *path* resolves inside *data_dir* and is not a symlink.

    Symlinks at *path* are refused so a tampered ``/data/openclaw`` cannot
    be used to coax the reset into unlinking an arbitrary host file.
    """
    try:
        resolved = path.resolve(strict=False)
    except OSError as exc:
        _LOG.warning("reset_pairing: cannot resolve %s (%s); skipping", path, exc)
        return False
    try:
        resolved.relative_to(data_dir)
    except ValueError:
        _LOG.warning(
            "reset_pairing: %s resolves to %s outside data_dir %s; skipping",
            path,
            resolved,
            data_dir,
        )
        return False
    if path.is_symlink():
        _LOG.warning("reset_pairing: %s is a symlink; skipping", path)
        return False
    return True


def _reset_bootstrap_state(config: NodeConfig) -> None:
    """Delete bootstrap markers when ``config.reset_bootstrap`` is True.

    Called on startup before the HTTP API starts.  Deleting the marker lets
    the HACS integration call ``GET /v1/bootstrap`` once more during the
    next ``BOOTSTRAP_WINDOW_SECONDS`` window.

    After the integration re-fetches, the operator should set
    ``reset_bootstrap = false`` in the add-on options — otherwise the marker
    is cleared on every subsequent restart.
    """
    if not config.reset_bootstrap:
        return
    cleared = False
    for marker_path in (
        _bootstrap_consumed_path(config),
        _bootstrap_claimed_path(config),
    ):
        if not marker_path.exists() and not marker_path.is_symlink():
            continue
        if marker_path.is_symlink():
            _LOG.warning("reset_bootstrap: %s is a symlink; refusing to remove", marker_path)
            continue
        try:
            marker_path.unlink()
            cleared = True
            _LOG.info("reset_bootstrap: cleared marker at %s", marker_path)
        except OSError as exc:
            _LOG.warning("reset_bootstrap: could not remove %s: %s", marker_path, exc)
    if cleared:
        _LOG.warning(
            "reset_bootstrap: the HACS integration can fetch the token once more "
            "within the next 300-second window. Set reset_bootstrap=false in "
            "add-on options after the integration re-fetches."
        )
    else:
        _LOG.info("reset_bootstrap: no bootstrap markers found; nothing to clear")


def _migrate_legacy_device_token(config: NodeConfig) -> None:
    """Move a pre-#98-part-3 single device-token file to the node-role path.

    Pre-fix builds persisted both roles' tokens to ``data_dir/device-token``
    with last-writer-wins, which the gateway then rejected with
    ``AUTH_TOKEN_MISMATCH`` on the next restart. New code uses per-role
    paths. On first startup after the upgrade we move the legacy file to
    the node-role path (best guess — the node token is the more critical
    one and is what most invokes need); the operator side will fetch a
    fresh token from the gateway on its next connect.

    Failures are logged and swallowed; a missing legacy file is the
    expected happy path.
    """
    legacy = config.device_token_path
    target = config.device_token_path_for("node")
    if not legacy.exists() or target.exists():
        return
    # Refuse to migrate a symlink — otherwise we'd carry the symlink (and
    # its arbitrary target) into the new per-role path, defeating the
    # symlink-refuse hardening elsewhere in the token write path.
    if legacy.is_symlink():
        _LOG.warning(
            "Refusing to migrate %s: path is a symlink. Remove it manually "
            "to force a fresh re-pair.",
            legacy,
        )
        return
    try:
        token = legacy.read_text().strip()
    except OSError as exc:
        _LOG.warning("Could not read legacy %s for migration: %s", legacy, exc)
        return
    if not token:
        # Nothing of value; unlink the empty file so it doesn't keep
        # tripping the migration check.
        with contextlib.suppress(OSError):
            legacy.unlink()
        return
    # Re-write through the hardened atomic writer so the new file lands at
    # 0o600 with O_NOFOLLOW, then unlink the legacy file.
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        from openclaw_node.gateway_ws import GatewayClient

        GatewayClient._atomic_write_token(target, token)
        legacy.unlink()
        _LOG.info("Migrated legacy device-token to %s (#98 part 3)", target)
    except OSError as exc:
        _LOG.warning("Could not migrate legacy %s -> %s: %s", legacy, target, exc)


def _initial_device_token(config: NodeConfig, role: str = "node") -> str | None:
    """Return the persisted device token for *role*, with pairing fallback.

    The pairing token is consumed when the gateway approves the first connect.
    Subsequent connects with the same value fail ``NOT_PAIRED``, so always
    prefer the persisted per-role token when it exists.

    Args:
        config: Loaded runtime configuration.
        role: Which role's token to load. The gateway issues per-role tokens
            on a dual-role pairing profile; mixing them produces
            ``AUTH_TOKEN_MISMATCH`` (#98 part 3).

    Returns:
        The token to send on the next connect, or ``None`` when neither a
        persisted device token nor a one-shot pairing token is available.
    """
    persisted = ""
    path = config.device_token_path_for(role)
    if path.exists():
        persisted = path.read_text().strip()
        if persisted:
            _LOG.info("Loaded persisted %s device_token from %s", role, path)
    return persisted or config.pairing_token or None


async def _resolve_identity_usernames(config: NodeConfig) -> NodeConfig:
    """Resolve configured HA usernames to user IDs for per-turn authz.

    The add-on Configuration tab should accept values a human can know, so
    identity options are entered as HA usernames. Assist actor payloads are
    signed by HA user ID, so startup resolves configured names once and stores
    the effective ID-based policy in memory.
    """
    super_admins = tuple(sorted(config.identity.super_admins))
    user_agent_map = dict(sorted(config.identity.user_agent_map.items()))
    if not super_admins and not user_agent_map:
        return config
    try:
        result = await ha_ws_call("config/auth/list")
    except (HAClientError, TimeoutError) as exc:
        detail = exc.message if isinstance(exc, HAClientError) else str(exc)
        _LOG.warning(
            "identity usernames could not be resolved from HA users: %s. "
            "No configured super_admins or user_agent_map entries are active for this startup.",
            detail,
        )
        return replace(
            config,
            identity=replace(
                config.identity,
                super_admins=frozenset(),
                user_agent_map={},
            ),
        )
    users = _ha_user_id_by_name(result)
    resolved_super_admins: set[str] = set()
    unresolved_super_admins: list[str] = []
    for name in super_admins:
        user_id = users.get(name.casefold())
        if user_id:
            resolved_super_admins.add(user_id)
        else:
            unresolved_super_admins.append(name)
    if unresolved_super_admins:
        _LOG.warning(
            "identity.super_admins entries not found in HA users and ignored: %s",
            ", ".join(unresolved_super_admins),
        )
    resolved_user_agent_map: dict[str, str] = {}
    unresolved_routes: list[str] = []
    for name, agent_id in user_agent_map.items():
        user_id = users.get(name.casefold())
        if user_id:
            resolved_user_agent_map[user_id] = agent_id
        else:
            unresolved_routes.append(name)
    if unresolved_routes:
        _LOG.warning(
            "identity.user_agent_map entries not found in HA users and ignored: %s",
            ", ".join(unresolved_routes),
        )
    _LOG.info(
        "identity.super_admins resolved %d/%d configured HA usernames",
        len(resolved_super_admins),
        len(super_admins),
    )
    _LOG.info(
        "identity.user_agent_map resolved %d/%d configured HA usernames",
        len(resolved_user_agent_map),
        len(user_agent_map),
    )
    return replace(
        config,
        identity=replace(
            config.identity,
            super_admins=frozenset(resolved_super_admins),
            user_agent_map=resolved_user_agent_map,
        ),
    )


def _ha_user_id_by_name(result: Any) -> dict[str, str]:
    """Extract a case-insensitive HA login-username map from config/auth/list."""
    raw_users = result.get("users", result) if isinstance(result, dict) else result
    if not isinstance(raw_users, list):
        return {}
    users: dict[str, str] = {}
    for raw in raw_users:
        if not isinstance(raw, dict):
            continue
        user_id = raw.get("id")
        if not isinstance(user_id, str) or not user_id.strip():
            continue
        for name in _ha_user_name_candidates(raw):
            users.setdefault(name.casefold(), user_id.strip())
    return users


def _ha_user_name_candidates(raw: dict[str, Any]) -> tuple[str, ...]:
    """Return stable login names from one HA config/auth/list user object."""
    names: list[str] = []
    username = raw.get("username")
    if isinstance(username, str) and username.strip():
        names.append(username.strip())
    credentials = raw.get("credentials")
    if isinstance(credentials, list):
        for credential in credentials:
            if not isinstance(credential, dict):
                continue
            data = credential.get("data")
            if not isinstance(data, dict):
                continue
            username = data.get("username")
            if isinstance(username, str) and username.strip():
                names.append(username.strip())
    return tuple(dict.fromkeys(names))


def build_runtime(
    config: NodeConfig,
    identity: DeviceIdentity,
    bootstrap_token: str = "",
) -> tuple[NodeRuntime, GatewayClient, GatewayClient]:
    """Construct the runtime + both gateway clients with pairing wired through.

    P5.13 dual-WS: the node runs two parallel gateway connections sharing
    the same device identity + device token (the dual-role pairing profile
    grants both roles on a single device record). The node-role connection
    handles ``node.invoke.*`` and ``node.event``; the operator-role
    connection handles ``chat.send`` / ``sessions.messages.subscribe`` via
    :class:`ChatRelay`. Each connection runs an independent reconnect loop;
    one failing does not take the other down.

    Args:
        config: Loaded runtime configuration.
        identity: Device identity loaded or freshly generated.
        bootstrap_token: Auto-generated local API token to expose via
            ``GET /v1/bootstrap``. Empty string when the operator supplied
            the token explicitly (bootstrap endpoint returns disabled).

    Returns:
        A ``(runtime, node_client, operator_client)`` tuple ready to be
        driven by :func:`_main`.
    """
    runtime = NodeRuntime(config, bootstrap_token=bootstrap_token)

    def _on_pairing_state(state: PairingState) -> None:
        runtime.pairing_state = state

    node_token = _initial_device_token(config, role="node")
    operator_token = _initial_device_token(config, role="operator")

    node_client = GatewayClient(
        config=config,
        identity=identity,
        device_token=node_token,
        pairing_state_callback=_on_pairing_state,
        runtime=runtime,
        chat_relay_enabled=False,  # operator owns the relay in P5.13
        invoke_dispatch_enabled=True,
        token_persist_path=config.device_token_path_for("node"),
    )
    operator_client = GatewayClient(
        config=config,
        identity=identity,
        device_token=operator_token,
        # No pairing_state_callback — the operator connection inherits
        # whatever the node connection already established; surfacing
        # operator-side pairing state would just churn the same flag.
        pairing_state_callback=None,
        runtime=runtime,
        role="operator",
        scopes=_OPERATOR_SCOPES,
        # `tool-events` opts this connection in to the gateway's per-tool
        # `agent` / `session.tool` broadcasts (see
        # `/app/dist/server-chat-DVXWYmKw.js:889,903` and the capability
        # check at `/app/dist/agent-DnsoYp5b.js:1684`). The relay uses
        # this to surface tool names in HA Assist's slow-turn progress
        # delta instead of the generic "Working on it..." placeholder.
        caps=["tool-events"],
        commands=[],
        chat_relay_enabled=True,
        invoke_dispatch_enabled=False,
        # Per-role token persistence (#98 part 3): the gateway issues
        # distinct deviceToken values for node and operator on a
        # dual-role pairing; sharing one file last-writer-wins'd them
        # and produced AUTH_TOKEN_MISMATCH on next restart. Operator
        # still skips the pair-fallback wipe — that path is node-driven.
        token_persist_path=config.device_token_path_for("operator"),
        pair_fallback_enabled=False,
    )
    return runtime, node_client, operator_client


async def _main() -> None:
    """Load config, resolve identity, and run the gateway + HTTP API concurrently."""
    config = load_config()
    mode = "add-on" if config.addon_mode else "standalone"
    _LOG.info(
        "Starting openclaw-hass-node %s in %s mode",
        __import__("openclaw_node").__version__,
        mode,
    )
    _LOG.info("Gateway URL: %s", config.gateway_url)
    _LOG.info("Data dir: %s", config.data_dir)
    _reset_pairing_state(config)
    _reset_bootstrap_state(config)
    _migrate_legacy_device_token(config)
    bootstrap_token = ""
    if not config.local_api_token:
        # Auto-generate a local API token so the HACS integration can fetch it
        # via GET /v1/bootstrap without the operator manually copying it.
        # Standalone mode also benefits: the token is persisted across restarts.
        try:
            auto_token, created = load_or_generate_local_api_token(config.data_dir)
            config = replace(config, local_api_token=auto_token)
            bootstrap_token = auto_token
            if created:
                _LOG.info(
                    "Auto-generated local_api_token. The HACS integration can "
                    "retrieve it via GET /v1/bootstrap during config-flow setup "
                    "(no manual copy-paste required)."
                )
        except OSError as exc:
            _LOG.warning(
                "Could not auto-generate local_api_token (%s). "
                "The local HTTP API on port 8099 will be OPEN to anything that "
                "can reach it. Set the `local_api_token` add-on option to secure it.",
                exc,
            )
    config = await _resolve_identity_usernames(config)

    identity, created = load_or_generate(config.key_path)
    if created:
        _LOG.info("Generated new device identity: %s", identity.device_id)
    else:
        _LOG.info("Loaded existing device identity: %s", identity.device_id)

    runtime, node_client, operator_client = build_runtime(
        config, identity, bootstrap_token=bootstrap_token
    )

    async with asyncio.TaskGroup() as tg:
        tg.create_task(node_client.run(), name="gateway-ws-node")
        tg.create_task(operator_client.run(), name="gateway-ws-operator")
        tg.create_task(run_http_api(runtime), name="http-api")


def main() -> None:  # pragma: no cover
    """Synchronous entrypoint used by the add-on run script.

    Raises:
        SystemExit: With code 0 on keyboard interrupt.
    """
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        _LOG.info("Received interrupt — shutting down.")
        sys.exit(0)


if __name__ == "__main__":  # pragma: no cover
    main()
