"""Tests for openclaw_node.commands.ha."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from openclaw_node.commands.ha import (
    handle_ha_addon_changelog,
    handle_ha_addon_documentation,
    handle_ha_addon_info,
    handle_ha_addon_logs,
    handle_ha_addon_restart,
    handle_ha_addon_start,
    handle_ha_addon_stats,
    handle_ha_addon_stop,
    handle_ha_addon_update,
    handle_ha_calendar_get_events,
    handle_ha_call_service,
    handle_ha_check_config,
    handle_ha_core_logs,
    handle_ha_get_config,
    handle_ha_get_state,
    handle_ha_history,
    handle_ha_light_turn_off,
    handle_ha_light_turn_on,
    handle_ha_list_addons,
    handle_ha_list_areas,
    handle_ha_list_automations,
    handle_ha_list_config_entries,
    handle_ha_list_devices,
    handle_ha_list_entity_registry,
    handle_ha_list_events,
    handle_ha_list_services,
    handle_ha_list_states,
    handle_ha_logbook,
    handle_ha_reload_config,
    handle_ha_update_install,
)
from openclaw_node.ha_client import HAClientError

# ---------------------------------------------------------------------------
# ha.list_states
# ---------------------------------------------------------------------------


async def test_list_states_returns_all() -> None:
    states = [
        {"entity_id": "sensor.a", "state": "1"},
        {"entity_id": "light.b", "state": "on"},
    ]
    with patch("openclaw_node.commands.ha.ha_get", return_value=states):
        result = await handle_ha_list_states({})
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["states"] == states


async def test_list_states_filters_by_domain() -> None:
    states = [
        {"entity_id": "sensor.a", "state": "1"},
        {"entity_id": "light.b", "state": "on"},
        {"entity_id": "sensor.c", "state": "2"},
    ]
    with patch("openclaw_node.commands.ha.ha_get", return_value=states):
        result = await handle_ha_list_states({"domain": "sensor"})
    assert result["count"] == 2
    assert all(s["entity_id"].startswith("sensor.") for s in result["states"])


async def test_list_states_ha_error_returns_wire_error() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_get",
        side_effect=HAClientError("HA_AUTH", "401"),
    ):
        result = await handle_ha_list_states({})
    assert result["ok"] is False
    assert result["error"] == "HA_AUTH"


async def test_list_states_bad_response_shape() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value={"not": "a list"}):
        result = await handle_ha_list_states({})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_list_states_filter_ignores_malformed_entries() -> None:
    states: list[dict[str, Any]] = [
        {"entity_id": "sensor.a"},
        {"no_entity_id": "x"},
        {"entity_id": 123},
    ]
    with patch("openclaw_node.commands.ha.ha_get", return_value=states):
        result = await handle_ha_list_states({"domain": "sensor"})
    assert result["count"] == 1


# ---------------------------------------------------------------------------
# ha.get_state
# ---------------------------------------------------------------------------


async def test_get_state_missing_entity_id() -> None:
    result = await handle_ha_get_state({})
    assert result["error"] == "MISSING_PARAM"


async def test_get_state_returns_state() -> None:
    state = {"entity_id": "sensor.a", "state": "1"}
    with patch("openclaw_node.commands.ha.ha_get", return_value=state):
        result = await handle_ha_get_state({"entity_id": "sensor.a"})
    assert result["ok"] is True
    assert result["state"] == state


async def test_get_state_ha_not_found() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_get",
        side_effect=HAClientError("HA_NOT_FOUND", "404"),
    ):
        result = await handle_ha_get_state({"entity_id": "sensor.missing"})
    assert result["error"] == "HA_NOT_FOUND"


async def test_get_state_bad_response_shape() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value=["not", "a", "dict"]):
        result = await handle_ha_get_state({"entity_id": "sensor.x"})
    assert result["error"] == "HA_BAD_RESPONSE"


# ---------------------------------------------------------------------------
# ha.call_service
# ---------------------------------------------------------------------------


async def test_call_service_missing_domain() -> None:
    result = await handle_ha_call_service({"service": "turn_on"})
    assert result["error"] == "MISSING_PARAM"


async def test_call_service_missing_service() -> None:
    result = await handle_ha_call_service({"domain": "light"})
    assert result["error"] == "MISSING_PARAM"


async def test_call_service_invalid_target_type() -> None:
    result = await handle_ha_call_service(
        {"domain": "light", "service": "turn_on", "target": "sensor.x"}
    )
    assert result["error"] == "INVALID_PARAM"


async def test_call_service_rejects_unknown_target_param_before_ha() -> None:
    with patch("openclaw_node.commands.ha.ha_post", new_callable=AsyncMock) as post:
        result = await handle_ha_call_service(
            {
                "domain": "light",
                "service": "turn_on",
                "target": {"entity_id": "light.kitchen", "unexpected": "bypass"},
            }
        )
    assert result["error"] == "INVALID_PARAM"
    post.assert_not_awaited()


async def test_call_service_invalid_data_type() -> None:
    result = await handle_ha_call_service(
        {"domain": "light", "service": "turn_on", "data": ["nope"]}
    )
    assert result["error"] == "INVALID_PARAM"


async def test_call_service_success_returns_changed_states() -> None:
    changed = [{"entity_id": "light.x", "state": "on"}]
    with patch("openclaw_node.commands.ha.ha_post", return_value=changed):
        result = await handle_ha_call_service(
            {"domain": "light", "service": "turn_on", "target": {"entity_id": "light.x"}}
        )
    assert result["ok"] is True
    assert result["changed_states"] == changed


async def test_call_service_merges_target_and_data() -> None:
    captured: list[dict[str, Any] | None] = []

    async def _fake_post(path: str, body: Any = None) -> Any:
        captured.append(body)
        return []

    with patch("openclaw_node.commands.ha.ha_post", side_effect=_fake_post):
        await handle_ha_call_service(
            {
                "domain": "light",
                "service": "turn_on",
                "target": {"entity_id": "light.x"},
                "data": {"brightness": 200},
            }
        )
    body = captured[0]
    assert body is not None
    assert body["entity_id"] == "light.x"
    assert body["brightness"] == 200


async def test_call_service_with_no_body_passes_none() -> None:
    captured: list[Any] = []

    async def _fake_post(path: str, body: Any = None) -> Any:
        captured.append(body)
        return []

    with patch("openclaw_node.commands.ha.ha_post", side_effect=_fake_post):
        await handle_ha_call_service({"domain": "light", "service": "turn_on"})
    assert captured[0] is None


async def test_call_service_ha_error_returns_wire_error() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_post",
        side_effect=HAClientError("HA_NETWORK", "boom"),
    ):
        result = await handle_ha_call_service({"domain": "light", "service": "turn_on"})
    assert result["error"] == "HA_NETWORK"


async def test_call_service_non_list_result_handled() -> None:
    with patch("openclaw_node.commands.ha.ha_post", return_value={"ok": 1}):
        result = await handle_ha_call_service({"domain": "light", "service": "turn_on"})
    assert result["ok"] is False
    assert result["error"] == "HA_BAD_RESPONSE"


@pytest.mark.parametrize(
    "payload",
    [
        {"service_data": {"brightness": 50}},
        {"data": {"brightness": 50}, "service_data": {"brightness": 50}},
        {
            "data": {"amount": 1, "transition": -0.0},
            "service_data": {"transition": 0, "amount": 1.0},
        },
        {
            "data": {"nested": [True, 1, {"a": "b"}]},
            "service_data": {"nested": [True, 1.0, {"a": "b"}]},
        },
    ],
)
async def test_call_service_compatibility_payload(payload: dict[str, Any]) -> None:
    with patch(
        "openclaw_node.commands.ha.ha_post", new_callable=AsyncMock, return_value=[]
    ) as post:
        result = await handle_ha_call_service({"domain": "light", "service": "turn_on", **payload})
    assert result["ok"] is True
    post.assert_awaited_once_with("/api/services/light/turn_on", payload["service_data"])


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"brightness": 50}, "service_data": {"brightness": 100}},
        {"data": {}, "service_data": []},
        {"data": None},
        {"service_data": None},
        {"service_data": "not an object"},
        {"data": {"nested": [False]}, "service_data": {"nested": [0]}},
        {"data": {"nested": [1]}, "service_data": {"nested": [1, 2]}},
        {"data": {"a": 1}, "service_data": {"b": 1}},
    ],
)
async def test_call_service_rejects_invalid_alias_before_ha(payload: dict[str, Any]) -> None:
    with patch("openclaw_node.commands.ha.ha_post", new_callable=AsyncMock) as post:
        result = await handle_ha_call_service({"domain": "light", "service": "turn_on", **payload})
    assert result["error"] == "INVALID_PARAM"
    post.assert_not_awaited()


@pytest.mark.parametrize(
    ("domain", "service"),
    [
        ("homeassistant", "restart"),
        ("homeassistant", "stop"),
        ("homeassistant", "reload_core_config"),
        ("automation", "reload"),
        ("hassio", "addon_restart"),
        ("hassio", "addon_start"),
        ("hassio", "addon_stop"),
        ("hassio", "addon_update"),
        ("hassio", "app_restart"),
        ("hassio", "app_start"),
        ("hassio", "app_stop"),
        ("hassio", "app_update"),
        ("hassio", "addon_stdin"),
        ("hassio", "app_stdin"),
        ("hassio", "host_reboot"),
        ("hassio", "host_shutdown"),
        ("hassio", "host_update"),
        ("hassio", "supervisor_update"),
        ("hassio", "mount_reload"),
        (" hassio ", " host_reboot "),
        ("update", "install"),
        ("shell_command", "run_backup"),
        ("python_script", "maintenance"),
        ("command_line", "restart_service"),
    ],
)
async def test_call_service_denies_privileged_effects_before_ha(domain: str, service: str) -> None:
    with patch("openclaw_node.commands.ha.ha_post", new_callable=AsyncMock) as post:
        result = await handle_ha_call_service({"domain": domain, "service": service})
    assert result["error"] == "SERVICE_DENIED"
    post.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {"domain": "light", "service": "turn_on", "unexpected": True},
        {"domain": "light/restart", "service": "turn_on"},
        {"domain": "light", "service": "turn_on?domain=homeassistant"},
        {"domain": True, "service": "turn_on"},
        {"domain": "light", "service": 1},
        {"domain": "light", "service": "x" * 65},
    ],
)
async def test_call_service_rejects_noncanonical_or_unknown_params_before_ha(
    payload: dict[str, Any],
) -> None:
    with patch("openclaw_node.commands.ha.ha_post", new_callable=AsyncMock) as post:
        result = await handle_ha_call_service(payload)
    assert result["error"] == "INVALID_PARAM"
    post.assert_not_awaited()


async def test_call_service_preserves_ordinary_light_action() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_post", new_callable=AsyncMock, return_value=[]
    ) as post:
        result = await handle_ha_call_service(
            {
                "domain": " light ",
                "service": " turn_on ",
                "target": {"entity_id": "light.kitchen"},
            }
        )
    assert result == {"ok": True, "changed_states": []}
    post.assert_awaited_once_with("/api/services/light/turn_on", {"entity_id": "light.kitchen"})


# ---------------------------------------------------------------------------
# ha.list_areas
# ---------------------------------------------------------------------------


async def test_list_areas_returns_areas() -> None:
    areas = [{"area_id": "kitchen", "name": "Kitchen"}]
    with patch("openclaw_node.commands.ha.ha_ws_call", return_value=areas):
        result = await handle_ha_list_areas({})
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["areas"] == areas


async def test_list_areas_ha_error() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_ws_call",
        side_effect=HAClientError("HA_AUTH", "auth fail"),
    ):
        result = await handle_ha_list_areas({})
    assert result["ok"] is False
    assert result["error"] == "HA_AUTH"


async def test_list_areas_bad_response_shape() -> None:
    with patch("openclaw_node.commands.ha.ha_ws_call", return_value={"not": "a list"}):
        result = await handle_ha_list_areas({})
    assert result["error"] == "HA_BAD_RESPONSE"


# ---------------------------------------------------------------------------
# ha.list_devices
# ---------------------------------------------------------------------------


async def test_list_devices_returns_devices() -> None:
    devices = [{"id": "abc123", "name": "Kitchen Light"}]
    with patch("openclaw_node.commands.ha.ha_ws_call", return_value=devices):
        result = await handle_ha_list_devices({})
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["devices"] == devices


async def test_list_devices_ha_error() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_ws_call",
        side_effect=HAClientError("HA_NETWORK", "conn fail"),
    ):
        result = await handle_ha_list_devices({})
    assert result["error"] == "HA_NETWORK"


async def test_list_devices_bad_response_shape() -> None:
    with patch("openclaw_node.commands.ha.ha_ws_call", return_value=None):
        result = await handle_ha_list_devices({})
    assert result["error"] == "HA_BAD_RESPONSE"


# ---------------------------------------------------------------------------
# ha.list_services
# ---------------------------------------------------------------------------


async def test_list_services_returns_services() -> None:
    services = [{"domain": "light", "services": {"turn_on": {}, "turn_off": {}}}]
    with patch("openclaw_node.commands.ha.ha_get", return_value=services):
        result = await handle_ha_list_services({})
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["services"] == services


async def test_list_services_ha_error() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_get",
        side_effect=HAClientError("HA_HTTP_ERROR", "500"),
    ):
        result = await handle_ha_list_services({})
    assert result["error"] == "HA_HTTP_ERROR"


async def test_list_services_bad_response_shape() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value={"not": "a list"}):
        result = await handle_ha_list_services({})
    assert result["error"] == "HA_BAD_RESPONSE"


# ---------------------------------------------------------------------------
# HA operational read helpers
# ---------------------------------------------------------------------------


async def test_get_config_returns_config() -> None:
    config = {"version": "2026.6.0", "location_name": "Home"}
    with patch("openclaw_node.commands.ha.ha_get", return_value=config) as mock_get:
        result = await handle_ha_get_config({})
    assert result["ok"] is True
    assert result["config"] == config
    mock_get.assert_called_once_with("/api/config")


async def test_get_config_bad_response_shape() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value=[]):
        result = await handle_ha_get_config({})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_get_config_ha_error() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_get",
        side_effect=HAClientError("HA_AUTH", "401"),
    ):
        result = await handle_ha_get_config({})
    assert result["error"] == "HA_AUTH"


async def test_list_events_returns_events() -> None:
    events = [{"event": "state_changed", "listener_count": 50}]
    with patch("openclaw_node.commands.ha.ha_get", return_value=events) as mock_get:
        result = await handle_ha_list_events({})
    assert result["ok"] is True
    assert result["events"] == events
    mock_get.assert_called_once_with("/api/events")


async def test_list_events_ha_error() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_get",
        side_effect=HAClientError("HA_HTTP_ERROR", "500"),
    ):
        result = await handle_ha_list_events({})
    assert result["error"] == "HA_HTTP_ERROR"


async def test_list_events_bad_response_shape() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value={}):
        result = await handle_ha_list_events({})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_list_config_entries_returns_entries() -> None:
    entries = [{"domain": "mqtt", "state": "loaded"}]
    with patch("openclaw_node.commands.ha.ha_get", return_value=entries) as mock_get:
        result = await handle_ha_list_config_entries({})
    assert result["ok"] is True
    assert result["entries"] == entries
    mock_get.assert_called_once_with("/api/config/config_entries/entry")


async def test_list_config_entries_bad_response_shape() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value={"not": "a list"}):
        result = await handle_ha_list_config_entries({})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_list_config_entries_ha_error() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_get",
        side_effect=HAClientError("HA_NETWORK", "boom"),
    ):
        result = await handle_ha_list_config_entries({})
    assert result["error"] == "HA_NETWORK"


async def test_core_logs_returns_tail() -> None:
    body = "\n".join(f"line {i}" for i in range(10))
    with patch("openclaw_node.commands.ha.supervisor_get_text", return_value=body) as mock_get:
        result = await handle_ha_core_logs({"lines": 3})
    assert result["ok"] is True
    assert result["lines"] == 3
    assert result["log"] == "line 7\nline 8\nline 9"
    mock_get.assert_called_once_with("/core/logs")


async def test_core_logs_rejects_bool_lines() -> None:
    result = await handle_ha_core_logs({"lines": True})
    assert result["error"] == "INVALID_PARAM"


async def test_core_logs_invalid_lines() -> None:
    result = await handle_ha_core_logs({"lines": "many"})
    assert result["error"] == "INVALID_PARAM"


async def test_core_logs_lines_clamped() -> None:
    body = "\n".join(f"line {i}" for i in range(5))
    with patch("openclaw_node.commands.ha.supervisor_get_text", return_value=body):
        result = await handle_ha_core_logs({"lines": 999999})
    assert result["lines"] == 5


async def test_core_logs_supervisor_error() -> None:
    with patch(
        "openclaw_node.commands.ha.supervisor_get_text",
        side_effect=HAClientError("SUPERVISOR_UNAVAILABLE", "missing"),
    ):
        result = await handle_ha_core_logs({})
    assert result["error"] == "SUPERVISOR_UNAVAILABLE"


async def test_calendar_get_events_requires_entity_id() -> None:
    result = await handle_ha_calendar_get_events(
        {
            "start_date_time": "2026-06-28T00:00:00Z",
            "end_date_time": "2026-06-29T00:00:00Z",
        }
    )
    assert result["error"] == "INVALID_PARAM"


async def test_calendar_get_events_requires_start_and_end() -> None:
    result = await handle_ha_calendar_get_events({"entity_id": "calendar.work"})
    assert result["error"] == "MISSING_PARAM"


async def test_calendar_get_events_requires_end() -> None:
    result = await handle_ha_calendar_get_events(
        {
            "entity_id": "calendar.work",
            "start_date_time": "2026-06-28T00:00:00Z",
        }
    )
    assert result["error"] == "MISSING_PARAM"


async def test_calendar_get_events_returns_response() -> None:
    response: dict[str, Any] = {"service_response": {"calendar.work": {"events": []}}}
    with patch("openclaw_node.commands.ha.ha_post", return_value=response) as mock_post:
        result = await handle_ha_calendar_get_events(
            {
                "entity_id": "calendar.work",
                "start_date_time": "2026-06-28T00:00:00Z",
                "end_date_time": "2026-06-29T00:00:00Z",
            }
        )
    assert result["ok"] is True
    assert result["response"] == response
    mock_post.assert_called_once_with(
        "/api/services/calendar/get_events?return_response",
        {
            "entity_id": "calendar.work",
            "start_date_time": "2026-06-28T00:00:00Z",
            "end_date_time": "2026-06-29T00:00:00Z",
        },
    )


async def test_calendar_get_events_accepts_entity_list() -> None:
    with patch("openclaw_node.commands.ha.ha_post", return_value={}) as mock_post:
        result = await handle_ha_calendar_get_events(
            {
                "entity_id": ["calendar.work", "calendar.family"],
                "start_date_time": "2026-06-28T00:00:00Z",
                "end_date_time": "2026-06-29T00:00:00Z",
            }
        )
    assert result["ok"] is True
    assert mock_post.call_args.args[1]["entity_id"] == ["calendar.work", "calendar.family"]


async def test_calendar_get_events_rejects_malformed_entity_list() -> None:
    result = await handle_ha_calendar_get_events(
        {
            "entity_id": ["calendar.work", 1],
            "start_date_time": "2026-06-28T00:00:00Z",
            "end_date_time": "2026-06-29T00:00:00Z",
        }
    )
    assert result["error"] == "INVALID_PARAM"


async def test_calendar_get_events_rejects_empty_entity_list() -> None:
    result = await handle_ha_calendar_get_events(
        {
            "entity_id": [],
            "start_date_time": "2026-06-28T00:00:00Z",
            "end_date_time": "2026-06-29T00:00:00Z",
        }
    )
    assert result["error"] == "INVALID_PARAM"


async def test_calendar_get_events_rejects_empty_entity_string() -> None:
    result = await handle_ha_calendar_get_events(
        {
            "entity_id": "",
            "start_date_time": "2026-06-28T00:00:00Z",
            "end_date_time": "2026-06-29T00:00:00Z",
        }
    )
    assert result["error"] == "INVALID_PARAM"


async def test_calendar_get_events_rejects_null_start_date_time() -> None:
    result = await handle_ha_calendar_get_events(
        {
            "entity_id": "calendar.work",
            "start_date_time": None,
            "end_date_time": "2026-06-29T00:00:00Z",
        }
    )
    assert result["error"] == "MISSING_PARAM"


async def test_calendar_get_events_rejects_null_end_date_time() -> None:
    result = await handle_ha_calendar_get_events(
        {
            "entity_id": "calendar.work",
            "start_date_time": "2026-06-28T00:00:00Z",
            "end_date_time": None,
        }
    )
    assert result["error"] == "MISSING_PARAM"


async def test_calendar_get_events_ha_error() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_post",
        side_effect=HAClientError("HA_HTTP_ERROR", "500"),
    ):
        result = await handle_ha_calendar_get_events(
            {
                "entity_id": "calendar.work",
                "start_date_time": "2026-06-28T00:00:00Z",
                "end_date_time": "2026-06-29T00:00:00Z",
            }
        )
    assert result["error"] == "HA_HTTP_ERROR"


async def test_calendar_get_events_bad_response_shape() -> None:
    with patch("openclaw_node.commands.ha.ha_post", return_value=[]):
        result = await handle_ha_calendar_get_events(
            {
                "entity_id": "calendar.work",
                "start_date_time": "2026-06-28T00:00:00Z",
                "end_date_time": "2026-06-29T00:00:00Z",
            }
        )
    assert result["error"] == "HA_BAD_RESPONSE"


# ---------------------------------------------------------------------------
# ha.list_entity_registry
# ---------------------------------------------------------------------------


async def test_list_entity_registry_returns_entities() -> None:
    entities = [{"entity_id": "light.kitchen", "platform": "hue"}]
    with patch("openclaw_node.commands.ha.ha_ws_call", return_value=entities):
        result = await handle_ha_list_entity_registry({})
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["entities"] == entities


async def test_list_entity_registry_ha_error() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_ws_call",
        side_effect=HAClientError("HA_WS_ERROR", "ws fail"),
    ):
        result = await handle_ha_list_entity_registry({})
    assert result["error"] == "HA_WS_ERROR"


async def test_list_entity_registry_bad_response_shape() -> None:
    with patch("openclaw_node.commands.ha.ha_ws_call", return_value="not a list"):
        result = await handle_ha_list_entity_registry({})
    assert result["error"] == "HA_BAD_RESPONSE"


# ---------------------------------------------------------------------------
# ha.logbook
# ---------------------------------------------------------------------------


async def test_logbook_returns_entries() -> None:
    entries = [{"when": "2026-06-06T00:00:00", "name": "Kitchen Light", "message": "turned on"}]
    with patch("openclaw_node.commands.ha.ha_get", return_value=entries) as mock_get:
        result = await handle_ha_logbook({})
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["entries"] == entries
    mock_get.assert_called_once_with("/api/logbook")


async def test_logbook_with_start_time() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value=[]) as mock_get:
        await handle_ha_logbook({"start_time": "2026-06-01T00:00:00"})
    assert "2026-06-01T00:00:00" in mock_get.call_args[0][0]


async def test_logbook_with_entity_and_end_time() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value=[]) as mock_get:
        await handle_ha_logbook({"entity_id": "light.kitchen", "end_time": "2026-06-06T12:00:00"})
    url = mock_get.call_args[0][0]
    assert "entity=light.kitchen" in url
    assert "end_time=2026-06-06T12:00:00" in url


async def test_logbook_ha_error() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_get",
        side_effect=HAClientError("HA_HTTP_ERROR", "500"),
    ):
        result = await handle_ha_logbook({})
    assert result["error"] == "HA_HTTP_ERROR"


async def test_logbook_bad_response_shape() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value={"not": "a list"}):
        result = await handle_ha_logbook({})
    assert result["error"] == "HA_BAD_RESPONSE"


# ---------------------------------------------------------------------------
# ha.history
# ---------------------------------------------------------------------------


async def test_history_returns_history() -> None:
    history = [[{"entity_id": "light.x", "state": "on"}]]
    with patch("openclaw_node.commands.ha.ha_get", return_value=history) as mock_get:
        result = await handle_ha_history({})
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["history"] == history
    mock_get.assert_called_once_with("/api/history/period")


async def test_history_with_start_time() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value=[]) as mock_get:
        await handle_ha_history({"start_time": "2026-06-01T00:00:00"})
    url = mock_get.call_args[0][0]
    assert "2026-06-01T00:00:00" in url


async def test_history_with_end_time() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value=[]) as mock_get:
        await handle_ha_history({"end_time": "2026-06-06T12:00:00"})
    url = mock_get.call_args[0][0]
    assert "end_time=2026-06-06T12:00:00" in url


async def test_history_with_entity_ids() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value=[]) as mock_get:
        await handle_ha_history({"entity_ids": ["light.x", "sensor.y"]})
    url = mock_get.call_args[0][0]
    assert "filter_entity_id=light.x,sensor.y" in url


async def test_history_invalid_entity_ids_type() -> None:
    result = await handle_ha_history({"entity_ids": "light.x"})
    assert result["error"] == "INVALID_PARAM"


async def test_history_invalid_entity_ids_contents() -> None:
    result = await handle_ha_history({"entity_ids": [1, 2]})
    assert result["error"] == "INVALID_PARAM"


# ---------------------------------------------------------------------------
# URL percent-encoding for ha.history / ha.logbook / ha.get_state
#
# Reproduced live against the installed node before this fix: an `end_time`
# carrying a `+00:00` offset reached HA as a space and was rejected with HTTP
# 400 "Invalid end_time", so callers had to use the `Z` form. `+` must be
# encoded in a query value and stays literal in a path segment.
# ---------------------------------------------------------------------------


async def test_history_encodes_plus_offset_in_end_time_query() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value=[]) as mock_get:
        await handle_ha_history({"end_time": "2026-09-12T01:00:00+00:00"})
    url = mock_get.call_args[0][0]
    assert "end_time=2026-09-12T01:00:00%2B00:00" in url
    assert "+00:00" not in url


async def test_history_keeps_plus_offset_literal_in_start_time_path() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value=[]) as mock_get:
        await handle_ha_history({"start_time": "2026-09-12T00:00:00+00:00"})
    assert mock_get.call_args[0][0] == "/api/history/period/2026-09-12T00:00:00+00:00"


async def test_history_start_time_cannot_escape_its_path_segment() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value=[]) as mock_get:
        await handle_ha_history({"start_time": "../../states?x=1"})
    assert mock_get.call_args[0][0] == "/api/history/period/..%2F..%2Fstates%3Fx%3D1"


async def test_history_entity_ids_cannot_inject_query_parameters() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value=[]) as mock_get:
        await handle_ha_history({"entity_ids": ["light.x&minimal_response"]})
    url = mock_get.call_args[0][0]
    assert "filter_entity_id=light.x%26minimal_response" in url
    assert "&minimal_response" not in url


async def test_logbook_encodes_plus_offset_in_end_time_query() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value=[]) as mock_get:
        await handle_ha_logbook({"end_time": "2026-09-12T01:00:00+00:00"})
    url = mock_get.call_args[0][0]
    assert "end_time=2026-09-12T01:00:00%2B00:00" in url
    assert "+00:00" not in url


async def test_logbook_entity_cannot_inject_query_parameters() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value=[]) as mock_get:
        await handle_ha_logbook({"entity_id": "light.x&end_time=bogus"})
    url = mock_get.call_args[0][0]
    assert "entity=light.x%26end_time%3Dbogus" in url
    assert "end_time=bogus" not in url


async def test_get_state_encodes_path_traversal_in_entity_id() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value={"state": "on"}) as mock_get:
        await handle_ha_get_state({"entity_id": "../config"})
    assert mock_get.call_args[0][0] == "/api/states/..%2Fconfig"


async def test_get_state_leaves_ordinary_entity_id_readable() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value={"state": "on"}) as mock_get:
        await handle_ha_get_state({"entity_id": "sensor.kitchen_temperature"})
    assert mock_get.call_args[0][0] == "/api/states/sensor.kitchen_temperature"


# A path segment of exactly "." or ".." is a relative reference. yarl normalizes
# dot segments before the request is sent, so "/api/states/.." would be sent as
# "/api/" — a different endpoint. Percent-encoding does not save it: "%2E%2E" is
# normalized too. Verified against the locked yarl: both the bare and the
# percent-encoded forms collapse. So these values are rejected outright, and the
# earlier traversal tests do not cover this because "../config" keeps the dots
# inside a larger segment where no normalization applies.


@pytest.mark.parametrize("value", [".", ".."])
async def test_get_state_rejects_bare_dot_segment(value: str) -> None:
    with patch("openclaw_node.commands.ha.ha_get") as mock_get:
        result = await handle_ha_get_state({"entity_id": value})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"
    mock_get.assert_not_called()


@pytest.mark.parametrize("value", [".", ".."])
async def test_history_rejects_bare_dot_segment_start_time(value: str) -> None:
    with patch("openclaw_node.commands.ha.ha_get") as mock_get:
        result = await handle_ha_history({"start_time": value})
    assert result["error"] == "INVALID_PARAM"
    mock_get.assert_not_called()


@pytest.mark.parametrize("value", [".", ".."])
async def test_logbook_rejects_bare_dot_segment_start_time(value: str) -> None:
    with patch("openclaw_node.commands.ha.ha_get") as mock_get:
        result = await handle_ha_logbook({"start_time": value})
    assert result["error"] == "INVALID_PARAM"
    mock_get.assert_not_called()


async def test_dotted_entity_id_that_is_not_a_dot_segment_is_still_allowed() -> None:
    """Only a bare dot segment is unsafe; ordinary dots must keep working."""
    with patch("openclaw_node.commands.ha.ha_get", return_value={"state": "on"}) as mock_get:
        await handle_ha_get_state({"entity_id": "sensor.a.b"})
    assert mock_get.call_args[0][0] == "/api/states/sensor.a.b"


async def test_triple_dot_entity_id_is_not_normalized_and_is_allowed() -> None:
    """'...' is not a dot segment under RFC 3986, so it must not be rejected."""
    with patch("openclaw_node.commands.ha.ha_get", return_value={"state": "on"}) as mock_get:
        await handle_ha_get_state({"entity_id": "..."})
    assert mock_get.call_args[0][0] == "/api/states/..."


def test_url_stack_really_normalizes_dot_segments() -> None:
    """Pin the client behavior the dot-segment rejection exists to defend against.

    If a future URL library stops collapsing dot segments this test fails, which
    is the signal to re-examine the rejection rather than discover the hazard
    again from a live endpoint-confusion bug.
    """
    import yarl

    assert yarl.URL("http://ha.invalid/api/states/..").path == "/api/"
    assert yarl.URL("http://ha.invalid/api/states/.").path == "/api/states/"
    # Percent-encoding is not an escape hatch here.
    assert yarl.URL("http://ha.invalid/api/states/%2E%2E").path == "/api/"
    # A dot inside a larger segment is untouched, which is why "../config"
    # encoded as "..%2Fconfig" is safe and needs no rejection.
    assert yarl.URL("http://ha.invalid/api/states/..%2Fconfig").path == "/api/states/../config"
    assert yarl.URL("http://ha.invalid/api/states/...").path == "/api/states/..."


async def test_history_with_flags() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value=[]) as mock_get:
        await handle_ha_history({"minimal_response": True, "no_attributes": True})
    url = mock_get.call_args[0][0]
    assert "minimal_response" in url
    assert "no_attributes" in url


async def test_history_ha_error() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_get",
        side_effect=HAClientError("HA_NETWORK", "conn fail"),
    ):
        result = await handle_ha_history({})
    assert result["error"] == "HA_NETWORK"


async def test_history_bad_response_shape() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value="nope"):
        result = await handle_ha_history({})
    assert result["error"] == "HA_BAD_RESPONSE"


# ---------------------------------------------------------------------------
# ha.reload_config
# ---------------------------------------------------------------------------


async def test_reload_config_no_env_token_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_ADMIN_TOKEN", raising=False)
    result = await handle_ha_reload_config({})
    assert result["error"] == "PERMISSION_DENIED"


async def test_reload_config_wrong_token_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    result = await handle_ha_reload_config({"admin_token": "wrong"})
    assert result["error"] == "PERMISSION_DENIED"


async def test_reload_config_correct_token_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    with patch("openclaw_node.commands.ha.ha_post", return_value=None):
        result = await handle_ha_reload_config({"admin_token": "secret"})
    assert result["ok"] is True


async def test_reload_config_ha_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    with patch(
        "openclaw_node.commands.ha.ha_post",
        side_effect=HAClientError("HA_HTTP_ERROR", "500"),
    ):
        result = await handle_ha_reload_config({"admin_token": "secret"})
    assert result["error"] == "HA_HTTP_ERROR"


async def test_reload_config_missing_token_param_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    result = await handle_ha_reload_config({})
    assert result["error"] == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# ha.reload_config domain semantics
#
# `domain` used to be accepted and silently ignored, so asking to reload
# `automation` performed a core-config reload and reported success. Per-domain
# reload is a distinct effect per domain and needs the Phase 2 effect policy,
# so an unsupported domain now fails closed instead.
# ---------------------------------------------------------------------------


async def test_reload_config_omitted_domain_reloads_core(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    with patch("openclaw_node.commands.ha.ha_post", return_value=None) as mock_post:
        result = await handle_ha_reload_config({"admin_token": "secret"})
    assert result == {"ok": True, "domain": "core"}
    mock_post.assert_called_once_with("/api/services/homeassistant/reload_core_config")


async def test_reload_config_explicit_core_domain_reloads_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    with patch("openclaw_node.commands.ha.ha_post", return_value=None) as mock_post:
        result = await handle_ha_reload_config({"admin_token": "secret", "domain": "core"})
    assert result == {"ok": True, "domain": "core"}
    mock_post.assert_called_once_with("/api/services/homeassistant/reload_core_config")


async def test_reload_config_rejects_per_domain_reload_without_calling_ha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    with patch("openclaw_node.commands.ha.ha_post") as mock_post:
        result = await handle_ha_reload_config({"admin_token": "secret", "domain": "automation"})
    assert result["ok"] is False
    assert result["error"] == "UNSUPPORTED"
    assert "automation" in result["message"]
    mock_post.assert_not_called()


async def test_reload_config_rejects_template_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    with patch("openclaw_node.commands.ha.ha_post") as mock_post:
        result = await handle_ha_reload_config({"admin_token": "secret", "domain": "template"})
    assert result["error"] == "UNSUPPORTED"
    mock_post.assert_not_called()


async def test_reload_config_rejects_non_string_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    with patch("openclaw_node.commands.ha.ha_post") as mock_post:
        result = await handle_ha_reload_config({"admin_token": "secret", "domain": 7})
    assert result["error"] == "INVALID_PARAM"
    mock_post.assert_not_called()


async def test_reload_config_blank_domain_is_treated_as_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    with patch("openclaw_node.commands.ha.ha_post", return_value=None) as mock_post:
        result = await handle_ha_reload_config({"admin_token": "secret", "domain": "  "})
    assert result == {"ok": True, "domain": "core"}
    mock_post.assert_called_once_with("/api/services/homeassistant/reload_core_config")


async def test_reload_config_domain_rejected_after_auth_not_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On the direct node path, an unauthorized caller must not learn the domains.

    Scoped deliberately to direct invocation. On the Assist path the TypeBox
    `core` literal is an executable constraint that rejects other values before
    the tool runs, so that boundary refuses earlier and for a different reason.
    This test pins the handler's own ordering: admin gate first, domain second.
    """
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    with patch("openclaw_node.commands.ha.ha_post") as mock_post:
        result = await handle_ha_reload_config({"admin_token": "wrong", "domain": "automation"})
    assert result["error"] == "PERMISSION_DENIED"
    mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# ha.light_turn_on
