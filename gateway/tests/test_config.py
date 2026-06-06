"""Tests for openclaw_gateway.config.load_config."""

from __future__ import annotations

from pathlib import Path

import pytest
from openclaw_gateway.config import load_config


def test_load_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "OPENCLAW_GATEWAY_HOST",
        "OPENCLAW_GATEWAY_PORT",
        "OPENCLAW_GATEWAY_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENCLAW_GATEWAY_MODEL",
        "OPENCLAW_GATEWAY_SYSTEM_PROMPT",
        "OPENCLAW_GATEWAY_AUTO_APPROVE",
        "OPENCLAW_GATEWAY_DATA_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = load_config()
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 8765
    assert cfg.provider == "anthropic"
    assert cfg.anthropic_api_key == ""
    assert cfg.openai_api_key == ""
    assert cfg.model == "claude-opus-4-7"
    assert cfg.auto_approve is False


def test_load_config_openai_provider_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_GATEWAY_PROVIDER", "openai")
    monkeypatch.delenv("OPENCLAW_GATEWAY_MODEL", raising=False)
    cfg = load_config()
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-5.5"


def test_load_config_reads_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENCLAW_GATEWAY_HOST", "127.0.0.1")
    monkeypatch.setenv("OPENCLAW_GATEWAY_PORT", "9000")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENCLAW_GATEWAY_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("OPENCLAW_GATEWAY_SYSTEM_PROMPT", "be brief")
    monkeypatch.setenv("OPENCLAW_GATEWAY_AUTO_APPROVE", "true")
    monkeypatch.setenv("OPENCLAW_GATEWAY_DATA_DIR", str(tmp_path))
    cfg = load_config()
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 9000
    assert cfg.anthropic_api_key == "sk-test"
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.system_prompt == "be brief"
    assert cfg.auto_approve is True
    assert cfg.data_dir == tmp_path


@pytest.mark.parametrize(
    ("val", "expected"), [("1", True), ("yes", True), ("0", False), ("", False)]
)
def test_load_config_auto_approve_truthy_values(
    monkeypatch: pytest.MonkeyPatch, val: str, expected: bool
) -> None:
    monkeypatch.setenv("OPENCLAW_GATEWAY_AUTO_APPROVE", val)
    assert load_config().auto_approve is expected
