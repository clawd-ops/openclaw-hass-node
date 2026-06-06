"""Tests for openclaw_node.commands.ha."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from openclaw_node.commands.ha import (
    handle_ha_call_service,
    handle_ha_get_state,
    handle_ha_history,
    handle_ha_light_turn_off,
    handle_ha_light_turn_on,
    handle_ha_list_areas,
    handle_ha_list_devices,
    handle_ha_list_entity_registry,
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