# ---------------------------------------------------------------------------


async def test_light_turn_on_missing_target() -> None:
    result = await handle_ha_light_turn_on({})
    assert result["error"] == "MISSING_PARAM"


async def test_light_turn_on_entity_id() -> None:
    changed = [{"entity_id": "light.kitchen", "state": "on"}]
    with patch("openclaw_node.commands.ha.ha_post", return_value=changed) as mock_post:
        result = await handle_ha_light_turn_on({"entity_id": "light.kitchen"})
    assert result["ok"] is True
    assert result["changed_states"] == changed
    body = mock_post.call_args[0][1]
    assert body["entity_id"] == "light.kitchen"


async def test_light_turn_on_with_brightness() -> None:
    with patch("openclaw_node.commands.ha.ha_post", return_value=[]) as mock_post:
        await handle_ha_light_turn_on({"entity_id": "light.x", "brightness": 200})
    body = mock_post.call_args[0][1]
    assert body["brightness"] == 200


async def test_light_turn_on_with_rgb_color() -> None:
    with patch("openclaw_node.commands.ha.ha_post", return_value=[]) as mock_post:
        await handle_ha_light_turn_on({"entity_id": "light.x", "rgb_color": [255, 0, 0]})
    body = mock_post.call_args[0][1]
    assert body["rgb_color"] == [255, 0, 0]


