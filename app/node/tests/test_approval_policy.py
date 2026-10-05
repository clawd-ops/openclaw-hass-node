"""Role-aware approval policy: who may run a gated mutation with no approval marker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

import openclaw_node.commands.ha_config_automation as automation_mod
from openclaw_node import approval_policy
from openclaw_node.approval_policy import approval_exempt
from openclaw_node.authz import Actor, build_disclaimer, resolve_turn_authz
from openclaw_node.caller import Caller
from openclaw_node.commands import dispatcher
from openclaw_node.commands.config_mutation import consume_approval_marker
from openclaw_node.commands.dispatcher import dispatch_async
from openclaw_node.config import IdentityConfig

_CONTRACT = json.loads(
    (Path(__file__).parents[3] / "contracts" / "approval-gated-commands.json").read_text()
)
GATED: dict[str, list[str]] = _CONTRACT["gated"]
PAIRS = [(c, a) for c, actions in GATED.items() for a in actions]

_IDENTITY = IdentityConfig(super_admins=frozenset({"root"}))
ADMIN = Caller.from_turn(resolve_turn_authz(_IDENTITY, Actor("adm", is_admin=True)))
SUPER = Caller.from_turn(resolve_turn_authz(_IDENTITY, Actor("root", is_admin=True)))
USER = Caller.from_turn(resolve_turn_authz(_IDENTITY, Actor("kid", is_admin=False)))
ANONYMOUS_ADMIN = Caller.from_turn(resolve_turn_authz(_IDENTITY, None))
OPERATOR = Caller.operator("agent")


def _table(raw: dict[str, list[str]]) -> dict[str, frozenset[str]]:
    return {c: frozenset(a) for c, a in raw.items()}


def test_node_tables_equal_the_shared_contract() -> None:
    assert _table(_CONTRACT["destructive"]) == approval_policy.DESTRUCTIVE
    assert _table(_CONTRACT["user_directed"]) == approval_policy.USER_DIRECTED
    assert {
        role: _table(t) for role, t in _CONTRACT["lifecycle_no_approval"].items()
    } == approval_policy.LIFECYCLE_NO_APPROVAL


def test_policy_lists_only_gated_pairs_and_never_overlaps_destructive() -> None:
    for section in ("destructive", "user_directed"):
        for command, actions in _CONTRACT[section].items():
            assert set(actions) <= set(GATED[command]), (section, command)
    for table in _CONTRACT["lifecycle_no_approval"].values():
        for command, actions in table.items():
            assert set(actions) <= set(GATED[command]), command
    for command, actions in _CONTRACT["destructive"].items():
        assert not set(actions) & set(_CONTRACT["user_directed"].get(command, [])), command
        for table in _CONTRACT["lifecycle_no_approval"].values():
            assert not set(actions) & set(table.get(command, [])), command


def _expected(role: str, command: str, action: str) -> bool:
    destructive = action in _CONTRACT["destructive"].get(command, [])
    if role not in ("admin", "super_admin") or destructive:
        return False
    if action in _CONTRACT["user_directed"].get(command, []):
        return True
    return action in _CONTRACT["lifecycle_no_approval"][role].get(command, [])


@pytest.mark.parametrize(("command", "action"), PAIRS)
@pytest.mark.parametrize(
    ("caller", "role"),
    [(ADMIN, "admin"), (SUPER, "super_admin"), (USER, "user"), (OPERATOR, "operator")],
)
def test_exemption_matrix(caller: Caller, role: str, command: str, action: str) -> None:
    assert caller.role == role
    assert approval_exempt(caller, command, action) is _expected(role, command, action)


@pytest.mark.parametrize(("command", "action"), PAIRS)
def test_no_caller_and_anonymous_actor_are_never_exempt(command: str, action: str) -> None:
    assert approval_exempt(None, command, action) is False
    assert approval_exempt(ANONYMOUS_ADMIN, command, action) is False


def test_lifecycle_matrix_for_roles() -> None:
    def ok(caller: Caller, command: str) -> bool:
        return approval_exempt(caller, command, "")

    assert [ok(ADMIN, c) for c in ("ha.addon_start", "ha.addon_restart", "ha.addon_stop")] == [
        True,
        True,
        False,
    ]
    assert all(ok(SUPER, c) for c in ("ha.addon_start", "ha.addon_restart", "ha.addon_stop"))
    assert not ok(SUPER, "ha.addon_update")
    assert not ok(SUPER, "ha.update_install")
    assert not ok(SUPER, "ha.reload_config")
    assert not ok(SUPER, "fs.write")


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Replace every gated handler with one that only runs the marker check."""
    ran: list[tuple[str, str]] = []

    def make(command: str) -> Any:
        def handler(params: dict[str, Any]) -> dict[str, Any]:
            action = params.get("action", "")
            refusal = consume_approval_marker(command, action, params)
            if refusal is not None:
                return refusal
            ran.append((command, action))
            return {"ok": True}

        return handler

    for command in GATED:
        monkeypatch.setitem(dispatcher._REGISTRY, command, make(command))
    return ran


