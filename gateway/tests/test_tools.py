"""Sanity tests for openclaw_gateway.tools."""

from __future__ import annotations

from openclaw_gateway.tools import HA_TOOLS


def test_every_tool_has_name_description_schema() -> None:
    """Anthropic requires name, description, input_schema for every tool."""
    for tool in HA_TOOLS:
        assert "name" in tool
        assert "description" in tool
        assert "input_schema" in tool
        assert tool["input_schema"]["type"] == "object"


def test_tool_names_match_node_command_surface() -> None:
    """Every advertised tool must correspond to a node ha.* command."""
    expected = {
        "ha.list_states",
        "ha.get_state",
        "ha.call_service",
        "ha.list_areas",
        "ha.list_devices",
        "ha.list_services",
        "ha.list_entity_registry",
        "ha.list_automations",
        "ha.check_config",
        "ha.logbook",
        "ha.history",
        "ha.light_turn_on",
        "ha.light_turn_off",
    }
    advertised = {t["name"] for t in HA_TOOLS}
    assert advertised == expected


def test_required_params_present_where_node_requires_them() -> None:
    """Tools whose handlers require params advertise them in 'required'."""
    by_name = {t["name"]: t for t in HA_TOOLS}
    assert by_name["ha.get_state"]["input_schema"]["required"] == ["entity_id"]
    assert set(by_name["ha.call_service"]["input_schema"]["required"]) == {"domain", "service"}
