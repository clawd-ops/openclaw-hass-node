"""Tests for openclaw_node.commands.ha."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from openclaw_node.commands.ha import (
    handle_ha_call_service,
    handle_ha_get_state,
    handle_ha_list_areas,
    handle_ha_list_devices,
    handle_ha_list_entity_registry,
    handle_ha_list_services,
    handle_ha_list_states,
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