async def test_light_turn_on_with_area_id() -> None:
    with patch("openclaw_node.commands.ha.ha_post", return_value=[]) as mock_post:
        await handle_ha_light_turn_on({"area_id": "living_room"})
    body = mock_post.call_args[0][1]
    assert body["area_id"] == "living_room"


async def test_light_turn_on_invalid_entity_id_type() -> None:
    result = await handle_ha_light_turn_on({"entity_id": 123})
    assert result["error"] == "MISSING_PARAM"


async def test_light_turn_on_ha_error() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_post",
        side_effect=HAClientError("HA_NETWORK", "down"),
    ):
        result = await handle_ha_light_turn_on({"entity_id": "light.x"})
    assert result["error"] == "HA_NETWORK"


async def test_light_turn_on_no_body_when_no_data() -> None:
    with patch("openclaw_node.commands.ha.ha_post", return_value=[]) as mock_post:
        await handle_ha_light_turn_on({"entity_id": "light.x"})
    body = mock_post.call_args[0][1]
    assert body is not None
    assert "entity_id" in body


# ---------------------------------------------------------------------------
# ha.light_turn_off
# ---------------------------------------------------------------------------


async def test_light_turn_off_missing_target() -> None:
    result = await handle_ha_light_turn_off({})
    assert result["error"] == "MISSING_PARAM"


