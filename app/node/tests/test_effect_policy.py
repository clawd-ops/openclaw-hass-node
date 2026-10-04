"""Tests for the pure effect policy (not wired to the dispatcher)."""

from __future__ import annotations

from typing import Any

import pytest

from openclaw_node import effect_policy
from openclaw_node.authz import HOUSEHOLD_AUTO_ALLOW_SERVICES, Actor, resolve_turn_authz
from openclaw_node.caller import UNTRUSTED, Caller
from openclaw_node.commands import ha
from openclaw_node.config import ForbiddenCommandPatch, IdentityConfig
from openclaw_node.effect_policy import check, classify


def _call(domain: str, service: str) -> dict[str, Any]:
    return {"domain": domain, "service": service}


def _code(refusal: dict[str, Any] | None) -> str | None:
    return None if refusal is None else str(refusal["error"])


def _turn(is_admin: bool, user_id: str = "u", identity: IdentityConfig | None = None) -> Caller:
    return Caller.from_turn(
        resolve_turn_authz(identity or IdentityConfig(), Actor(user_id, is_admin))
    )


def test_denylist_is_the_ha_dict_not_a_copy() -> None:
    assert effect_policy.DENY is ha._INTERIM_DENIED_SERVICE_PATTERNS
    assert effect_policy.AUTO_ALLOW is HOUSEHOLD_AUTO_ALLOW_SERVICES
    assert HOUSEHOLD_AUTO_ALLOW_SERVICES == ("light.turn_on", "light.turn_off")


def test_every_denylist_key_classifies_deny() -> None:
    for key in effect_policy.DENY:
        domain, _, name = key.partition(".")
        domain = "zzz" if domain == "*" else domain
        name = "reload_all" if name == "*" else name
        if key == "*.reload":
            name = "reload"
        assert classify(f"{domain}.{name}") == "deny", key


def test_classify_light_unclassified_and_malformed() -> None:
    assert classify("light.turn_on") == "auto_allow"
    assert classify("light.turn_off") == "auto_allow"
    assert classify("light.toggle") == "unclassified"
    assert classify("lock.unlock") == "unclassified"
    assert classify(None) == "deny"
    assert classify("light.reload") == "deny"


def test_untrusted_default_is_household_user() -> None:
    assert UNTRUSTED.role == "user"
    assert "ha.call_service:*" in UNTRUSTED.forbidden
    assert (
        _code(check(UNTRUSTED, "ha.call_service", _call("lock", "unlock"))) == "PERMISSION_DENIED"
    )


@pytest.mark.parametrize("service", [("light", "turn_on"), ("light", "turn_off")])
def test_user_allowed_light_calls_and_wrappers(service: tuple[str, str]) -> None:
    user = _turn(False)

    assert check(user, "ha.call_service", _call(*service)) is None
    assert check(user, f"ha.light_{service[1]}", {"entity_id": "light.x"}) is None


@pytest.mark.parametrize(
    "service",
    [("light", "toggle"), ("lock", "unlock"), ("switch", "turn_on")],
)
def test_user_refused_for_everything_else(service: tuple[str, str]) -> None:
    refusal = check(_turn(False), "ha.call_service", _call(*service))

    assert refusal is not None
    assert refusal["ok"] is False
    assert refusal["error"] == "PERMISSION_DENIED"


def test_user_forbidden_commands_refused() -> None:
    for command in ("fs.write", "system.run", "ha.reload_config", "ha.addon_restart"):
        refusal = check(_turn(False), command, {})
        assert refusal is not None
        assert refusal["error"] == "PERMISSION_DENIED"
    assert check(_turn(False), "ha.get_state", {"entity_id": "light.x"}) is None


def test_admin_unclassified_needs_approval_and_deny_refused() -> None:
    admin = _turn(True)

    refusal = check(admin, "ha.call_service", _call("switch", "turn_on"))
    assert refusal is not None
    assert refusal["error"] == "APPROVAL_REQUIRED"
    assert check(admin, "ha.call_service", _call("light", "turn_on")) is None
    denied = check(admin, "ha.call_service", _call("shell_command", "x"))
    assert denied is not None
    assert denied["error"] == "SERVICE_DENIED"
    assert _code(check(admin, "system.run", {})) == "PERMISSION_DENIED"


