"""Tests for node runtime configuration."""

from __future__ import annotations

from openclaw_node.config import load_config


def test_load_config_addon_mode(monkeypatch) -> None:
    """Add-on mode uses Supervisor token and HA internal URL."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-token")
    monkeypatch.setenv("GATEWAY_URL", "wss://gateway.example/ws")
    monkeypatch.setenv("PAIRING_TOKEN", "pair-me")
    monkeypatch.setenv("NODE_NAME", "Kitchen HA")
    monkeypatch.delenv("HASS_TOKEN", raising=False)
    monkeypatch.delenv("HASS_URL", raising=False)

    config = load_config()

    assert config.addon_mode is True
    assert config.hass_url == "http://homeassistant"
    assert config.supervisor_token == "supervisor-token"
    assert config.gateway_url == "wss://gateway.example/ws"
    assert config.pairing_token == "pair-me"
    assert config.node_name == "Kitchen HA"
    assert str(config.key_path).endswith("/data/openclaw/node-key.json")


def test_load_config_standalone_mode(monkeypatch) -> None:
    """Standalone mode uses explicit HA URL and token."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.setenv("HASS_URL", "http://ha.local:8123")
    monkeypatch.setenv("HASS_TOKEN", "ha-token")

    config = load_config()

    assert config.addon_mode is False
    assert config.hass_url == "http://ha.local:8123"
    assert config.hass_token == "ha-token"
    assert config.supervisor_token == ""
