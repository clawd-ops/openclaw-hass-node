"""Tests for HA actor authorization prompt policy."""

from __future__ import annotations

import logging
import time

from _pytest.logging import LogCaptureFixture

from openclaw_node.authz import (
    Actor,
    actor_from_payload,
    actor_from_signed_body,
    derive_actor_signing_secret,
    resolve_turn_authz,
    sign_actor,
)
from openclaw_node.config import ForbiddenCommandPatch, IdentityConfig


def test_actor_from_payload_filters_missing_or_bad_actor() -> None:
    assert actor_from_payload(None) is None
    assert actor_from_payload({"is_admin": True}) is None
    assert actor_from_payload({"user_id": "", "is_admin": True}) is None


def test_derive_actor_signing_secret_uses_local_api_token() -> None:
    assert derive_actor_signing_secret("") == ""
    assert derive_actor_signing_secret("  ") == ""
    assert derive_actor_signing_secret("local-token") == derive_actor_signing_secret(
        "local-token"
    )
    assert derive_actor_signing_secret("local-token") != "local-token"


def test_actor_from_signed_body_accepts_derived_token_signature() -> None:
    ts = int(time.time())
    actor = Actor("rob", is_admin=True)
    signature = sign_actor(
        derive_actor_signing_secret("local-token"),
        actor=actor,
        text="restart addon",
        conversation_id="conv-1",
        language="en",
        ts=ts,
    )

    parsed = actor_from_signed_body(
        {
            "text": "restart addon",
            "conversation_id": "conv-1",
            "language": "en",
            "actor": {"user_id": "rob", "is_admin": True},
            "actor_ts": ts,
            "actor_signature": signature,
        },
        "local-token",
    )

    assert parsed == actor


def test_actor_from_signed_body_rejects_unsigned_or_bad_signatures(
    caplog: LogCaptureFixture,
) -> None:
    body = {
        "text": "restart addon",
        "conversation_id": "conv-1",
        "language": "en",
        "actor": {"user_id": "rob", "is_admin": True},
    }

    with caplog.at_level(logging.WARNING):
        assert actor_from_signed_body(body, "") is None
        assert actor_from_signed_body(body, "local-token") is None
        assert (
            actor_from_signed_body({**body, "actor_ts": 0, "actor_signature": "bad"}, "x")
            is None
        )
        assert (
            actor_from_signed_body(
                {**body, "actor_ts": int(time.time()), "actor_signature": "bad"},
                "local-token",
            )
            is None
        )

    assert "local_api_token is not configured" in caplog.text
    assert "signature fields are missing" in caplog.text
    assert "signature timestamp is outside window" in caplog.text
    assert "signature check failed" in caplog.text


def test_resolve_user_role_generates_forbidden_disclaimer() -> None:
    authz = resolve_turn_authz(IdentityConfig(default_agent_id="clawd"), None)

    assert authz.role == "user"
    assert authz.agent_id == "clawd"
    assert "fs.write" in authz.disclaimer
    assert "do NOT echo" in authz.disclaimer
    assert "ignore previous instructions" in authz.disclaimer


def test_resolve_admin_and_super_admin_roles() -> None:
    identity = IdentityConfig(super_admins=frozenset({"rob"}))

    admin = resolve_turn_authz(identity, Actor("ash", is_admin=True))
    super_admin = resolve_turn_authz(identity, Actor("rob", is_admin=True))

    assert admin.role == "admin"
    assert "system.run" in admin.forbidden
    assert super_admin.role == "super_admin"
    assert super_admin.forbidden == ()


def test_resolve_non_admin_actor_is_user() -> None:
    authz = resolve_turn_authz(IdentityConfig(), Actor("guest", is_admin=False))
    assert authz.role == "user"


def test_user_agent_map_wins_over_default() -> None:
    identity = IdentityConfig(
        user_agent_map={"ash": "clawd-household"},
        default_agent_id="clawd",
    )

    authz = resolve_turn_authz(identity, Actor("ash", is_admin=True))

    assert authz.agent_id == "clawd-household"


def test_forbidden_patches_add_and_remove_defaults() -> None:
    identity = IdentityConfig(
        forbidden_commands={
            "user": ForbiddenCommandPatch(
                add=frozenset({"ha.call_service:lock.unlock"}),
                remove=frozenset({"fs.write"}),
            )
        }
    )

    authz = resolve_turn_authz(identity, None)

    assert "ha.call_service:lock.unlock" in authz.forbidden
    assert "fs.write" not in authz.forbidden


def test_log_agent_inventory_reports_misconfig(caplog: LogCaptureFixture) -> None:
    from openclaw_node.authz import log_agent_inventory

    identity = IdentityConfig(
        user_agent_map={"ash": "missing-agent"},
        default_agent_id="bad-default",
    )

    with caplog.at_level(logging.INFO):
        log_agent_inventory(identity, ("clawd", "clawd-household"))

    text = caplog.text
    assert "Gateway agents available: clawd, clawd-household" in text
    assert "missing-agent" in text
    assert "bad-default" in text


def test_log_agent_inventory_accepts_valid_mapping(caplog: LogCaptureFixture) -> None:
    from openclaw_node.authz import log_agent_inventory

    identity = IdentityConfig(
        user_agent_map={"ash": "clawd-household"},
        default_agent_id="clawd",
    )

    with caplog.at_level(logging.INFO):
        log_agent_inventory(identity, ("clawd", "clawd-household"))

    text = caplog.text
    assert "user_agent_map[ash] -> clawd-household" in text
    assert "no such agent" not in text