async def test_light_turn_off_entity_id() -> None:
    changed = [{"entity_id": "light.kitchen", "state": "off"}]
    with patch("openclaw_node.commands.ha.ha_post", return_value=changed) as mock_post:
        result = await handle_ha_light_turn_off({"entity_id": "light.kitchen"})
    assert result["ok"] is True
    assert result["changed_states"] == changed
    body = mock_post.call_args[0][1]
    assert body["entity_id"] == "light.kitchen"


async def test_light_turn_off_with_transition() -> None:
    with patch("openclaw_node.commands.ha.ha_post", return_value=[]) as mock_post:
        await handle_ha_light_turn_off({"entity_id": "light.x", "transition": 2.0})
    body = mock_post.call_args[0][1]
    assert body["transition"] == 2.0


async def test_light_turn_off_ha_error() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_post",
        side_effect=HAClientError("HA_AUTH", "auth fail"),
    ):
        result = await handle_ha_light_turn_off({"entity_id": "light.x"})
    assert result["error"] == "HA_AUTH"


async def test_light_turn_off_non_list_result() -> None:
    with patch("openclaw_node.commands.ha.ha_post", return_value={}):
        result = await handle_ha_light_turn_off({"entity_id": "light.x"})
    assert result["ok"] is True
    assert result["changed_states"] == []


