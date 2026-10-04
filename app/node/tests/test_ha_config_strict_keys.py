"""Every ha.config.* action refuses unknown and null parameters before any HA request."""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openclaw_node import ha_client

# domain -> action -> a valid parameter dict (every accepted key present once).
VALID: dict[str, dict[str, dict[str, Any]]] = {
    "automation": {
        "get": {"id": "a"},
        "save": {"id": "a", "config": {"alias": "A"}, "proposal_id": "p"},
        "delete": {"id": "a", "proposal_id": "p"},
    },
    "script": {
        "get": {"id": "a"},
        "save": {"id": "a", "config": {"alias": "A"}, "proposal_id": "p"},
        "delete": {"id": "a", "proposal_id": "p"},
    },
    "scene": {
        "get": {"id": "a"},
        "save": {"id": "a", "config": {"name": "A"}, "proposal_id": "p"},
        "delete": {"id": "a", "proposal_id": "p"},
    },
    "lovelace": {
        "get": {"url_path": "dash"},
        "save": {"url_path": "dash", "config": {"views": []}, "proposal_id": "p"},
        "dashboards_list": {},
        "resources_list": {},
        "resources_create": {"url": "/local/a.js", "res_type": "module", "proposal_id": "p"},
    },
    "helpers": {
        "list": {"helper_type": "input_boolean"},
        "create": {"helper_type": "input_boolean", "attrs": {"name": "A"}, "proposal_id": "p"},
        "update": {
            "helper_type": "input_boolean",
            "input_boolean_id": "a",
            "attrs": {"name": "A"},
            "proposal_id": "p",
        },
        "delete": {"helper_type": "input_boolean", "input_boolean_id": "a", "proposal_id": "p"},
    },
    "area_registry": {
        "list": {},
        "create": {"name": "A", "attrs": {"icon": "mdi:a"}, "proposal_id": "p"},
        "update": {"area_id": "a", "attrs": {"name": "A"}, "proposal_id": "p"},
        "delete": {"area_id": "a", "proposal_id": "p"},
    },
    "device_registry": {
        "list": {"area_id": "a", "config_entry_id": "e"},
        "update": {"device_id": "d", "attrs": {"name": "A"}, "proposal_id": "p"},
    },
    "entity_registry": {
        "list": {"domain": "light", "platform": "hue", "area_id": "a", "device_id": "d"},
        "get": {"entity_id": "light.a"},
        "update": {"entity_id": "light.a", "attrs": {"name": "A"}, "proposal_id": "p"},
        "remove": {"entity_id": "light.a", "proposal_id": "p"},
    },
    "config_entries": {
        "get": {"entry_id": "e"},
        "disable": {"entry_id": "e", "proposal_id": "p"},
        "enable": {"entry_id": "e", "proposal_id": "p"},
    },
}
MUTATING = {
    "automation": {"save", "delete"},
    "script": {"save", "delete"},
    "scene": {"save", "delete"},
    "lovelace": {"save", "resources_create"},
    "helpers": {"create", "update", "delete"},
    "area_registry": {"create", "update", "delete"},
    "device_registry": {"update"},
    "entity_registry": {"update", "remove"},
    "config_entries": {"disable", "enable"},
}
# Documented as nullable: omitting url_path selects the default dashboard.
NULLABLE = {("lovelace", "get", "url_path"), ("lovelace", "save", "url_path")}

CASES = [(d, a) for d, actions in VALID.items() for a in actions]
NULL_CASES = [
    (d, a, k) for d, a in CASES for k in ["action", *VALID[d][a]] if (d, a, k) not in NULLABLE
]


@pytest.fixture
def ha_calls(monkeypatch: pytest.MonkeyPatch) -> list[AsyncMock]:
    mocks: list[AsyncMock] = []
    modules = [ha_client] + [
        importlib.import_module(f"openclaw_node.commands.ha_config_{d}") for d in VALID
    ]
    for module in modules:
        for name in ("ha_get", "ha_post", "ha_delete", "ha_ws_call"):
            if hasattr(module, name):
                mock = AsyncMock(name=f"{module.__name__}.{name}", return_value={})
                monkeypatch.setattr(module, name, mock)
                mocks.append(mock)
    return mocks


