"""Entrypoint for ``python -m openclaw_gateway``.

Loads :class:`GatewayConfig` from the environment, constructs an
:class:`AsyncAnthropic` client and :class:`DeviceRegistry`, then runs
:meth:`GatewayServer.serve_forever`.

Environment variables documented in :mod:`openclaw_gateway.config`.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from anthropic import AsyncAnthropic

from openclaw_gateway import __version__
from openclaw_gateway.config import load_config
from openclaw_gateway.device_registry import DeviceRegistry
from openclaw_gateway.server import GatewayServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

_LOG = logging.getLogger(__name__)


async def _main() -> None:  # pragma: no cover — runtime entrypoint
    """Load config, build the server, run forever."""
    config = load_config()
    _LOG.info("Starting openclaw-gateway %s", __version__)
    _LOG.info("Brain model: %s; auto_approve=%s", config.model, config.auto_approve)
    _LOG.info("Bind: ws://%s:%d", config.host, config.port)
    _LOG.info("Data dir: %s", config.data_dir)

    if not config.anthropic_api_key:
        _LOG.warning("ANTHROPIC_API_KEY not set — brain calls will fail at runtime")

    config.data_dir.mkdir(parents=True, exist_ok=True)
    registry = DeviceRegistry(persist_path=config.data_dir / "devices.json")

    client = AsyncAnthropic(api_key=config.anthropic_api_key or None)
    server = GatewayServer(
        client,
        host=config.host,
        port=config.port,
        model=config.model,
        system_prompt=config.system_prompt,
        device_registry=registry,
        auto_approve=config.auto_approve,
    )
    await server.serve_forever()


def main() -> None:  # pragma: no cover — bootstrap
    """Synchronous entrypoint.

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