# ---------------------------------------------------------------------------
# ha.list_automations
# ---------------------------------------------------------------------------


async def test_list_automations_filters_states() -> None:
    states = [
        {"entity_id": "automation.morning", "state": "on", "attributes": {"id": "auto1"}},
        {"entity_id": "light.kitchen", "state": "on"},
        {"entity_id": "automation.night", "state": "off", "attributes": {"id": "auto2"}},
    ]
    with patch("openclaw_node.commands.ha.ha_get", return_value=states):
        result = await handle_ha_list_automations({})
    assert result["ok"] is True
    assert result["count"] == 2
    assert all(a["entity_id"].startswith("automation.") for a in result["automations"])


async def test_list_automations_includes_traces_when_requested() -> None:
    states = [
        {"entity_id": "automation.morning", "state": "on", "attributes": {"id": "auto1"}},
    ]
    traces = [{"run_id": "abc", "timestamp": "2026-06-06T00:00:00"}]
    with (
        patch("openclaw_node.commands.ha.ha_get", return_value=states),
        patch("openclaw_node.commands.ha.ha_ws_call", return_value=traces),
    ):
        result = await handle_ha_list_automations({"include_traces": True})
    assert result["automations"][0]["traces"] == traces


async def test_list_automations_handles_trace_failure() -> None:
    states = [
        {"entity_id": "automation.x", "state": "on", "attributes": {"id": "auto1"}},
    ]
    with (
        patch("openclaw_node.commands.ha.ha_get", return_value=states),
        patch(
            "openclaw_node.commands.ha.ha_ws_call",
            side_effect=HAClientError("HA_WS_ERROR", "fail"),
        ),
    ):
        result = await handle_ha_list_automations({"include_traces": True})
    assert result["automations"][0]["traces"] == []


async def test_list_automations_no_id_attribute_skips_trace_fetch() -> None:
    states = [
        {"entity_id": "automation.x", "state": "on", "attributes": {}},
    ]
    with patch("openclaw_node.commands.ha.ha_get", return_value=states):
        result = await handle_ha_list_automations({"include_traces": True})
    assert result["automations"][0]["traces"] == []


async def test_list_automations_ha_error() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_get",
        side_effect=HAClientError("HA_AUTH", "auth fail"),
    ):
        result = await handle_ha_list_automations({})
    assert result["error"] == "HA_AUTH"


async def test_list_automations_bad_response_shape() -> None:
    with patch("openclaw_node.commands.ha.ha_get", return_value={"not": "a list"}):
        result = await handle_ha_list_automations({})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_list_automations_trace_non_list_response() -> None:
    states = [
        {"entity_id": "automation.x", "state": "on", "attributes": {"id": "auto1"}},
    ]
    with (
        patch("openclaw_node.commands.ha.ha_get", return_value=states),
        patch("openclaw_node.commands.ha.ha_ws_call", return_value={"not": "list"}),
    ):
        result = await handle_ha_list_automations({"include_traces": True})
    assert result["automations"][0]["traces"] == []


async def test_list_automations_rejects_unknown_param() -> None:
    result = await handle_ha_list_automations({"bogus": 1})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"
    assert "unknown params" in result["message"]


async def test_list_automations_rejects_non_bool_include_traces() -> None:
    result = await handle_ha_list_automations({"include_traces": "yes"})
    assert result["error"] == "INVALID_PARAM"
    assert "include_traces" in result["message"]


async def test_list_automations_rejects_empty_entity_filter() -> None:
    result = await handle_ha_list_automations({"entity_filter": ""})
    assert result["error"] == "INVALID_PARAM"


async def test_list_automations_rejects_non_string_entity_filter() -> None:
    result = await handle_ha_list_automations({"entity_filter": 5})
    assert result["error"] == "INVALID_PARAM"


async def test_list_automations_rejects_out_of_domain_entity_filter() -> None:
    result = await handle_ha_list_automations({"entity_filter": "light.*"})
    assert result["error"] == "INVALID_PARAM"
    assert "automation." in result["message"]


