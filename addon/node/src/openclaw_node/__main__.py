"""Entrypoint for ``python -m openclaw_node``.

Detects add-on vs standalone mode, loads or generates the device identity,
and starts the gateway WebSocket client loop.

The local HTTP API (port 8099) is not yet implemented in P2 and will be
added in a later phase.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from openclaw_node.config import load_config
from openclaw_node.gateway_ws import GatewayClient
from openclaw_node.identity import load_or_generate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

_LOG = logging.getLogger(__name__)


async def _main() -> None:  # pragma: no cover
    """Load config, resolve identity, and run the gateway client loop."""
    config = load_config()
    mode = "add-on" if config.addon_mode else "standalone"
    _LOG.info(
        "Starting openclaw-hass-node %s in %s mode",
        __import__("openclaw_node").__version__,
        mode,
    )
    _LOG.info("Gateway URL: %s", config.gateway_url)
    _LOG.info("Data dir: %s", config.data_dir)

    identity, created = load_or_generate(config.key_path)
    if created:
        _LOG.info("Generated new device identity: %s", identity.device_id)
    else:
        _LOG.info("Loaded existing device identity: %s", identity.device_id)

    client = GatewayClient(
        config=config,
        identity=identity,
    )
    await client.run()


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
