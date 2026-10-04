"""The remaining ha.* commands refuse unknown and null parameters before any HA request."""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openclaw_node.commands import ha
from openclaw_node.commands.config_mutation import APPROVAL_PARAM as APPROVAL

Handler = Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]

SLUG = "my_addon"
MARKER = {"id": "m", "exp": 1, "bind": "b"}
CONTRACT = (
    Path(__file__).resolve().parents[3]
    / "plugins/openclaw-hass-node-assist-tools/src/tools/assist-command-contract.json"
)

# command -> (handler, accepted keys, a valid parameter dict using every accepted key).
READS: dict[str, tuple[Handler, frozenset[str], dict[str, Any]]] = {
    "ha.get_state": (ha.handle_ha_get_state, ha._GET_STATE_KEYS, {"entity_id": "light.a"}),
    "ha.list_areas": (ha.handle_ha_list_areas, ha._NO_KEYS, {}),
    "ha.get_config": (ha.handle_ha_get_config, ha._NO_KEYS, {}),
    "ha.list_events": (ha.handle_ha_list_events, ha._NO_KEYS, {}),
    "ha.check_config": (ha.handle_ha_check_config, ha._NO_KEYS, {}),
    "ha.core_logs": (ha.handle_ha_core_logs, ha._CORE_LOGS_KEYS, {"lines": 5}),
    "ha.calendar_get_events": (
        ha.handle_ha_calendar_get_events,
        ha._CALENDAR_KEYS,
        {"entity_id": "calendar.a", "start_date_time": "s", "end_date_time": "e"},
    ),
    "ha.list_addons": (ha.handle_ha_list_addons, ha._NO_KEYS, {}),
    "ha.addon_info": (ha.handle_ha_addon_info, ha._SLUG_KEYS, {"slug": SLUG}),
    "ha.addon_stats": (ha.handle_ha_addon_stats, ha._SLUG_KEYS, {"slug": SLUG}),
    "ha.addon_logs": (ha.handle_ha_addon_logs, ha._ADDON_LOGS_KEYS, {"slug": SLUG, "lines": 5}),
    "ha.addon_changelog": (ha.handle_ha_addon_changelog, ha._SLUG_KEYS, {"slug": SLUG}),
    "ha.addon_documentation": (ha.handle_ha_addon_documentation, ha._SLUG_KEYS, {"slug": SLUG}),
    "ha.supervisor_info": (ha.handle_ha_supervisor_info, ha._NO_KEYS, {}),
}
MUTATING: dict[str, tuple[Handler, frozenset[str], dict[str, Any]]] = {
    "ha.light_turn_on": (
        ha.handle_ha_light_turn_on,
        ha._LIGHT_ON_KEYS,
        {
            "entity_id": "light.a",
            "area_id": "kitchen",
            "device_id": "d",
            "brightness": 1,
            "brightness_pct": 1,
            "color_temp_kelvin": 3000,
            "rgb_color": [1, 2, 3],
            "transition": 1,
        },
    ),
    "ha.light_turn_off": (
        ha.handle_ha_light_turn_off,
        ha._LIGHT_OFF_KEYS,
        {"entity_id": "light.a", "area_id": "kitchen", "device_id": "d", "transition": 1},
    ),
    "ha.reload_config": (
        ha.handle_ha_reload_config,
        ha._RELOAD_CONFIG_KEYS,
        {"domain": "core", APPROVAL: MARKER},
    ),
    "ha.update_install": (
        ha.handle_ha_update_install,
        ha._UPDATE_INSTALL_KEYS,
        {"entity_id": "update.a", "backup": True, "version": "1", APPROVAL: MARKER},
    ),
    "ha.addon_start": (
        ha.handle_ha_addon_start,
        ha._LIFECYCLE_KEYS,
        {"slug": SLUG, APPROVAL: MARKER},
    ),
    "ha.addon_stop": (
        ha.handle_ha_addon_stop,
        ha._LIFECYCLE_KEYS,
        {"slug": SLUG, APPROVAL: MARKER},
    ),
    "ha.addon_restart": (
        ha.handle_ha_addon_restart,
        ha._LIFECYCLE_KEYS,
        {"slug": SLUG, APPROVAL: MARKER},
    ),
    "ha.addon_update": (
        ha.handle_ha_addon_update,
        ha._LIFECYCLE_KEYS,
        {"slug": SLUG, APPROVAL: MARKER},
    ),
}
TIER_B = {"ha.reload_config", "ha.update_install"} | {
    c for c in MUTATING if c.startswith("ha.addon_")
}
ALL = {**READS, **MUTATING}
COMMANDS = sorted(ALL)
# The approval marker is owned by the approval check, which runs after the key check.
NULL_CASES = [(c, k) for c in COMMANDS for k in sorted(ALL[c][1]) if k != APPROVAL]


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADDON_LIFECYCLE_ALLOWLIST", SLUG)


