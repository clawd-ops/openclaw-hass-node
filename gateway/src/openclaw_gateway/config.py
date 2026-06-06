"""Runtime configuration for the OpenClaw gateway process.

Mirrors the style of :mod:`openclaw_node.config`: env-driven, immutable.
Provider choice is config-driven — Anthropic (claude-opus-4-7) or
OpenAI (gpt-5.5) is selected by ``OPENCLAW_GATEWAY_PROVIDER``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_HOST = "0.0.0.0"  # nosec B104 - intentional bind-all for add-on use
_DEFAULT_PORT = 8765
_DEFAULT_PROVIDER = "anthropic"
_DEFAULT_DATA_DIR = Path.home() / ".openclaw" / "hass-gateway"

# Per-provider sensible defaults if OPENCLAW_GATEWAY_MODEL is not set.
_DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-7",
    "openai": "gpt-5.5",
}


@dataclass(frozen=True)
class GatewayConfig:
    """Immutable runtime configuration for the gateway process.

    Attributes:
        host: WS bind host.
        port: WS bind port.
        provider: ``"anthropic"`` or ``"openai"``.
        anthropic_api_key: Key for Claude (used when provider=anthropic).
        openai_api_key: Key for OpenAI (used when provider=openai).
        model: Brain model id. Defaults per provider if not set.
        system_prompt: Optional system prompt prepended to every Assist turn.
        auto_approve: If True, unknown devices pair on first connect.
        data_dir: Directory holding the device registry JSON.
    """

    host: str
    port: int
    provider: str
    anthropic_api_key: str
    openai_api_key: str
    model: str
    system_prompt: str
    auto_approve: bool
    data_dir: Path


def load_config() -> GatewayConfig:
    """Load configuration from environment variables.

    Returns:
        Frozen :class:`GatewayConfig`.

    Env vars:
        OPENCLAW_GATEWAY_HOST: bind host (default 0.0.0.0).
        OPENCLAW_GATEWAY_PORT: bind port (default 8765).
        OPENCLAW_GATEWAY_PROVIDER: "anthropic" (default) or "openai".
        ANTHROPIC_API_KEY: required when provider=anthropic.
        OPENAI_API_KEY: required when provider=openai.
        OPENCLAW_GATEWAY_MODEL: brain model id; defaults per provider.
        OPENCLAW_GATEWAY_SYSTEM_PROMPT: optional system prompt.
        OPENCLAW_GATEWAY_AUTO_APPROVE: "1"/"true" → auto-pair (trial mode).
        OPENCLAW_GATEWAY_DATA_DIR: directory for state.
    """
    provider = os.environ.get("OPENCLAW_GATEWAY_PROVIDER", _DEFAULT_PROVIDER).lower()
    model = os.environ.get("OPENCLAW_GATEWAY_MODEL") or _DEFAULT_MODELS.get(
        provider, _DEFAULT_MODELS[_DEFAULT_PROVIDER]
    )
    return GatewayConfig(
        host=os.environ.get("OPENCLAW_GATEWAY_HOST", _DEFAULT_HOST),
        port=int(os.environ.get("OPENCLAW_GATEWAY_PORT", str(_DEFAULT_PORT))),
        provider=provider,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        model=model,
        system_prompt=os.environ.get("OPENCLAW_GATEWAY_SYSTEM_PROMPT", ""),
        auto_approve=os.environ.get("OPENCLAW_GATEWAY_AUTO_APPROVE", "").lower()
        in {"1", "true", "yes"},
        data_dir=Path(os.environ.get("OPENCLAW_GATEWAY_DATA_DIR", str(_DEFAULT_DATA_DIR))),
    )