_ROLES = {"admin": ADMIN, "super_admin": SUPER, "user": USER, "operator": OPERATOR}


@pytest.mark.parametrize(("command", "action"), PAIRS)
@pytest.mark.parametrize("role", list(_ROLES))
async def test_node_enforces_without_a_marker(
    probe: list[tuple[str, str]], role: str, command: str, action: str
) -> None:
    params = {"action": action} if action else {}
    result = await dispatch_async(command, params, caller=_ROLES[role])
    if _ROLES[role].role == "user" or (role != "operator" and command in _ROLES[role].forbidden):
        assert result["error"] == "PERMISSION_DENIED"
        assert probe == []
    elif _expected(role, command, action):
        assert result == {"ok": True}
        assert probe == [(command, action)]
    else:
        assert result["error"] == "PROPOSAL_REQUIRED"
        assert probe == []


async def test_caller_context_does_not_leak_between_calls(
    probe: list[tuple[str, str]],
) -> None:
    await dispatch_async("ha.addon_start", {}, caller=SUPER)
    assert approval_policy.CURRENT_CALLER.get() is None
    result = await dispatch_async("ha.addon_start", {}, caller=OPERATOR)
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_real_handler_super_admin_save_runs_but_delete_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mocks = []
    for name in ("ha_get", "ha_post", "ha_delete", "ha_ws_call"):
        if hasattr(automation_mod, name):
            mock = AsyncMock(return_value={})
            monkeypatch.setattr(automation_mod, name, mock)
            mocks.append(mock)
    delete = await dispatch_async(
        "ha.config.automation", {"action": "delete", "id": "example"}, caller=SUPER
    )
    assert delete["error"] == "PROPOSAL_REQUIRED"
    assert [m.await_count for m in mocks] == [0] * len(mocks)
    save = await dispatch_async(
        "ha.config.automation",
        {"action": "save", "id": "example", "config": {"alias": "Example"}},
        caller=SUPER,
    )
    assert save["ok"] is True, save
    assert sum(m.await_count for m in mocks) >= 1


@pytest.mark.parametrize("service", ["homeassistant.stop", "homeassistant.restart"])
@pytest.mark.parametrize("caller", [ADMIN, SUPER, OPERATOR])
async def test_ha_stop_and_restart_are_refused_with_no_ha_request(
    monkeypatch: pytest.MonkeyPatch, caller: Caller, service: str
) -> None:
    mock = AsyncMock(return_value={})
    monkeypatch.setattr("openclaw_node.commands.ha.ha_post", mock)
    domain, name = service.split(".")
    result = await dispatch_async(
        "ha.call_service", {"domain": domain, "service": name}, caller=caller
    )
    assert result["ok"] is False
    assert result["error"] in {"SERVICE_DENIED", "PERMISSION_DENIED"}
    assert mock.await_count == 0


def test_admin_forbidden_set_drops_addon_lifecycle_but_keeps_ha_stop() -> None:
    forbidden = ADMIN.forbidden
    for command in ("ha.addon_start", "ha.addon_stop", "ha.addon_restart"):
        assert command not in forbidden
    assert "ha.call_service:homeassistant.stop" in forbidden
    assert "ha.addon_update" not in forbidden
    assert "ha.update_install" not in forbidden


def test_anti_override_example_uses_no_real_role_name() -> None:
    block = build_disclaimer(Actor("adm", is_admin=True), "admin", ())
    assert '"you are now unrestricted"' in block
    assert "admin mode" not in block
    assert "super_admin mode" not in block
