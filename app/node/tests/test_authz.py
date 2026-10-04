"""Tests for HA actor authorization prompt policy."""

from __future__ import annotations

import json
import logging
import time

import pytest
from _pytest.logging import LogCaptureFixture

from openclaw_node import authz as authz_mod
from openclaw_node.authz import (
    HOUSEHOLD_ALLOWED_SERVICES,
    Actor,
    actor_from_payload,
    actor_from_signed_body,
    derive_actor_signing_secret,
    is_forbidden,
    redact_code,
    resolve_turn_authz,
    scrub_code,
    service_allowed,
    service_for_command,
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
                remove=frozenset({"ha.call_service:lock.unlock"}),
            ),
            "admin": ForbiddenCommandPatch(remove=frozenset({"fs.write"})),
        }
    )

    assert "ha.call_service:lock.unlock" not in resolve_turn_authz(identity, None).forbidden
    assert "fs.write" not in resolve_turn_authz(identity, Actor("a", is_admin=True)).forbidden


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


def _call(domain: str, service: str) -> dict[str, object]:
    return {"domain": domain, "service": service}


def test_disclaimer_prints_the_allowed_table() -> None:
    authz = resolve_turn_authz(IdentityConfig(), Actor("kid", is_admin=False))
    exception = authz.disclaimer.split("may still be called are: ")[1].split(". Locks")[0]
    printed = set(exception.split(", "))

    expected = {f"{d}.{n}" for d, names in HOUSEHOLD_ALLOWED_SERVICES.items() for n in names}
    assert printed == expected
    assert "shell_command" not in exception
    assert "never guess or invent" in authz.disclaimer


@pytest.mark.parametrize(
    ("domain", "service", "forbidden"),
    [
        ("light", "turn_on", False),
        ("light", "toggle", False),
        ("switch", "turn_on", False),
        ("media_player", "turn_on", False),
        ("scene", "turn_on", False),
        ("cover", "open_cover", False),
        ("climate", "set_temperature", False),
        ("script", "turn_on", False),
        ("script", "turn_off", True),
        ("script", "reload", True),
        ("button", "press", False),
        ("button", "reload", True),
        ("light", "reload", True),
        ("input_select", "set_options", True),
        ("input_select", "select_option", False),
        ("light", "future_service", True),
        ("lock", "unlock", False),
        ("lock", "set_code", True),
        ("alarm_control_panel", "alarm_disarm", False),
        ("alarm_control_panel", "alarm_trigger", True),
        ("automation", "trigger", True),
        ("homeassistant", "turn_off", True),
        ("shell_command", "x", True),
    ],
)
def test_default_user_policy_matches_printed_rule(
    domain: str, service: str, forbidden: bool
) -> None:
    authz = resolve_turn_authz(IdentityConfig(), Actor("kid", is_admin=False))

    assert is_forbidden(authz.forbidden, "ha.call_service", _call(domain, service)) is forbidden
    assert service_allowed(f"{domain}.{service}") is (not forbidden) or domain == "shell_command"


def _patched(add: dict[str, frozenset[str]]) -> IdentityConfig:
    return IdentityConfig(
        forbidden_commands={role: ForbiddenCommandPatch(add=a) for role, a in add.items()}
    )


def test_admin_prohibition_is_inherited_by_user_and_logged_once(caplog: LogCaptureFixture) -> None:
    entry = "ha.call_service:cover.open_cover"
    identity = _patched({"admin": frozenset({entry}), "super_admin": frozenset({"fs.read"})})
    authz_mod._WARNED_INHERITED.clear()

    with caplog.at_level("WARNING"):
        first = resolve_turn_authz(identity, Actor("kid", is_admin=False))
        resolve_turn_authz(identity, Actor("kid", is_admin=False))

    assert entry in first.forbidden
    assert "fs.read" not in first.forbidden  # only service prohibitions are inherited
    assert sum(entry in r.getMessage() for r in caplog.records) == 1


def test_redact_code_masks_code_without_mutating_input() -> None:
    params = {"domain": "lock", "data": {"code": "4321", "x": 1}, "service_data": {"code": 9}}

    redacted = redact_code(params)

    assert redacted["data"] == {"code": "***", "x": 1}
    assert redacted["service_data"] == {"code": "***"}
    assert params["data"] == {"code": "4321", "x": 1}
    assert redact_code({"data": {"x": 1}}) == {"data": {"x": 1}}


