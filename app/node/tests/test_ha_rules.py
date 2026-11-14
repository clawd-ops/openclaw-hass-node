"""Classification, role matrix and caller mapping for the ha.ws / ha.rest pass-through."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openclaw_node import ha_rules
from openclaw_node.authz import HOUSEHOLD_ALLOWED_SERVICES
from openclaw_node.caller import UNTRUSTED, Caller
from openclaw_node.ha_rules import (
    ROLE_KINDS,
    RULES,
    Classification,
    PolicyRole,
    classify,
    decide,
    policy_role,
)

HARD = [
    "ws auth/sign_path",
    "ws config/auth/list",
    "ws config/auth_provider/homeassistant/create",
    "ws backup/remove",
    "ws backup/delete_all",
    "svc update.install",
    "svc hassio.addon_update",
    "svc hassio.host_update",
    "svc hassio.supervisor_update",
    "svc hassio.host_reboot",
    "svc hassio.host_shutdown",
]

KINDS = [
    ("ws get_states", "diagnose"),
    ("ws config/entity_registry/list", "diagnose"),
    ("ws config/entity_registry/get_entries", "diagnose"),
    ("ws config/config_entries/entry", "diagnose"),
    ("ws logbook/get_events", "diagnose"),
    ("ws lovelace/config", "diagnose"),
    ("ws zwave_js/get_config_parameters", "diagnose"),
    ("GET /api/states/light.kitchen", "diagnose"),
    ("GET /api/error_log", "diagnose"),
    ("POST /api/template", "diagnose"),
    ("svc homeassistant.check_config", "diagnose"),
    ("svc light.turn_on", "control"),
    ("svc scene.turn_on", "control"),
    ("svc media_player.volume_set", "control"),
    ("svc homeassistant.restart", "fix"),
    ("svc homeassistant.reload_all", "fix"),
    ("svc hassio.addon_restart", "fix"),
    ("svc hassio.app_restart", "fix"),
    ("svc automation.reload", "fix"),
    ("POST /api/config/config_entries/entry/abc123/reload", "fix"),
    ("ws config/entity_registry/update", "configure"),
    ("ws lovelace/config/save", "configure"),
    ("ws input_boolean/create", "configure"),
    ("svc input_boolean.turn_on", "configure"),
    ("svc input_boolean.turn_off", "configure"),
    ("svc input_boolean.toggle", "configure"),
    ("svc input_button.press", "configure"),
    ("svc input_number.set_value", "control"),
    ("svc input_select.select_option", "control"),
    ("ws zwave_js/set_config_parameter", "configure"),
    ("POST /api/config/automation/config/12", "configure"),
    ("svc hassio.addon_stop", "configure"),
    ("ws config/entity_registry/remove", "admin"),
    ("DELETE /api/config/automation/config/12", "admin"),
    ("ws execute_script", "admin"),
    ("svc shell_command.anything", "admin"),
    ("svc homeassistant.turn_on", "admin"),
    ("svc homeassistant.stop", "admin"),
    ("ws a/brand/new/thing", "admin"),
]


@pytest.mark.parametrize(("target", "kind"), KINDS)
def test_kinds(target: str, kind: str) -> None:
    result = classify(target)
    assert (result.kind, result.hard) == (kind, False)


@pytest.mark.parametrize("target", HARD)
def test_hard_deny_set(target: str) -> None:
    result = classify(target)
    assert (result.kind, result.hard) == ("admin", True)


def test_unknown_is_admin_not_deny() -> None:
    assert classify("ws never/heard/of/it") == Classification("admin", False, "unknown")


def test_every_everyday_control_service_stays_control() -> None:
    """No earlier rule may shadow the one control table in authz."""
    for domain, services in HOUSEHOLD_ALLOWED_SERVICES.items():
        for service in services:
            expected = {"script": "admin", "button": "admin", "input_boolean": "configure"}.get(
                domain, "control"
            )
            assert classify(f"svc {domain}.{service}").kind == expected, (domain, service)


@pytest.mark.parametrize(
    "target",
    [
        "svc script.turn_on",
        "svc script.toggle",
        "svc script.turn_off",
        "svc script.bedtime",
        "svc button.press",
        "svc automation.trigger",
        "ws execute_script",
        "ws fire_event",
        "POST /api/events/some_event",
    ],
)
def test_indirect_executors_are_admin(target: str) -> None:
    result = classify(target)
    assert (result.kind, result.hard) == ("admin", False)


def test_scene_turn_on_stays_control() -> None:
    assert classify("svc scene.turn_on").kind == "control"


def test_matcher_segments() -> None:
    assert ha_rules._matches("ws config/*/list", "ws config/area_registry/list")
    assert not ha_rules._matches("ws config/*/list", "ws config/a/b/list")
    assert not ha_rules._matches("ws config/*/list", "ws config/list")
    assert ha_rules._matches("ws supervisor/**", "ws supervisor/a/b/c")
    assert ha_rules._matches("ws supervisor/**", "ws supervisor")
    assert not ha_rules._matches("ws supervisor/**", "ws supervisors/api")
    assert not ha_rules._matches("GET /api/**", "POST /api/states")
    assert not ha_rules._matches("svc light.*", "ws light/turn_on")


MATRIX: dict[PolicyRole, dict[str, str]] = {
    "owner": {
        "control": "allow",
        "diagnose": "allow",
        "fix": "allow",
        "configure": "allow",
        "admin": "approval",
        "hard": "approval",
    },
    "operator": {
        "control": "allow",
        "diagnose": "allow",
        "fix": "allow",
        "configure": "approval",
        "admin": "approval",
        "hard": "approval",
    },
    "household": {
        "control": "allow",
        "diagnose": "allow",
        "fix": "allow",
        "configure": "refuse",
        "admin": "refuse",
        "hard": "refuse",
    },
    "guest": {
        "control": "allow",
        "diagnose": "allow",
        "fix": "refuse",
        "configure": "refuse",
        "admin": "refuse",
        "hard": "refuse",
    },
}


@pytest.mark.parametrize("role", list(MATRIX))
@pytest.mark.parametrize("kind", ["control", "diagnose", "fix", "configure", "admin", "hard"])
def test_role_matrix(role: PolicyRole, kind: str) -> None:
    target = {
        "control": "svc light.turn_on",
        "diagnose": "ws get_states",
        "fix": "svc homeassistant.restart",
        "configure": "ws config/entity_registry/update",
        "admin": "ws config/entity_registry/remove",
        "hard": "svc update.install",
    }[kind]
    assert decide(role, classify(target)) == MATRIX[role][kind]


def test_hard_deny_never_resolves_to_allow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even a role table edited to allow admin cannot let a hard-deny item through."""
    loosened = {**ROLE_KINDS, "household": {**ROLE_KINDS["household"], "admin": "allow"}}
    monkeypatch.setattr(ha_rules, "ROLE_KINDS", loosened)
    assert decide("household", classify("ws config/entity_registry/remove")) == "allow"
    assert decide("household", classify("svc update.install")) == "approval"


