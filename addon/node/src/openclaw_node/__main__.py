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
from pathlib import Path

from openclaw_node.config import NodeConfig, load_config
from openclaw_node.gateway_ws import _OPERATOR_SCOPES, GatewayClient
from openclaw_node.http_api import NodeRuntime, run_http_api
from openclaw_node.identity import DeviceIdentity, load_or_generate
from openclaw_node.pairing import PairingState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

_LOG = logging.getLogger(__name__)


def _reset_pairing_state(config: NodeConfig) -> None:
    """Delete persisted pairing artifacts based on ``config.reset_pairing``.

    Modes:

    - ``"none"``: no-op (caller normally short-circuits).
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
    """
    mode = config.reset_pairing
    if mode == "none":
        return
    if mode == "token":
        targets: tuple[Path, ...] = (
            config.device_token_path,
            config.device_token_path_for("node"),
            config.device_token_path_for("operator"),
        )
    elif mode == "identity":
        targets = (
            config.device_token_path,
            config.device_token_path_for("node"),
            config.device_token_path_for("operator"),
            config.key_path,
        )
    else:
        _LOG.warning("reset_pairing: unknown mode %r; skipping wipe", mode)
        return
    try:
        data_dir = config.data_dir.resolve(strict=False)
    except OSError as exc:
        _LOG.warning(
            "reset_pairing: cannot resolve data_dir %s (%s); skipping wipe",
            config.data_dir,
            exc,
        )
        return
    for path in targets:
        if not _safe_to_unlink_under(path, data_dir):
            continue
        try:
            if path.exists():
                path.unlink()
                _LOG.warning("reset_pairing[%s]: removed %s", mode, path)
        except OSError as exc:
            _LOG.warning("reset_pairing[%s]: failed to remove %s: %s", mode, path, exc)
    if mode == "identity":
        _LOG.warning(
            "reset_pairing=identity wiped the device identity. The previously "
            "issued bootstrap is no longer valid for this device — generate "
            "a NEW setup-code on the gateway and paste it as `pairing_token` "
            "before the next restart."
        )
    _LOG.warning(
        "reset_pairing was %r on startup. Set it back to false in add-on "
        "options after the node re-pairs successfully — otherwise every "
        "restart will wipe pairing again.",
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


def build_runtime(
    config: NodeConfig, identity: DeviceIdentity
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

    Returns:
        A ``(runtime, node_client, operator_client)`` tuple ready to be
        driven by :func:`_main`.
    """
    runtime = NodeRuntime(config)

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
        caps=[],
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
    if config.reset_pairing != "none":
        _reset_pairing_state(config)
    _migrate_legacy_device_token(config)
    if config.addon_mode and not config.local_api_token:
        _LOG.warning(
            "local_api_token is unset — the local HTTP API on port 8099 is "
            "OPEN to anything that can reach it inside the Supervisor add-on "
            "network. Set the `local_api_token` option to require a bearer "
            "token for every endpoint except /health and /v1/conversation/info."
        )

    identity, created = load_or_generate(config.key_path)
    if created:
        _LOG.info("Generated new device identity: %s", identity.device_id)
    else:
        _LOG.info("Loaded existing device identity: %s", identity.device_id)

    runtime, node_client, operator_client = build_runtime(config, identity)

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
