"""Tests for node runtime configuration."""

from __future__ import annotations

import base64
import json

import pytest

from openclaw_node.config import allowed_roots_for_env, load_config, normalize_pairing_token


def test_load_config_addon_mode(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_load_config_identity_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """Identity env values parse into typed config."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-token")
    monkeypatch.setenv("OPENCLAW_IDENTITY_SUPER_ADMINS", '["rob"]')
    monkeypatch.setenv("OPENCLAW_IDENTITY_USER_AGENT_MAP", '{"ash":"clawd-household"}')
    monkeypatch.setenv("OPENCLAW_IDENTITY_DEFAULT_AGENT_ID", "clawd")
    monkeypatch.setenv(
        "OPENCLAW_IDENTITY_FORBIDDEN_COMMANDS",
        '{"user":{"add":["ha.call_service:lock.unlock"],"remove":["script.*"]}}',
    )
    monkeypatch.setenv("OPENCLAW_ADDON_LIFECYCLE_ALLOWLIST", '["openclaw_hass_node"]')
    monkeypatch.setenv("OPENCLAW_ADDON_LIFECYCLE_DENYLIST", '["bad_addon"]')

    config = load_config()

    assert config.identity.super_admins == frozenset({"rob"})
    assert config.identity.user_agent_map == {"ash": "clawd-household"}
    assert config.identity.default_agent_id == "clawd"
    assert config.identity.forbidden_commands["user"].add == frozenset(
        {"ha.call_service:lock.unlock"}
    )
    assert config.identity.forbidden_commands["user"].remove == frozenset({"script.*"})
    assert config.identity.addon_lifecycle_allowlist == frozenset({"openclaw_hass_node"})
    assert config.identity.addon_lifecycle_denylist == frozenset({"bad_addon"})


def test_load_config_identity_options_ignore_invalid_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid identity env shapes fail closed to defaults."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-token")
    monkeypatch.setenv("OPENCLAW_IDENTITY_SUPER_ADMINS", '{"not":"a list"}')
    monkeypatch.setenv("OPENCLAW_IDENTITY_USER_AGENT_MAP", '["not", "a map"]')
    monkeypatch.setenv("OPENCLAW_IDENTITY_FORBIDDEN_COMMANDS", '["not", "a map"]')
    monkeypatch.setenv("OPENCLAW_ADDON_LIFECYCLE_ALLOWLIST", "not-json,other")

    config = load_config()

    assert config.identity.super_admins == frozenset()
    assert config.identity.user_agent_map == {}
    assert config.identity.forbidden_commands == {}
    assert config.identity.addon_lifecycle_allowlist == frozenset({"not-json", "other"})


def test_load_config_identity_options_ignore_bad_forbidden_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed per-role patches are skipped."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-token")
    monkeypatch.setenv(
        "OPENCLAW_IDENTITY_FORBIDDEN_COMMANDS",
        '{"user":{"add":"bad","remove":[]},"nobody":{"add":["x"],"remove":[]}}',
    )

    config = load_config()

    assert config.identity.forbidden_commands == {}


def test_load_config_identity_bad_json_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed JSON env values are ignored."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-token")
    monkeypatch.setenv("OPENCLAW_IDENTITY_USER_AGENT_MAP", "{bad-json")

    config = load_config()

    assert config.identity.user_agent_map == {}


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        # Truthy bool aliases all coerce to safe "token" mode.
        ("1", "token"),
        ("true", "token"),
        ("TRUE", "token"),
        ("yes", "token"),
        ("YES", "token"),
        ("on", "token"),
        ("y", "token"),
        ("token", "token"),
        # Identity wipe is opt-in via explicit string.
        ("identity", "identity"),
        ("full", "identity"),
        # Falsy and "none" => no-op.
        ("", "none"),
        ("0", "none"),
        ("false", "none"),
        ("False", "none"),
        ("FALSE", "none"),
        ("no", "none"),
        ("off", "none"),
        ("n", "none"),
        ("none", "none"),
    ],
)
def test_load_config_reset_pairing_env(
    monkeypatch: pytest.MonkeyPatch, env_value: str, expected: str
) -> None:
    """``OPENCLAW_RESET_PAIRING`` canonicalises to one of none/token/identity."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sv-tok")
    monkeypatch.setenv("OPENCLAW_RESET_PAIRING", env_value)

    config = load_config()

    assert config.reset_pairing == expected


