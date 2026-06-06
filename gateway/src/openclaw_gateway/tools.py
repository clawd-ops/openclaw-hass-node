"""Anthropic-shaped tool definitions for the HA control surface.

These advertise the node-side ``ha.*`` commands to the brain so it can
turn user requests like "turn off the kitchen light" into a ``ha.light_turn_off``
tool call.

Keep schemas minimal and stable. The model only needs enough to pick the
right tool and fill required params; per-property docs come from the
``description`` fields.
"""

from __future__ import annotations

from typing import Any, Final

HA_TOOLS: Final[list[dict[str, Any]]] = [
    {
        "name": "ha.list_states",
        "description": (
            "List the current state of every Home Assistant entity, optionally filtered by domain."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Optional HA domain to filter by, e.g. 'light' or 'sensor'.",
                },
            },
        },
    },
    {
        "name": "ha.get_state",
        "description": "Return the current state of a single entity.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Entity ID, e.g. 'light.kitchen'."},
            },
            "required": ["entity_id"],
        },
    },
    {
        "name": "ha.call_service",
        "description": (
            "Call a generic Home Assistant service. "
            "Use for anything not covered by the specialised tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Service domain, e.g. 'media_player'."},
                "service": {"type": "string", "description": "Service name, e.g. 'play_media'."},
                "target": {"type": "object", "description": "Optional service target."},
                "data": {"type": "object", "description": "Optional service data."},
            },
            "required": ["domain", "service"],
        },
    },
    {
        "name": "ha.list_areas",
        "description": "List all Home Assistant areas (rooms/zones).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ha.list_devices",
        "description": "List all Home Assistant devices.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ha.list_services",
        "description": "List all Home Assistant service descriptions, grouped by domain.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ha.list_entity_registry",
        "description": "List all Home Assistant entity-registry entries.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ha.list_automations",
        "description": "List Home Assistant automation entities, with optional recent traces.",
        "input_schema": {
            "type": "object",
            "properties": {
                "include_traces": {
                    "type": "boolean",
                    "description": "Include recent traces per automation.",
                },
            },
        },
    },
    {
        "name": "ha.check_config",
        "description": (
            "Validate Home Assistant configuration.yaml. Always call this before any reload."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ha.logbook",
        "description": "Read Home Assistant logbook entries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_time": {"type": "string", "description": "ISO-8601 start time."},
                "end_time": {"type": "string", "description": "ISO-8601 end time."},
                "entity_id": {"type": "string", "description": "Restrict to one entity."},
            },
        },
    },
    {
        "name": "ha.history",
        "description": "Read state history for one or more Home Assistant entities.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_time": {"type": "string", "description": "ISO-8601 start time."},
                "end_time": {"type": "string", "description": "ISO-8601 end time."},
                "entity_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of entity IDs.",
                },
            },
        },
    },
    {
        "name": "ha.light_turn_on",
        "description": "Turn on one or more lights, optionally setting brightness or colour.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": "Light entity ID or list of IDs.",
                },
                "area_id": {"type": "string", "description": "Target area instead of entity."},
                "device_id": {"type": "string", "description": "Target device instead of entity."},
                "brightness": {"type": "integer", "description": "0-255."},
                "brightness_pct": {"type": "number", "description": "0-100."},
                "color_temp_kelvin": {"type": "integer", "description": "Colour temperature in K."},
                "rgb_color": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "[R, G, B] 0-255 each.",
                },
                "transition": {"type": "number", "description": "Transition seconds."},
            },
        },
    },
    {
        "name": "ha.light_turn_off",
        "description": "Turn off one or more lights.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": "Light entity ID or list of IDs.",
                },
                "area_id": {"type": "string", "description": "Target area instead of entity."},
                "device_id": {"type": "string", "description": "Target device instead of entity."},
                "transition": {"type": "number", "description": "Transition seconds."},
            },
        },
    },
]