@pytest.fixture
def ha_calls(monkeypatch: pytest.MonkeyPatch) -> list[AsyncMock]:
    mocks: list[AsyncMock] = []
    for name in (
        "ha_get",
        "ha_post",
        "ha_ws_call",
        "supervisor_get_json",
        "supervisor_get_text",
        "supervisor_post_json",
    ):
        mock = AsyncMock(name=name, return_value="" if name == "supervisor_get_text" else {})
        monkeypatch.setattr(ha, name, mock)
        mocks.append(mock)
    return mocks


def assert_refused(result: dict[str, Any], key: str, calls: list[AsyncMock]) -> None:
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"
    assert key in result["message"]
    for mock in calls:
        mock.assert_not_awaited()


def test_fixture_inventory_matches_accepted_keys() -> None:
    for command, (_handler, keys, valid) in ALL.items():
        assert set(valid) == keys, command


@pytest.mark.parametrize("command", COMMANDS)
async def test_unknown_key_refused_without_ha_request(
    command: str, ha_calls: list[AsyncMock]
) -> None:
    handler, _keys, valid = ALL[command]
    assert_refused(await handler({**valid, "bogus": 1}), "bogus", ha_calls)


@pytest.mark.parametrize(("command", "key"), NULL_CASES)
async def test_null_value_refused_without_ha_request(
    command: str, key: str, ha_calls: list[AsyncMock]
) -> None:
    handler, _keys, valid = ALL[command]
    assert_refused(await handler({**valid, key: None}), key, ha_calls)


@pytest.mark.parametrize("command", COMMANDS)
async def test_full_valid_params_pass_the_key_check(
    command: str, ha_calls: list[AsyncMock]
) -> None:
    handler, _keys, valid = ALL[command]
    result = await handler(dict(valid))
    assert "unknown parameter" not in result.get("message", "")
    assert "must not be null" not in result.get("message", "")


@pytest.mark.parametrize("command", sorted(TIER_B))
async def test_key_check_runs_before_approval_check(
    command: str, ha_calls: list[AsyncMock]
) -> None:
    handler, _keys, valid = ALL[command]
    params = {k: v for k, v in valid.items() if k != APPROVAL}
    result = await handler({**params, "bogus": 1})
    assert result["error"] == "INVALID_PARAM"
    result = await handler(params)
    assert result["error"] == "PROPOSAL_REQUIRED"
    for mock in ha_calls:
        mock.assert_not_awaited()


@pytest.mark.parametrize(
    "command", ["ha.addon_start", "ha.addon_stop", "ha.addon_restart", "ha.addon_update"]
)
async def test_lifecycle_policy_runs_before_key_check(
    command: str, ha_calls: list[AsyncMock]
) -> None:
    handler, _keys, _valid = ALL[command]
    result = await handler({"slug": "other_addon", "bogus": 1})
    assert result["error"] == "PERMISSION_DENIED"
    for mock in ha_calls:
        mock.assert_not_awaited()


@pytest.mark.parametrize("key", ["include_traces", "entity_filter", "state_filter"])
async def test_list_automations_refuses_null_key(key: str, ha_calls: list[AsyncMock]) -> None:
    result = await ha.handle_ha_list_automations({key: None})
    assert result["error"] == "INVALID_PARAM"
    assert key in result["message"]
    for mock in ha_calls:
        mock.assert_not_awaited()


def test_every_key_a_wrapper_emits_is_accepted_by_the_handler() -> None:
    registrations = json.loads(CONTRACT.read_text())["registrations"]
    seen: set[str] = set()
    for item in registrations:
        command = item["node_command"]
        if command not in ALL:
            continue
        seen.add(command)
        emitted = {key for key, source in item["emitted_params"].items() if source is not None}
        emitted |= set(item["injected_node_params"].values())
        assert emitted <= ALL[command][1], (command, emitted - ALL[command][1])
    assert seen == set(ALL)