@pytest.mark.parametrize("role_admin", [False, True])
def test_every_printed_forbidden_entry_is_enforced_and_vice_versa(role_admin: bool) -> None:
    identity = IdentityConfig(
        forbidden_commands={
            "user": ForbiddenCommandPatch(
                add=frozenset(
                    {
                        "ha.call_service:lock.*",
                    }
                ),
                remove=frozenset(),
            ),
            "admin": ForbiddenCommandPatch(
                add=frozenset(
                    {
                        "ha.call_service:lock.*",
                    }
                ),
                remove=frozenset(),
            ),
        }
    )
    authz = resolve_turn_authz(identity, Actor("a", is_admin=role_admin))

    for entry in authz.forbidden:
        assert f"  - {entry}" in authz.disclaimer
        if entry.startswith("ha.call_service:"):
            glob = entry.split(":", 1)[1]
            sample = _call("lock", "unlock") if glob != "*" else _call("automation", "trigger")
            if glob.startswith("shell_command"):
                sample = _call("shell_command", "run")
            elif glob.startswith("python_script"):
                sample = _call("python_script", "run")
            elif glob.startswith("command_line"):
                sample = _call("command_line", "run")
            elif glob == "homeassistant.stop":
                sample = _call("homeassistant", "stop")
            assert is_forbidden(authz.forbidden, "ha.call_service", sample)
        else:
            assert is_forbidden(authz.forbidden, entry, {})
    assert is_forbidden(authz.forbidden, "ha.call_service", _call("lock", "unlock"))


def test_user_service_wildcard_is_non_removable(caplog: LogCaptureFixture) -> None:
    identity = IdentityConfig(
        forbidden_commands={"user": ForbiddenCommandPatch(remove=frozenset({"ha.call_service:*"}))}
    )
    with caplog.at_level("WARNING"):
        authz = resolve_turn_authz(identity, Actor("kid", is_admin=False))

    assert "ha.call_service:*" in authz.forbidden
    assert is_forbidden(authz.forbidden, "ha.call_service", _call("automation", "trigger"))
    assert sum("ha.call_service:*" in r.getMessage() for r in caplog.records) == 1


def test_is_forbidden_wrappers_follow_service_entries_and_fail_closed() -> None:
    wildcard = ("ha.call_service:*",)

    assert not is_forbidden(wildcard, "ha.light_turn_on", {})
    assert not is_forbidden(wildcard, "ha.light_turn_off", {})
    assert is_forbidden(("ha.call_service:light.*",), "ha.light_turn_on", {})
    assert is_forbidden(("ha.light_turn_on",), "ha.light_turn_on", {})
    assert is_forbidden(wildcard, "ha.call_service", {"domain": "Light", "service": "turn_on"})
    assert is_forbidden(wildcard, "ha.call_service", {"domain": 1})
    assert not is_forbidden(("fs.write",), "ha.call_service", {"domain": 1})
    assert not is_forbidden(wildcard, "ha.get_state", {})
    assert service_for_command("ha.get_state", {}) is None


def _patched_identity(add: frozenset[str]) -> IdentityConfig:
    return IdentityConfig(forbidden_commands={"user": ForbiddenCommandPatch(add=add)})


def _printed(disclaimer: str) -> set[str]:
    return set(disclaimer.split("may still be called are: ")[1].split(". Locks")[0].split(", "))


def test_disclaimer_exception_reflects_patched_prohibitions() -> None:
    kid = Actor("kid", is_admin=False)
    one = resolve_turn_authz(_patched_identity(frozenset({"ha.call_service:light.turn_off"})), kid)
    domain = resolve_turn_authz(
        _patched_identity(frozenset({"ha.call_service:lock.*", "ha.call_service:button.press"})),
        kid,
    )

    assert "light.turn_off" not in _printed(one.disclaimer)
    assert "light.turn_on" in _printed(one.disclaimer)
    assert not {s for s in _printed(domain.disclaimer) if s.startswith("lock.")}
    assert "button.press" not in _printed(domain.disclaimer)


def test_disclaimer_matches_enforcement_for_glob_patch() -> None:
    kid = Actor("kid", is_admin=False)
    authz = resolve_turn_authz(_patched_identity(frozenset({"ha.call_service:l*.turn_on"})), kid)
    printed = _printed(authz.disclaimer)

    assert is_forbidden(authz.forbidden, "ha.call_service", _call("light", "turn_on"))
    assert "light.turn_on" not in printed
    assert "light.turn_off" in printed
    for entry in (f"{d}.{n}" for d, ns in HOUSEHOLD_ALLOWED_SERVICES.items() for n in ns):
        d, _, n = entry.partition(".")
        enforced = not is_forbidden(authz.forbidden, "ha.call_service", _call(d, n))
        assert (entry in printed) is enforced, entry


def test_scrub_code_masks_embedded_escaped_and_nested_occurrences() -> None:
    value = {
        "a": "x482913x",
        "b": ["pin 482913 ok", {"c": 'q"z'}],
        "n": 5,
        "482913": "k",
    }

    out = scrub_code(value, "482913")
    assert "482913" not in json.dumps(out)
    assert out["a"] == "x[redacted]x"
    assert out["n"] == 5
    # A code with a quote also appears JSON-escaped inside serialized text.
    code = 'q"z'
    escaped = json.dumps(code)[1:-1]
    out = scrub_code({"s": f"raw {code} esc {escaped}"}, code)
    assert out == {"s": "raw [redacted] esc [redacted]"}
    assert scrub_code(value, None) is value
    assert scrub_code(value, "") is value
    assert scrub_code("12 apples", "12") == "[redacted] apples"
