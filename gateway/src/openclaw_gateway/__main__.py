"""Entrypoint for ``python -m openclaw_gateway``.

Loads :class:`GatewayConfig` from the environment, constructs the configured
provider (Anthropic or OpenAI), builds a persistent DeviceRegistry, then
runs :meth:`GatewayServer.serve_forever`.

Environment variables documented in :mod:`openclaw_gateway.config`.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from openclaw_gateway import __version__
from openclaw_gateway.config import GatewayConfig, load_config
from openclaw_gateway.device_registry import DeviceRegistry
from openclaw_gateway.providers import Provider
from openclaw_gateway.server import GatewayServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

_LOG = logging.getLogger(__name__)


def _build_provider(config: GatewayConfig) -> Provider:
    """Construct the configured provider, importing SDKs lazily."""
    if config.provider == "openai":
        from openai import AsyncOpenAI

        from openclaw_gateway.providers_openai import OpenAIProvider

        oai_client = AsyncOpenAI(api_key=config.openai_api_key or None)
        return OpenAIProvider(oai_client, model=config.model)

    if config.provider == "anthropic":
        from anthropic import AsyncAnthropic

        from openclaw_gateway.providers_anthropic import AnthropicProvider

        ant_client = AsyncAnthropic(api_key=config.anthropic_api_key or None)
        return AnthropicProvider(ant_client, model=config.model)

    msg = f"unknown provider: {config.provider!r}"
    raise ValueError(msg)


async def _main() -> None:  # pragma: no cover — runtime entrypoint
    """Load config, build the server, run forever."""
    config = load_config()
    _LOG.info("Starting openclaw-gateway %s", __version__)
    _LOG.info(
        "Provider=%s model=%s auto_approve=%s",
        config.provider,
        config.model,
        config.auto_approve,
    )
    _LOG.info("Bind: ws://%s:%d", config.host, config.port)
    _LOG.info("Data dir: %s", config.data_dir)

    if config.provider == "anthropic" and not config.anthropic_api_key:
        _LOG.warning("ANTHROPIC_API_KEY not set — Claude calls will fail at runtime")
    if config.provider == "openai" and not config.openai_api_key:
        _LOG.warning("OPENAI_API_KEY not set — OpenAI calls will fail at runtime")

    config.data_dir.mkdir(parents=True, exist_ok=True)
    registry = DeviceRegistry(persist_path=config.data_dir / "devices.json")

    provider = _build_provider(config)
    server = GatewayServer(
        provider,
        host=config.host,
        port=config.port,
        system_prompt=config.system_prompt,
        device_registry=registry,
        auto_approve=config.auto_approve,
    )
    await server.serve_forever()


def main() -> None:  # pragma: no cover
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
