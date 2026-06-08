"""Entrypoint for ``python -m openclaw_node``.

Detects add-on vs standalone mode, loads or generates the device identity,
constructs the shared :class:`NodeRuntime`, and runs the gateway WebSocket
client and the local HTTP API concurrently under one task group.
"""

from __future__ import annotations

import asyncio
import logging
import sys

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


def _initial_device_token(config: NodeConfig) -> str | None:
    """Return the persisted device token, falling back to a one-shot pairing token.

    The pairing token is consumed when the gateway approves the first connect.
    Subsequent connects with the same value fail ``NOT_PAIRED``, so always
    prefer the persisted token when it exists.

    Args:
        config: Loaded runtime configuration.

    Returns:
        The token to send on the next connect, or ``None`` when neither a
        persisted device token nor a one-shot pairing token is available.
    """
    persisted = ""
    if config.device_token_path.exists():
        persisted = config.device_token_path.read_text().strip()
        if persisted:
            _LOG.info("Loaded persisted device_token from %s", config.device_token_path)
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

    initial_token = _initial_device_token(config)

    node_client = GatewayClient(
        config=config,
        identity=identity,
        device_token=initial_token,
        pairing_state_callback=_on_pairing_state,
        runtime=runtime,
        chat_relay_enabled=False,  # operator owns the relay in P5.13
        invoke_dispatch_enabled=True,
    )
    operator_client = GatewayClient(
        config=config,
        identity=identity,
        device_token=initial_token,
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
        # Both connections share the device_token file (idempotent
        # writes), but the operator side must NOT clear it on an
        # INVALID_REQUEST — that would break the node connection too.
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
