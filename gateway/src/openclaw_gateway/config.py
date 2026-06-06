"""Runtime configuration for the OpenClaw gateway process.

Mirrors the style of :mod:`openclaw_node.config`: env-driven, immutable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_HOST = "0.0.0.0"  # nosec B104 - intentional bind-all for add-on use  # noqa: S104
_DEFAULT_PORT = 8765
_DEFAULT_MODEL = "claude-opus-4-7"
_DEFAULT_DATA_DIR = Path.home() / ".openclaw" / "hass-gateway"


@dataclass(frozen=True)
class GatewayConfig:
    """Immutable runtime configuration for the gateway process.

    Attributes:
        host: WS bind host.
        port: WS bind port.
        anthropic_api_key: Required key for Claude. Empty disables the brain.
        model: Brain model id.
        system_prompt: Optional system prompt prepended to every Assist turn.
        auto_approve: If True, unknown devices pair on first connect.
        data_dir: Directory holding the device registry JSON.
    """

    host: str
    port: int
    anthropic_api_key: str
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
        ANTHROPIC_API_KEY: required for live brain.
        OPENCLAW_GATEWAY_MODEL: brain model id (default claude-opus-4-7).
        OPENCLAW_GATEWAY_SYSTEM_PROMPT: optional system prompt.
        OPENCLAW_GATEWAY_AUTO_APPROVE: "1"/"true" → auto-pair (trial mode).
        OPENCLAW_GATEWAY_DATA_DIR: directory for state (default ~/.openclaw/hass-gateway).
    """
    return GatewayConfig(
        host=os.environ.get("OPENCLAW_GATEWAY_HOST", _DEFAULT_HOST),
        port=int(os.environ.get("OPENCLAW_GATEWAY_PORT", str(_DEFAULT_PORT))),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        model=os.environ.get("OPENCLAW_GATEWAY_MODEL", _DEFAULT_MODEL),
        system_prompt=os.environ.get("OPENCLAW_GATEWAY_SYSTEM_PROMPT", ""),
        auto_approve=os.environ.get("OPENCLAW_GATEWAY_AUTO_APPROVE", "").lower()
        in {"1", "true", "yes"},
        data_dir=Path(os.environ.get("OPENCLAW_GATEWAY_DATA_DIR", str(_DEFAULT_DATA_DIR))),
    )
