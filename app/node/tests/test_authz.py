"""Tests for HA actor authorization prompt policy."""

from __future__ import annotations

import logging
import time

import pytest
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
    assert derive_actor_signing_secret("local-token") == derive_actor_signing_secret("local-token")
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
            actor_from_signed_body({**body, "actor_ts": 0, "actor_signature": "bad"}, "x") is None
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
    authz = resolve_turn_authz(IdentityConfig(default_agent_id="my-agent"), None)

    assert authz.role == "user"
    assert authz.agent_id == "my-agent"
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
        user_agent_map={"ash": "my-agent-household"},
        default_agent_id="my-agent",
    )

    authz = resolve_turn_authz(identity, Actor("ash", is_admin=True))

    assert authz.agent_id == "my-agent-household"


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
        log_agent_inventory(identity, ("my-agent", "my-agent-household"))

    text = caplog.text
    assert "Gateway agents available: my-agent, my-agent-household" in text
    assert "missing-agent" in text
    assert "bad-default" in text


def test_log_agent_inventory_accepts_valid_mapping(caplog: LogCaptureFixture) -> None:
    from openclaw_node.authz import log_agent_inventory

    identity = IdentityConfig(
        user_agent_map={"ash": "my-agent-household"},
        default_agent_id="my-agent",
    )

    with caplog.at_level(logging.INFO):
        log_agent_inventory(identity, ("my-agent", "my-agent-household"))

    text = caplog.text
    assert "user_agent_map[ash] -> my-agent-household" in text
    assert "no such agent" not in text


# The topology matrix for #347. Before the fix, the (many agents, no default)
# row produced no diagnostic at all, which is what made the production failure
# opaque: every Assist turn failed and nothing at startup predicted it. Each row
# below pins one cell, so flipping either half of the condition in
# `log_agent_inventory` fails a test rather than leaving the matrix satisfied.
_UNSET_DEFAULT_ERROR = "no agent owns an Assist turn"


@pytest.mark.parametrize(
    ("agents", "default_agent_id", "expect_error"),
    [
        # The broken topology: nothing to fall back to, so the turn is doomed.
        (("my-agent", "my-agent-household"), "", True),
        (("a", "b", "c"), "", True),
        # Configured, so resolution terminates on the operator's choice.
        (("my-agent", "my-agent-household"), "my-agent", False),
        # One agent: omitting agentId is well defined and remains correct.
        (("my-agent",), "", False),
        # No inventory reported. The add-on cannot conclude anything, and must
        # not claim a misconfiguration it has not observed.
        ((), "", False),
    ],
)
def test_unset_default_is_an_error_only_on_a_multi_agent_gateway(
    caplog: LogCaptureFixture,
    agents: tuple[str, ...],
    default_agent_id: str,
    expect_error: bool,
) -> None:
    from openclaw_node.authz import log_agent_inventory

    identity = IdentityConfig(user_agent_map={}, default_agent_id=default_agent_id)

    with caplog.at_level(logging.INFO):
        log_agent_inventory(identity, agents)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert bool(errors) is expect_error, caplog.text
    assert (_UNSET_DEFAULT_ERROR in caplog.text) is expect_error


def test_unset_default_error_names_the_setting_and_the_candidates(
    caplog: LogCaptureFixture,
) -> None:
    """The operator must be able to act on the message without grepping.

    An error that says only "misconfigured" costs the reader the same
    investigation the log was supposed to save, so assert the setting name and
    every available agent are present in one record.
    """
    from openclaw_node.authz import log_agent_inventory

    identity = IdentityConfig(user_agent_map={}, default_agent_id="")

    with caplog.at_level(logging.INFO):
        log_agent_inventory(identity, ("my-agent", "my-agent-household"))

    error = next(r for r in caplog.records if r.levelno >= logging.ERROR)
    message = error.getMessage()
    assert "identity.default_agent_id" in message
    assert "my-agent" in message
    assert "my-agent-household" in message
    assert "2 agents" in message


# Deliberately no test here asserting that `IdentityConfig` is unmodified.
# An earlier version did, and it could not fail: `IdentityConfig` is frozen and
# `log_agent_inventory` has no routing output, so the assertion held with the
# new branch deleted. The real guarantee -- that no agent is chosen on the
# operator's behalf -- is that the turn is refused rather than routed, and it is
# pinned in `test_chat_relay.py` where routing actually happens.


def test_startup_does_not_claim_a_gateway_default_that_does_not_exist(
    caplog: LogCaptureFixture,
) -> None:
    """The contradictory reassurance must be absent, not merely outweighed.

    The startup log used to report `default_agent_id: <gateway-default>`
    unconditionally, so on the broken topology it named a working fallback
    immediately before the ERROR saying no agent owns a turn. Two lines, two
    mutually exclusive diagnoses, to an operator reading the log because turns
    were failing. #347 lists it as one of four surfaces.
    """
    from openclaw_node.authz import log_agent_inventory

    identity = IdentityConfig(user_agent_map={}, default_agent_id="")
    with caplog.at_level(logging.INFO):
        log_agent_inventory(identity, ("a", "b"))

    assert "<gateway-default>" not in caplog.text
    assert "no gateway default exists" in caplog.text


def test_single_agent_still_reports_that_a_gateway_default_applies(
    caplog: LogCaptureFixture,
) -> None:
    """With one agent the gateway does resolve it, and saying so is accurate."""
    from openclaw_node.authz import log_agent_inventory

    with caplog.at_level(logging.INFO):
        log_agent_inventory(IdentityConfig(user_agent_map={}, default_agent_id=""), ("solo",))

    assert "gateway default applies" in caplog.text


def test_the_error_does_not_claim_every_turn_fails(caplog: LogCaptureFixture) -> None:
    """`user_agent_map` still resolves, so "every turn" was false.

    `resolve_agent_id` returns the mapped agent for a signed actor before
    `default_agent_id` is consulted, so those turns succeed on exactly the
    topology the ERROR describes. Claiming otherwise sends an operator looking
    for a fault in the wrong place.
    """
    from openclaw_node.authz import log_agent_inventory

    identity = IdentityConfig(user_agent_map={}, default_agent_id="")
    with caplog.at_level(logging.INFO):
        log_agent_inventory(identity, ("a", "b"))

    error = next(r for r in caplog.records if r.levelno >= logging.ERROR).getMessage()
    assert "every turn" not in error
    assert "anonymous or unmapped" in error
    assert "user_agent_map" in error


def test_mapped_user_resolves_an_agent_on_the_broken_topology() -> None:
    """The claim above, asserted against the resolver rather than the prose."""
    from openclaw_node.authz import Actor, resolve_agent_id

    identity = IdentityConfig(user_agent_map={"ash": "household"}, default_agent_id="")
    assert resolve_agent_id(identity, Actor("ash", is_admin=False)) == "household"
    assert resolve_agent_id(identity, Actor("nobody", is_admin=False)) == ""
    assert resolve_agent_id(identity, None) == ""