async def call(domain: str, params: dict[str, Any]) -> dict[str, Any]:
    module = importlib.import_module(f"openclaw_node.commands.ha_config_{domain}")
    result: dict[str, Any] = await getattr(module, f"handle_ha_config_{domain}")(params)
    return result


def assert_no_ha_request(mocks: list[AsyncMock]) -> None:
    for mock in mocks:
        mock.assert_not_awaited()


def test_fixture_inventory_matches_handlers() -> None:
    for domain, actions in VALID.items():
        module = importlib.import_module(f"openclaw_node.commands.ha_config_{domain}")
        assert set(actions) == module._ACTIONS == set(module._ACTION_KEYS)
        for action, params in actions.items():
            # The helpers item id key is named after the helper type, added at call time.
            fixed = {k for k in params if k != "input_boolean_id"}
            reserved = {"_openclaw_approval"}  # approval marker, not a caller-visible param
            assert {"action", *fixed} == module._ACTION_KEYS[action] - reserved
            assert (action in MUTATING.get(domain, set())) == (
                action not in {"get", "list", "dashboards_list", "resources_list"}
            )


@pytest.mark.parametrize(("domain", "action"), CASES)
async def test_unknown_key_refused_without_ha_request(
    domain: str, action: str, ha_calls: list[AsyncMock]
) -> None:
    result = await call(domain, {"action": action, **VALID[domain][action], "bogus": 1})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"
    assert "bogus" in result["message"]
    assert "allowed:" in result["message"]
    assert_no_ha_request(ha_calls)


@pytest.mark.parametrize(("domain", "action", "key"), NULL_CASES)
async def test_null_for_accepted_key_refused_without_ha_request(
    domain: str, action: str, key: str, ha_calls: list[AsyncMock]
) -> None:
    result = await call(domain, {"action": action, **VALID[domain][action], key: None})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"
    assert key in result["message"]
    assert_no_ha_request(ha_calls)


@pytest.mark.parametrize(("domain", "action"), CASES)
async def test_valid_call_unchanged(domain: str, action: str, ha_calls: list[AsyncMock]) -> None:
    result = await call(domain, {"action": action, **VALID[domain][action]})
    if action in MUTATING.get(domain, set()):
        assert result["error"] == "PROPOSAL_REQUIRED"
        assert_no_ha_request(ha_calls)
    else:
        assert result.get("error") != "INVALID_PARAM"
        assert any(mock.await_count for mock in ha_calls)


async def test_lovelace_null_url_path_selects_default_dashboard(
    ha_calls: list[AsyncMock],
) -> None:
    result = await call("lovelace", {"action": "get", "url_path": None})
    assert result.get("error") != "INVALID_PARAM"
    assert any(mock.await_count for mock in ha_calls)


@pytest.mark.parametrize("action", ["update", "delete"])
async def test_helpers_item_key_must_match_helper_type(
    action: str, ha_calls: list[AsyncMock]
) -> None:
    params = {**VALID["helpers"][action], "counter_id": "a"}
    del params["input_boolean_id"]
    result = await call("helpers", {"action": action, **params})
    assert result["error"] == "INVALID_PARAM"
    assert "counter_id" in result["message"]
    assert_no_ha_request(ha_calls)


@pytest.mark.parametrize("domain", sorted(VALID))
async def test_unknown_action_still_reports_the_action(
    domain: str, ha_calls: list[AsyncMock]
) -> None:
    result = await call(domain, {"action": "bogus", "extra": 1})
    assert result["error"] == "INVALID_PARAM"
    assert "action must be one of" in result["message"]
    assert_no_ha_request(ha_calls)