async def test_list_automations_rejects_oversize_entity_filter() -> None:
    result = await handle_ha_list_automations({"entity_filter": "automation." + "a" * 300})
    assert result["error"] == "INVALID_PARAM"


async def test_list_automations_rejects_empty_state_filter() -> None:
    result = await handle_ha_list_automations({"state_filter": ""})
    assert result["error"] == "INVALID_PARAM"


async def test_list_automations_rejects_oversize_state_filter() -> None:
    result = await handle_ha_list_automations({"state_filter": "x" * 257})
    assert result["error"] == "INVALID_PARAM"
    assert "256" in result["message"]


async def test_list_automations_entity_filter_exact_match() -> None:
    states = [
        {"entity_id": "automation.morning", "state": "on", "attributes": {"id": "m"}},
        {"entity_id": "automation.night", "state": "off", "attributes": {"id": "n"}},
    ]
    with patch("openclaw_node.commands.ha.ha_get", return_value=states):
        result = await handle_ha_list_automations({"entity_filter": "automation.morning"})
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["automations"][0]["entity_id"] == "automation.morning"


async def test_list_automations_entity_filter_glob() -> None:
    states = [
        {"entity_id": "automation.morning_lights", "state": "on", "attributes": {}},
        {"entity_id": "automation.morning_music", "state": "on", "attributes": {}},
        {"entity_id": "automation.night", "state": "off", "attributes": {}},
    ]
    with patch("openclaw_node.commands.ha.ha_get", return_value=states):
        result = await handle_ha_list_automations({"entity_filter": "automation.morning_*"})
    assert result["count"] == 2
    assert {a["entity_id"] for a in result["automations"]} == {
        "automation.morning_lights",
        "automation.morning_music",
    }


async def test_list_automations_entity_filter_no_match_returns_empty() -> None:
    states = [
        {"entity_id": "automation.morning", "state": "on", "attributes": {}},
    ]
    with patch("openclaw_node.commands.ha.ha_get", return_value=states):
        result = await handle_ha_list_automations(
            {"entity_filter": "automation.__openclaw_audit_no_match__"}
        )
    assert result["ok"] is True
    assert result["count"] == 0
    assert result["automations"] == []


async def test_list_automations_state_filter_narrows() -> None:
    states = [
        {"entity_id": "automation.a", "state": "on", "attributes": {}},
        {"entity_id": "automation.b", "state": "off", "attributes": {}},
        {"entity_id": "automation.c", "state": "on", "attributes": {}},
    ]
    with patch("openclaw_node.commands.ha.ha_get", return_value=states):
        result = await handle_ha_list_automations({"state_filter": "on"})
    assert result["count"] == 2
    assert all(a["state"] == "on" for a in result["automations"])


