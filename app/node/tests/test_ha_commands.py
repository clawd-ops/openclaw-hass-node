"""Tests for openclaw_node.commands.ha."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

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
        await handle_ha_call_service({"domain": "homeassistant", "service": "restart"})
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
    assert result["ok"] is True
    assert result["changed_states"] == []


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