def test_caller_to_role_mapping() -> None:
    assert policy_role(Caller(role="super_admin", actor_id="owner-id")) == "owner"
    assert policy_role(Caller(role="admin", actor_id="adult-1")) == "household"
    assert policy_role(Caller(role="user", actor_id="adult-2")) == "household"
    assert policy_role(Caller.operator("gateway-invoke")) == "operator"
    assert policy_role(Caller(role="super_admin", actor_id="<anonymous>")) == "guest"
    assert policy_role(Caller(role="user", actor_id="<anonymous>")) == "guest"
    assert policy_role(UNTRUSTED) == "guest"
    assert policy_role(None) == "guest"


_MIRROR = (
    Path(__file__).parents[3] / "plugins/openclaw-hass-node-assist-tools/src/shared/ha-rules.json"
)


def test_plugin_mirror_equals_the_rules_table_and_its_cases() -> None:
    """The approval prompt's summary reads this mirror; it must be the node's own table."""
    mirror = json.loads(_MIRROR.read_text())

    assert mirror["rules"] == [list(rule) for rule in RULES], (
        "ha-rules.json is stale: rewrite its 'rules' from ha_rules.RULES"
    )
    for target, kind, hard in mirror["cases"]:
        assert (classify(target).kind, classify(target).hard) == (kind, hard), target