async def test_list_automations_entity_filter_applied_before_traces() -> None:
    states = [
        {"entity_id": "automation.match", "state": "on", "attributes": {"id": "m"}},
        {"entity_id": "automation.skip", "state": "on", "attributes": {"id": "s"}},
    ]
    calls: list[str] = []

    async def fake_ws(cmd: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(str(args.get("item_id")))
        return [{"run_id": "r"}]

    with (
        patch("openclaw_node.commands.ha.ha_get", return_value=states),
        patch("openclaw_node.commands.ha.ha_ws_call", side_effect=fake_ws),
    ):
        result = await handle_ha_list_automations(
            {"entity_filter": "automation.match", "include_traces": True}
        )
    assert result["count"] == 1
    assert calls == ["m"], "trace lookup must run only for filtered automations"


# ---------------------------------------------------------------------------
# ha.check_config
# ---------------------------------------------------------------------------


async def test_check_config_valid() -> None:
    body = {"result": "valid", "errors": None, "warnings": None}
    with patch("openclaw_node.commands.ha.ha_post", return_value=body):
        result = await handle_ha_check_config({})
    assert result["ok"] is True
    assert result["result"] == "valid"
    assert result["errors"] is None


async def test_check_config_invalid_with_errors() -> None:
    body = {"result": "invalid", "errors": "yaml line 5: bad", "warnings": "deprecation"}
    with patch("openclaw_node.commands.ha.ha_post", return_value=body):
        result = await handle_ha_check_config({})
    assert result["result"] == "invalid"
    assert "bad" in str(result["errors"])
    assert "deprecation" in str(result["warnings"])


async def test_check_config_ha_error() -> None:
    with patch(
        "openclaw_node.commands.ha.ha_post",
        side_effect=HAClientError("HA_HTTP_ERROR", "500"),
    ):
        result = await handle_ha_check_config({})
    assert result["error"] == "HA_HTTP_ERROR"


async def test_check_config_bad_response_shape() -> None:
    with patch("openclaw_node.commands.ha.ha_post", return_value=["not", "a", "dict"]):
        result = await handle_ha_check_config({})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_check_config_missing_result_field_defaults_unknown() -> None:
    with patch("openclaw_node.commands.ha.ha_post", return_value={}):
        result = await handle_ha_check_config({})
    assert result["ok"] is True
    assert result["result"] == "unknown"


# ---------------------------------------------------------------------------
# ha.addon_logs
# ---------------------------------------------------------------------------


async def test_addon_logs_missing_slug() -> None:
    result = await handle_ha_addon_logs({})
    assert result["error"] == "MISSING_PARAM"


@pytest.mark.parametrize("slug", ["bad slug", "../etc", "with/slash", "a" * 200, ""])
async def test_addon_logs_invalid_slug(slug: str) -> None:
    result = await handle_ha_addon_logs({"slug": slug})
    assert result["error"] in ("INVALID_PARAM", "MISSING_PARAM")


async def test_addon_logs_returns_trimmed_tail() -> None:
    body = "\n".join(f"line {i}" for i in range(500))
    with patch(
        "openclaw_node.commands.ha.supervisor_get_text",
        return_value=body,
    ) as fake:
        result = await handle_ha_addon_logs({"slug": "fcccfbbd_openclaw_hass_node", "lines": 50})
    assert result["ok"] is True
    assert result["slug"] == "fcccfbbd_openclaw_hass_node"
    assert result["lines"] == 50
    assert result["log"].splitlines()[0] == "line 450"
    assert result["log"].splitlines()[-1] == "line 499"
    assert fake.call_args.kwargs["max_bytes"] == 1_048_576


async def test_addon_logs_lines_clamped() -> None:
    body = "single line"
    with patch(
        "openclaw_node.commands.ha.supervisor_get_text",
        return_value=body,
    ):
        result = await handle_ha_addon_logs({"slug": "self", "lines": 999999})
    assert result["ok"] is True
    assert result["lines"] == 1


async def test_addon_logs_lines_bad_type() -> None:
    result = await handle_ha_addon_logs({"slug": "self", "lines": "many"})
    assert result["error"] == "INVALID_PARAM"


async def test_addon_logs_supervisor_unavailable() -> None:
    with patch(
        "openclaw_node.commands.ha.supervisor_get_text",
        side_effect=HAClientError("SUPERVISOR_UNAVAILABLE", "no token"),
    ):
        result = await handle_ha_addon_logs({"slug": "self"})
    assert result["error"] == "SUPERVISOR_UNAVAILABLE"


async def test_addon_logs_supervisor_404() -> None:
    with patch(
        "openclaw_node.commands.ha.supervisor_get_text",
        side_effect=HAClientError("HA_NOT_FOUND", "addon not found"),
    ):
        result = await handle_ha_addon_logs({"slug": "does_not_exist"})
    assert result["error"] == "HA_NOT_FOUND"


async def test_addon_logs_suppresses_upstream_html_error() -> None:
    with patch(
        "openclaw_node.commands.ha.supervisor_get_text",
        side_effect=HAClientError(
            "HA_HTTP_ERROR",
            "Supervisor returned 504 (HTML error page suppressed)",
        ),
    ):
        result = await handle_ha_addon_logs({"slug": "self"})
    assert result["error"] == "HA_HTTP_ERROR"
    assert result["message"] == "Supervisor returned 504 (HTML error page suppressed)"
    assert "<html>" not in result["message"]


# ---------------------------------------------------------------------------
# ha.list_addons
# ---------------------------------------------------------------------------


async def test_list_addons_happy_path() -> None:
    payload = {
        "result": "ok",
        "data": {
            "addons": [
                {
                    "slug": "fcccfbbd_openclaw_hass_node",
                    "name": "OpenClaw Node",
                    "state": "started",
                    "version": "2026.6.19b2",
                    "version_latest": "2026.6.19b2",
                    "update_available": False,
                    "repository": "core",
                    "boot": "auto",  # filtered out
                    "options": {"secret": "should not leak"},  # filtered out
                },
                {"slug": "core_mosquitto", "name": "Mosquitto broker", "state": "started"},
            ]
        },
    }
    with patch(
        "openclaw_node.commands.ha.supervisor_get_json",
        return_value=payload,
    ):
        result = await handle_ha_list_addons({})
    assert result["ok"] is True
    assert result["count"] == 2
    first = result["addons"][0]
    assert first["slug"] == "fcccfbbd_openclaw_hass_node"
    assert first["state"] == "started"
    assert "options" not in first
    assert "boot" not in first
    assert "repository" not in first
    second = result["addons"][1]
    assert second["slug"] == "core_mosquitto"
    assert second["version"] is None


async def test_list_addons_filters_non_dict_entries() -> None:
    payload = {"data": {"addons": [{"slug": "a"}, "garbage", None, {"slug": "b"}]}}
    with patch("openclaw_node.commands.ha.supervisor_get_json", return_value=payload):
        result = await handle_ha_list_addons({})
    assert result["ok"] is True
    assert result["count"] == 2


async def test_list_addons_bad_top_level() -> None:
    with patch("openclaw_node.commands.ha.supervisor_get_json", return_value=["nope"]):
        result = await handle_ha_list_addons({})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_list_addons_missing_data() -> None:
    with patch("openclaw_node.commands.ha.supervisor_get_json", return_value={"result": "ok"}):
        result = await handle_ha_list_addons({})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_list_addons_data_addons_not_list() -> None:
    with patch(
        "openclaw_node.commands.ha.supervisor_get_json",
        return_value={"data": {"addons": "oops"}},
    ):
        result = await handle_ha_list_addons({})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_list_addons_supervisor_unavailable() -> None:
    with patch(
        "openclaw_node.commands.ha.supervisor_get_json",
        side_effect=HAClientError("SUPERVISOR_UNAVAILABLE", "no token"),
    ):
        result = await handle_ha_list_addons({})
    assert result["error"] == "SUPERVISOR_UNAVAILABLE"


# ---------------------------------------------------------------------------
# ha.addon_info
# ---------------------------------------------------------------------------


async def test_addon_info_missing_slug() -> None:
    result = await handle_ha_addon_info({})
    assert result["error"] == "MISSING_PARAM"


@pytest.mark.parametrize(
    "slug",
    [
        "bad slug",
        "../etc",
        "with/slash",
        "a" * 200,
        # Slug grammar must reject uppercase and non-ASCII alnum that
        # str.isalnum() would otherwise accept.
        "Self",
        "CORE_MOSQUITTO",
        "addonÿ",
        "addón",
        "ＡＢＣ",  # noqa: RUF001 - fullwidth chars are exactly the attack we reject
        "-leading-dash",
    ],
)
async def test_addon_info_invalid_slug(slug: str) -> None:
    result = await handle_ha_addon_info({"slug": slug})
    assert result["error"] == "INVALID_PARAM"


async def test_addon_info_drops_options_and_schema() -> None:
    """The whole point of this command — `options` and `schema` MUST NOT leak."""
    payload = {
        "data": {
            "slug": "self",
            "name": "OpenClaw Node",
            "state": "started",
            "version": "1.0.0",
            "version_latest": "1.0.0",
            "update_available": False,
            "description": "OpenClaw node addon",
            "repository": "core",
            "boot": "auto",
            "startup": "services",
            "stage": "stable",
            "arch": "amd64",
            "machine": "qemux86-64",
            "ingress": True,
            "ingress_port": 8099,
            # Sensitive fields that MUST be stripped:
            "options": {"hass_token": "super-secret-leak", "mqtt_password": "leak"},
            "schema": [{"name": "mqtt_password", "type": "password"}],
            "hostname": "addon_internal_hostname",
            "ip_address": "172.30.32.42",
            "homeassistant_api": True,
            "hassio_api": True,
            "privileged": ["NET_ADMIN"],
            "audio": True,
        }
    }
    with patch("openclaw_node.commands.ha.supervisor_get_json", return_value=payload):
        result = await handle_ha_addon_info({"slug": "self"})
    assert result["ok"] is True
    info = result["info"]
    # Kept fields:
    assert info["slug"] == "self"
    assert info["name"] == "OpenClaw Node"
    assert info["state"] == "started"
    assert info["ingress_port"] == 8099
    # Dropped fields — CRITICAL invariant of this command:
    for forbidden in (
        "options",
        "schema",
        "hostname",
        "ip_address",
        "homeassistant_api",
        "hassio_api",
        "privileged",
        "audio",
        "repository",
    ):
        assert forbidden not in info, (
            f"{forbidden!r} leaked into ha.addon_info output — see _ADDON_INFO_FIELDS"
        )


async def test_addon_info_missing_source_fields_are_none() -> None:
    payload = {"data": {"slug": "x"}}
    with patch("openclaw_node.commands.ha.supervisor_get_json", return_value=payload):
        result = await handle_ha_addon_info({"slug": "x"})
    assert result["ok"] is True
    assert result["info"]["slug"] == "x"
    assert result["info"]["version"] is None


async def test_addon_info_bad_response_shape() -> None:
    with patch("openclaw_node.commands.ha.supervisor_get_json", return_value=["nope"]):
        result = await handle_ha_addon_info({"slug": "x"})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_addon_info_missing_data() -> None:
    with patch("openclaw_node.commands.ha.supervisor_get_json", return_value={"result": "ok"}):
        result = await handle_ha_addon_info({"slug": "x"})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_addon_info_supervisor_unavailable() -> None:
    with patch(
        "openclaw_node.commands.ha.supervisor_get_json",
        side_effect=HAClientError("SUPERVISOR_UNAVAILABLE", "no token"),
    ):
        result = await handle_ha_addon_info({"slug": "self"})
    assert result["error"] == "SUPERVISOR_UNAVAILABLE"


async def test_addon_info_not_found() -> None:
    with patch(
        "openclaw_node.commands.ha.supervisor_get_json",
        side_effect=HAClientError("HA_NOT_FOUND", "no such"),
    ):
        result = await handle_ha_addon_info({"slug": "missing"})
    assert result["error"] == "HA_NOT_FOUND"


# ---------------------------------------------------------------------------
# ha.addon_stats
# ---------------------------------------------------------------------------


async def test_addon_stats_missing_slug() -> None:
    result = await handle_ha_addon_stats({})
    assert result["error"] == "MISSING_PARAM"


async def test_addon_stats_invalid_slug() -> None:
    result = await handle_ha_addon_stats({"slug": "../etc"})
    assert result["error"] == "INVALID_PARAM"


async def test_addon_stats_happy_path() -> None:
    payload = {
        "data": {
            "cpu_percent": 0.42,
            "memory_usage": 12345678,
            "memory_limit": 256000000,
            "memory_percent": 4.8,
            "network_rx": 100,
            "network_tx": 200,
            "blk_read": 300,
            "blk_write": 400,
            # A future Supervisor field that should NOT pass through:
            "internal_env_dump": {"SECRET": "leak"},
        }
    }
    with patch("openclaw_node.commands.ha.supervisor_get_json", return_value=payload):
        result = await handle_ha_addon_stats({"slug": "self"})
    assert result["ok"] is True
    stats = result["stats"]
    assert stats["cpu_percent"] == 0.42
    assert stats["memory_usage"] == 12345678
    assert "internal_env_dump" not in stats


async def test_addon_stats_bad_response_shape() -> None:
    with patch("openclaw_node.commands.ha.supervisor_get_json", return_value=["nope"]):
        result = await handle_ha_addon_stats({"slug": "x"})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_addon_stats_missing_data() -> None:
    with patch("openclaw_node.commands.ha.supervisor_get_json", return_value={}):
        result = await handle_ha_addon_stats({"slug": "x"})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_addon_stats_supervisor_unavailable() -> None:
    with patch(
        "openclaw_node.commands.ha.supervisor_get_json",
        side_effect=HAClientError("SUPERVISOR_UNAVAILABLE", "no token"),
    ):
        result = await handle_ha_addon_stats({"slug": "self"})
    assert result["error"] == "SUPERVISOR_UNAVAILABLE"


# ---------------------------------------------------------------------------
# ha.addon_changelog
# ---------------------------------------------------------------------------


async def test_addon_changelog_missing_slug() -> None:
    result = await handle_ha_addon_changelog({})
    assert result["error"] == "MISSING_PARAM"


async def test_addon_changelog_invalid_slug() -> None:
    result = await handle_ha_addon_changelog({"slug": "../etc"})
    assert result["error"] == "INVALID_PARAM"


async def test_addon_changelog_returns_body() -> None:
    with patch(
        "openclaw_node.commands.ha.supervisor_get_text",
        return_value="# Changelog\n- thing happened",
    ):
        result = await handle_ha_addon_changelog({"slug": "self"})
    assert result["ok"] is True
    assert result["slug"] == "self"
    assert "thing happened" in result["changelog"]


async def test_addon_changelog_not_found() -> None:
    with patch(
        "openclaw_node.commands.ha.supervisor_get_text",
        side_effect=HAClientError("HA_NOT_FOUND", "no changelog"),
    ):
        result = await handle_ha_addon_changelog({"slug": "self"})
    assert result["error"] == "HA_NOT_FOUND"


# ---------------------------------------------------------------------------
# ha.addon_documentation
# ---------------------------------------------------------------------------


async def test_addon_documentation_missing_slug() -> None:
    result = await handle_ha_addon_documentation({})
    assert result["error"] == "MISSING_PARAM"


async def test_addon_documentation_invalid_slug() -> None:
    result = await handle_ha_addon_documentation({"slug": "with/slash"})
    assert result["error"] == "INVALID_PARAM"


async def test_addon_documentation_returns_body() -> None:
    with patch(
        "openclaw_node.commands.ha.supervisor_get_text",
        return_value="# Docs\nUse like this.",
    ):
        result = await handle_ha_addon_documentation({"slug": "self"})
    assert result["ok"] is True
    assert "Use like this" in result["documentation"]


async def test_addon_documentation_not_found() -> None:
    with patch(
        "openclaw_node.commands.ha.supervisor_get_text",
        side_effect=HAClientError("HA_NOT_FOUND", "no docs"),
    ):
        result = await handle_ha_addon_documentation({"slug": "self"})
    assert result["error"] == "HA_NOT_FOUND"


# ---------------------------------------------------------------------------
# Tier B addon lifecycle
# ---------------------------------------------------------------------------


async def test_addon_start_ignores_admin_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tier B no longer requires OPENCLAW_ADMIN_TOKEN; the pairing-session
    bearer authenticates the request, and the slug allowlist authorizes it.

    With no allowlist configured every slug is rejected by the allowlist check,
    not by an admin gate.
    """
    monkeypatch.delenv("OPENCLAW_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("OPENCLAW_ADDON_LIFECYCLE_ALLOWLIST", raising=False)

    result = await handle_ha_addon_start({"slug": "openclaw_hass_node"})

    assert result["error"] == "PERMISSION_DENIED"
    assert "allowlisted" in result["message"]


async def test_addon_start_requires_allowlisted_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("OPENCLAW_ADDON_LIFECYCLE_ALLOWLIST", raising=False)

    result = await handle_ha_addon_start({"slug": "openclaw_hass_node"})

    assert result["error"] == "PERMISSION_DENIED"
    assert "allowlisted" in result["message"]


async def test_addon_lifecycle_always_denies_core_slugs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENCLAW_ADDON_LIFECYCLE_ALLOWLIST", '["core_mosquitto"]')

    result = await handle_ha_addon_restart({"slug": "core_mosquitto"})

    assert result["error"] == "PERMISSION_DENIED"
    assert "core slug" in result["message"]


async def test_addon_start_idempotent_when_already_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENCLAW_ADDON_LIFECYCLE_ALLOWLIST", '["openclaw_hass_node"]')
    with (
        patch(
            "openclaw_node.commands.ha.supervisor_get_json",
            return_value={"data": {"state": "started"}},
        ) as mock_get,
        patch("openclaw_node.commands.ha.supervisor_post_json") as mock_post,
    ):
        result = await handle_ha_addon_start({"slug": "openclaw_hass_node"})

    assert result == {
        "ok": True,
        "slug": "openclaw_hass_node",
        "state": "started",
        "changed": False,
    }
    mock_get.assert_called_once_with("/addons/openclaw_hass_node/info")
    mock_post.assert_not_called()


async def test_addon_stop_posts_when_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADDON_LIFECYCLE_ALLOWLIST", '["openclaw_hass_node"]')
    with (
        patch(
            "openclaw_node.commands.ha.supervisor_get_json",
            side_effect=[
                {"data": {"state": "started"}},
                {"data": {"state": "stopped"}},
            ],
        ),
        patch("openclaw_node.commands.ha.supervisor_post_json", return_value={}) as mock_post,
    ):
        result = await handle_ha_addon_stop({"slug": "openclaw_hass_node"})

    assert result["ok"] is True
    assert result["changed"] is True
    assert result["state"] == "stopped"
    mock_post.assert_called_once_with("/addons/openclaw_hass_node/stop")


async def test_addon_restart_posts_when_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADDON_LIFECYCLE_ALLOWLIST", "openclaw_hass_node")
    with (
        patch(
            "openclaw_node.commands.ha.supervisor_get_json",
            side_effect=[
                {"data": {"state": "started"}},
                {"data": {"state": "started"}},
            ],
        ),
        patch("openclaw_node.commands.ha.supervisor_post_json", return_value={}) as mock_post,
    ):
        result = await handle_ha_addon_restart({"slug": "openclaw_hass_node"})

    assert result["ok"] is True
    assert result["changed"] is True
    mock_post.assert_called_once_with("/addons/openclaw_hass_node/restart")


# ---------------------------------------------------------------------------
# ha.addon_update (Tier B)
# ---------------------------------------------------------------------------


async def test_addon_update_requires_allowlisted_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("OPENCLAW_ADDON_LIFECYCLE_ALLOWLIST", raising=False)

    result = await handle_ha_addon_update({"slug": "openclaw_hass_node"})

    assert result["error"] == "PERMISSION_DENIED"
    assert "allowlisted" in result["message"]


async def test_addon_update_always_denies_core_slugs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENCLAW_ADDON_LIFECYCLE_ALLOWLIST", '["core_mosquitto"]')

    result = await handle_ha_addon_update({"slug": "core_mosquitto"})

    assert result["error"] == "PERMISSION_DENIED"
    assert "core slug" in result["message"]


async def test_addon_update_posts_when_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADDON_LIFECYCLE_ALLOWLIST", '["openclaw_hass_node"]')
    with (
        patch(
            "openclaw_node.commands.ha.supervisor_get_json",
            side_effect=[
                {"data": {"state": "started"}},
                {"data": {"state": "started"}},
            ],
        ),
        patch("openclaw_node.commands.ha.supervisor_post_json", return_value={}) as mock_post,
    ):
        result = await handle_ha_addon_update({"slug": "openclaw_hass_node"})

    assert result["ok"] is True
    assert result["changed"] is True
    mock_post.assert_called_once_with("/addons/openclaw_hass_node/update")


# ---------------------------------------------------------------------------
# ha.update_install (Tier B admin)
# ---------------------------------------------------------------------------


async def test_update_install_no_env_token_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_ADMIN_TOKEN", raising=False)
    result = await handle_ha_update_install(
        {"entity_id": "update.home_assistant_core_update", "admin_token": "secret"}
    )
    assert result["error"] == "PERMISSION_DENIED"


async def test_update_install_wrong_token_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    result = await handle_ha_update_install({"entity_id": "update.hacs", "admin_token": "wrong"})
    assert result["error"] == "PERMISSION_DENIED"


async def test_update_install_missing_token_param_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    result = await handle_ha_update_install({"entity_id": "update.hacs"})
    assert result["error"] == "PERMISSION_DENIED"


async def test_update_install_missing_entity_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    result = await handle_ha_update_install({"admin_token": "secret"})
    assert result["error"] == "MISSING_PARAM"
    assert "entity_id" in result["message"]


async def test_update_install_rejects_non_update_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    result = await handle_ha_update_install(
        {"entity_id": "sensor.temperature", "admin_token": "secret"}
    )
    assert result["error"] == "INVALID_PARAM"
    assert "update." in result["message"]


async def test_update_install_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    with patch(
        "openclaw_node.commands.ha.ha_post",
        return_value=[{"entity_id": "update.hacs", "state": "off"}],
    ) as mock_post:
        result = await handle_ha_update_install(
            {"entity_id": "update.hacs", "admin_token": "secret"}
        )

    assert result["ok"] is True
    assert result["entity_id"] == "update.hacs"
    assert len(result["changed_states"]) == 1
    mock_post.assert_called_once_with("/api/services/update/install", {"entity_id": "update.hacs"})


async def test_update_install_passes_backup_and_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    with patch("openclaw_node.commands.ha.ha_post", return_value=[]) as mock_post:
        result = await handle_ha_update_install(
            {
                "entity_id": "update.home_assistant_core_update",
                "backup": True,
                "version": "2026.7.0",
                "admin_token": "secret",
            }
        )

    assert result["ok"] is True
    mock_post.assert_called_once_with(
        "/api/services/update/install",
        {
            "entity_id": "update.home_assistant_core_update",
            "backup": True,
            "version": "2026.7.0",
        },
    )


async def test_update_install_invalid_backup_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    result = await handle_ha_update_install(
        {"entity_id": "update.hacs", "backup": "yes", "admin_token": "secret"}
    )
    assert result["error"] == "INVALID_PARAM"
    assert "backup" in result["message"]


async def test_update_install_ha_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "secret")
    with patch(
        "openclaw_node.commands.ha.ha_post",
        side_effect=HAClientError("HA_HTTP_ERROR", "500 Internal Server Error"),
    ):
        result = await handle_ha_update_install(
            {"entity_id": "update.hacs", "admin_token": "secret"}
        )
    assert result["error"] == "HA_HTTP_ERROR"