def test_super_admin_unclassified_needs_approval() -> None:
    identity = IdentityConfig(super_admins=frozenset({"root"}))
    sa = _turn(True, "root", identity)

    assert sa.role == "super_admin"
    assert _code(check(sa, "ha.call_service", _call("switch", "turn_on"))) == "APPROVAL_REQUIRED"
    assert check(sa, "system.run", {}) is None


def test_operator_keeps_non_denied_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    op = Caller.operator("gateway-invoke")

    with caplog.at_level("INFO"):
        assert check(op, "ha.call_service", _call("scene", "turn_on")) is None
    assert "unclassified service scene.turn_on" in caplog.text
    assert check(op, "fs.write", {}) is None
    assert check(op, "ha.call_service", _call("light", "turn_on")) is None
    denied = check(op, "ha.call_service", _call("shell_command", "x"))
    assert denied is not None
    assert denied["error"] == "SERVICE_DENIED"
    assert check(op, "ha.call_service", {"domain": 5}) is None


def test_malformed_service_refused_for_non_operator() -> None:
    refusal = check(_turn(True), "ha.call_service", {"domain": "Light", "service": "turn_on"})

    assert refusal is not None
    assert refusal["error"] == "PERMISSION_DENIED"


def test_policy_agrees_with_printed_rule_under_config_patch() -> None:
    identity = IdentityConfig(
        forbidden_commands={
            "user": ForbiddenCommandPatch(
                add=frozenset(
                    {
                        "ha.call_service:light.turn_off",
                    }
                ),
                remove=frozenset(),
            )
        }
    )
    user = _turn(False, identity=identity)
    authz = resolve_turn_authz(identity, Actor("u", is_admin=False))

    assert "ha.call_service:light.turn_off" in authz.disclaimer
    assert _code(check(user, "ha.call_service", _call("light", "turn_off"))) == "PERMISSION_DENIED"
    assert check(user, "ha.light_turn_off", {}) is not None
    assert check(user, "ha.call_service", _call("light", "turn_on")) is None

    relaxed = IdentityConfig(
        forbidden_commands={"user": ForbiddenCommandPatch(remove=frozenset({"ha.call_service:*"}))}
    )
    # The wildcard is non-removable: the patched disclaimer and the gate come
    # from the same computed set, so both still refuse an unclassified service.
    authz = resolve_turn_authz(relaxed, Actor("u", is_admin=False))
    rel = Caller.from_turn(authz)
    assert "ha.call_service:*" in authz.forbidden
    assert "the only services that may still be called are: light.turn_on, light.turn_off." in (
        authz.disclaimer
    )
    assert _code(check(rel, "ha.call_service", _call("lock", "unlock"))) == "PERMISSION_DENIED"
    assert check(rel, "ha.call_service", _call("light", "turn_on")) is None


@pytest.mark.parametrize("caller", [UNTRUSTED, _turn(True), Caller.operator("gateway-invoke")])
def test_deny_class_is_service_denied_for_every_caller(caller: Caller) -> None:
    for service in (("shell_command", "x"), ("homeassistant", "restart"), ("update", "install")):
        assert _code(check(caller, "ha.call_service", _call(*service))) == "SERVICE_DENIED"


def test_role_refusal_is_permission_denied_not_service_denied() -> None:
    assert (
        _code(check(UNTRUSTED, "ha.call_service", _call("lock", "unlock"))) == "PERMISSION_DENIED"
    )


def test_removed_user_forbidden_command_stays_in_disclaimer_and_refused() -> None:
    identity = IdentityConfig(
        forbidden_commands={
            "user": ForbiddenCommandPatch(
                add=frozenset({"ha.get_state"}),
                remove=frozenset({"ha.addon_update"}),
            )
        }
    )
    authz = resolve_turn_authz(identity, None)
    caller = Caller(role="user", actor_id="u", forbidden=authz.forbidden)

    for command in ("ha.addon_update", "ha.get_state"):
        assert f"  - {command}" in authz.disclaimer
        assert _code(check(caller, command, {})) == "PERMISSION_DENIED"