@pytest.mark.parametrize("garbage", ["maybe", "2", "tru", "yesplease", "identitee"])
def test_load_config_reset_pairing_unknown_value_is_none(
    monkeypatch: pytest.MonkeyPatch, garbage: str
) -> None:
    """Unknown values must default to ``"none"`` so typos cannot trigger a wipe."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sv-tok")
    monkeypatch.setenv("OPENCLAW_RESET_PAIRING", garbage)

    config = load_config()

    assert config.reset_pairing == "none"


def test_load_config_reset_pairing_default_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``OPENCLAW_RESET_PAIRING`` set, reset_pairing must be ``"none"``."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sv-tok")
    monkeypatch.delenv("OPENCLAW_RESET_PAIRING", raising=False)

    config = load_config()

    assert config.reset_pairing == "none"


def test_load_config_standalone_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Standalone mode uses explicit HA URL and token."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.setenv("HASS_URL", "http://ha.local:8123")
    monkeypatch.setenv("HASS_TOKEN", "ha-token")

    config = load_config()

    assert config.addon_mode is False
    assert config.hass_url == "http://ha.local:8123"
    assert config.hass_token == "ha-token"
    assert config.supervisor_token == ""


def test_allowed_roots_for_env_addon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Add-on mode returns the standard HA add-on roots."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    monkeypatch.delenv("OPENCLAW_ALLOWED_ROOTS", raising=False)
    roots = allowed_roots_for_env()
    root_strs = {str(r) for r in roots}
    assert "/config" in root_strs
    assert "/share" in root_strs


def test_allowed_roots_for_env_standalone_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Standalone mode with no OPENCLAW_ALLOWED_ROOTS returns empty."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("OPENCLAW_ALLOWED_ROOTS", raising=False)
    roots = allowed_roots_for_env()
    assert roots == ()


# ---- normalize_pairing_token ----


def _encode_setup_code(payload: dict[str, object]) -> str:
    """Encode a dict the way ``openclaw qr --setup-code-only`` would."""
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8"))
    return encoded.decode("ascii").rstrip("=")


def test_normalize_pairing_token_passes_raw_token_through() -> None:
    """A raw bootstrap token (not base64-JSON) must round-trip unchanged."""
    raw = "KsQ3euJaFrppxKsdqV4QUAJXhbtGg5pgg368BGUbwOk"
    assert normalize_pairing_token(raw) == raw


def test_normalize_pairing_token_extracts_bootstrap_token_from_setup_code() -> None:
    """An ``openclaw qr --setup-code-only`` payload must yield bootstrapToken."""
    setup = _encode_setup_code({"url": "wss://gw.example/ws", "bootstrapToken": "extracted-token"})
    assert normalize_pairing_token(setup) == "extracted-token"


def test_normalize_pairing_token_strips_whitespace() -> None:
    """Leading/trailing whitespace from copy-paste must not break detection."""
    setup = _encode_setup_code({"url": "x", "bootstrapToken": "tok"})
    assert normalize_pairing_token(f"  {setup}\n") == "tok"


def test_normalize_pairing_token_empty_string_is_empty() -> None:
    """Empty input is allowed (no pairing on first boot — uses persisted token)."""
    assert normalize_pairing_token("") == ""


def test_normalize_pairing_token_garbage_passes_through() -> None:
    """Non-base64 garbage is treated as a raw token (back-compat)."""
    assert normalize_pairing_token("not!base64!at!all") == "not!base64!at!all"


def test_normalize_pairing_token_base64_non_json_passes_through() -> None:
    """Base64 that doesn't decode to JSON is treated as a raw token."""
    raw_b64 = base64.urlsafe_b64encode(b"\xff\xfe\xfd").decode("ascii").rstrip("=")
    assert normalize_pairing_token(raw_b64) == raw_b64


def test_normalize_pairing_token_envelope_without_bootstrap_key() -> None:
    """A base64-JSON object lacking bootstrapToken is treated as a raw token."""
    envelope = _encode_setup_code({"url": "x", "other": "value"})
    # No bootstrapToken to extract — pass through unchanged.
    assert normalize_pairing_token(envelope) == envelope


def test_normalize_pairing_token_envelope_with_empty_bootstrap_key() -> None:
    """Empty bootstrapToken value falls back to raw."""
    envelope = _encode_setup_code({"url": "x", "bootstrapToken": ""})
    assert normalize_pairing_token(envelope) == envelope


def test_load_config_decodes_setup_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_config must apply the setup-code normalisation for env-supplied tokens."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sv-tok")
    setup = _encode_setup_code(
        {"url": "wss://gw.example/ws", "bootstrapToken": "decoded-bootstrap"}
    )
    monkeypatch.setenv("PAIRING_TOKEN", setup)
    monkeypatch.setenv("GATEWAY_URL", "wss://gw.example/ws")
    config = load_config()
    assert config.pairing_token == "decoded-bootstrap"
