"""Home Assistant control surface (P4.1 - P4.4 commands).

Implements the read and call-service primitives that replace the equivalent
``mcp__homeassistant__*`` MCP tools.  All handlers are async and talk to HA
via :mod:`openclaw_node.ha_client`.

Commands in this module:

- ``ha.list_states``          — return state of all entities.
- ``ha.get_state``            — return state of one entity.
- ``ha.call_service``         — call a service (domain, service, target, data).
- ``ha.list_areas``           — return all area-registry entries.
- ``ha.list_devices``         — return all device-registry entries.
- ``ha.list_services``        — return all service descriptions by domain.
- ``ha.list_entity_registry`` — return all entity-registry entries.
- ``ha.logbook``              — return logbook entries (optional entity + time window).
- ``ha.history``              — return state history for entities (optional time window).
- ``ha.reload_config``        — reload HA core config (operator-admin gated).
- ``ha.light_turn_on``        — turn on one or more lights (entity/area/device target).
- ``ha.light_turn_off``       — turn off one or more lights.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from openclaw_node.ha_client import HAClientError, ha_get, ha_post, ha_ws_call

_LOG: Final[logging.Logger] = logging.getLogger(__name__)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _to_error(exc: HAClientError) -> dict[str, Any]:
    return _error(exc.code, exc.message)


async def handle_ha_list_states(params: dict[str, Any]) -> dict[str, Any]:
    """Return the state of every entity registered in HA.

    Params:
        domain (str, optional): If provided, only return entities whose
            ``entity_id`` is under that domain (e.g. ``"sensor"``).

    Returns:
        ``{ok: True, count, states}`` or an error dict.
    """
    domain_filter = params.get("domain")
    try:
        raw = await ha_get("/api/states")
    except HAClientError as exc:
        return _to_error(exc)

    if not isinstance(raw, list):
        return _error("HA_BAD_RESPONSE", "Expected list from /api/states")

    states: list[dict[str, Any]] = raw
    if domain_filter:
        prefix = f"{domain_filter}."
        states = [
            s
            for s in states
            if isinstance(s.get("entity_id"), str) and s["entity_id"].startswith(prefix)
        ]

    return {"ok": True, "count": len(states), "states": states}


async def handle_ha_get_state(params: dict[str, Any]) -> dict[str, Any]:
    """Return the state object for a single entity.

    Params:
        entity_id (str): Required; e.g. ``"sensor.kitchen_temperature"``.

    Returns:
        ``{ok: True, state}`` or an error dict.
    """
    entity_id = str(params.get("entity_id", ""))
    if not entity_id:
        return _error("MISSING_PARAM", "entity_id is required")

    try:
        state = await ha_get(f"/api/states/{entity_id}")
    except HAClientError as exc:
        return _to_error(exc)

    if not isinstance(state, dict):
        return _error("HA_BAD_RESPONSE", "Expected dict from /api/states/<entity_id>")

    return {"ok": True, "state": state}


async def handle_ha_call_service(params: dict[str, Any]) -> dict[str, Any]:
    """Call a Home Assistant service.

    Params:
        domain (str): Required; e.g. ``"light"``.
        service (str): Required; e.g. ``"turn_on"``.
        target (dict, optional): Service target dict (entity_id/area_id/device_id).
        data (dict, optional): Service-specific data payload.

    Returns:
        ``{ok: True, changed_states}`` with the HA response (list of state
        objects that changed) or an error dict.
    """
    domain = str(params.get("domain", ""))
    service = str(params.get("service", ""))
    if not domain:
        return _error("MISSING_PARAM", "domain is required")
    if not service:
        return _error("MISSING_PARAM", "service is required")

    target = params.get("target")
    data = params.get("data")
    if target is not None and not isinstance(target, dict):
        return _error("INVALID_PARAM", "target must be a dict")
    if data is not None and not isinstance(data, dict):
        return _error("INVALID_PARAM", "data must be a dict")

    body: dict[str, Any] = {}
    if data:
        body.update(data)
    if target:
        # HA REST collapses target into the body for service calls.
        body.update(target)

    try:
        result = await ha_post(f"/api/services/{domain}/{service}", body or None)
    except HAClientError as exc:
        return _to_error(exc)

    changed: list[dict[str, Any]] = result if isinstance(result, list) else []
    return {"ok": True, "changed_states": changed}


async def handle_ha_list_areas(_params: dict[str, Any]) -> dict[str, Any]:
    """Return all area-registry entries from Home Assistant.

    Returns:
        ``{ok: True, count, areas}`` where each area is a dict with at least
        ``area_id`` and ``name``, or an error dict.
    """
    try:
        result = await ha_ws_call("config/area_registry/list")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(result, list):
        return _error("HA_BAD_RESPONSE", "Expected list from area_registry/list")
    return {"ok": True, "count": len(result), "areas": result}


async def handle_ha_list_devices(_params: dict[str, Any]) -> dict[str, Any]:
    """Return all device-registry entries from Home Assistant.

    Returns:
        ``{ok: True, count, devices}`` or an error dict.
    """
    try:
        result = await ha_ws_call("config/device_registry/list")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(result, list):
        return _error("HA_BAD_RESPONSE", "Expected list from device_registry/list")
    return {"ok": True, "count": len(result), "devices": result}


async def handle_ha_list_services(_params: dict[str, Any]) -> dict[str, Any]:
    """Return all service descriptions from Home Assistant.

    Uses the REST ``/api/services`` endpoint which returns a list of domain
    objects, each containing the services available in that domain.

    Returns:
        ``{ok: True, count, services}`` or an error dict.
    """
    try:
        raw = await ha_get("/api/services")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(raw, list):
        return _error("HA_BAD_RESPONSE", "Expected list from /api/services")
    return {"ok": True, "count": len(raw), "services": raw}


async def handle_ha_list_entity_registry(_params: dict[str, Any]) -> dict[str, Any]:
    """Return all entity-registry entries from Home Assistant.

    Returns:
        ``{ok: True, count, entities}`` or an error dict.
    """
    try:
        result = await ha_ws_call("config/entity_registry/list")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(result, list):
        return _error("HA_BAD_RESPONSE", "Expected list from entity_registry/list")
    return {"ok": True, "count": len(result), "entities": result}


async def handle_ha_logbook(params: dict[str, Any]) -> dict[str, Any]:
    """Return logbook entries from Home Assistant.

    Params:
        start_time (str, optional): ISO-8601 timestamp; default is one day ago.
        end_time (str, optional): ISO-8601 upper bound.
        entity_id (str, optional): Restrict to a single entity.

    Returns:
        ``{ok: True, count, entries}`` or an error dict.
    """
    path = "/api/logbook"
    start_time = str(params.get("start_time", ""))
    if start_time:
        path = f"{path}/{start_time}"

    query_parts: list[str] = []
    end_time = str(params.get("end_time", ""))
    if end_time:
        query_parts.append(f"end_time={end_time}")
    entity_id = str(params.get("entity_id", ""))
    if entity_id:
        query_parts.append(f"entity={entity_id}")
    if query_parts:
        path = f"{path}?{'&'.join(query_parts)}"

    try:
        raw = await ha_get(path)
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(raw, list):
        return _error("HA_BAD_RESPONSE", "Expected list from /api/logbook")
    return {"ok": True, "count": len(raw), "entries": raw}


async def handle_ha_history(params: dict[str, Any]) -> dict[str, Any]:
    """Return state history for entities from Home Assistant.

    Params:
        start_time (str, optional): ISO-8601 start; default is one day ago.
        end_time (str, optional): ISO-8601 upper bound.
        entity_ids (list[str], optional): List of entity IDs to filter.
        minimal_response (bool, optional): Reduce payload size (default False).
        no_attributes (bool, optional): Omit attributes (default False).
        significant_changes_only (bool, optional): Only significant changes.

    Returns:
        ``{ok: True, count, history}`` where ``history`` is a list of entity
        history lists, or an error dict.
    """
    path = "/api/history/period"
    start_time = str(params.get("start_time", ""))
    if start_time:
        path = f"{path}/{start_time}"

    query_parts: list[str] = []
    end_time = str(params.get("end_time", ""))
    if end_time:
        query_parts.append(f"end_time={end_time}")
    entity_ids = params.get("entity_ids")
    if entity_ids is not None:
        if not isinstance(entity_ids, list) or not all(isinstance(e, str) for e in entity_ids):
            return _error("INVALID_PARAM", "entity_ids must be a list of strings")
        query_parts.append(f"filter_entity_id={','.join(entity_ids)}")
    for flag in ("minimal_response", "no_attributes", "significant_changes_only"):
        if params.get(flag):
            query_parts.append(flag)
    if query_parts:
        path = f"{path}?{'&'.join(query_parts)}"

    try:
        raw = await ha_get(path)
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(raw, list):
        return _error("HA_BAD_RESPONSE", "Expected list from /api/history/period")
    return {"ok": True, "count": len(raw), "history": raw}


async def handle_ha_reload_config(params: dict[str, Any]) -> dict[str, Any]:
    """Reload the Home Assistant core configuration.

    This is an operator-admin action; the caller must supply a valid
    ``OPENCLAW_ADMIN_TOKEN`` in the environment (same gate as ``system.run``).

    Returns:
        ``{ok: True}`` on success or an error dict.
    """
    import hmac
    import os

    required = os.environ.get("OPENCLAW_ADMIN_TOKEN", "")
    if not required:
        return _error("PERMISSION_DENIED", "ha.reload_config: admin gate not configured")
    caller = str(params.get("admin_token", ""))
    if not hmac.compare_digest(caller.encode(), required.encode()):
        return _error("PERMISSION_DENIED", "ha.reload_config requires operator admin token")

    try:
        await ha_post("/api/services/homeassistant/reload_core_config")
    except HAClientError as exc:
        return _to_error(exc)
    return {"ok": True}


def _build_light_target(params: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Extract and validate a light service target from params.

    Returns:
        ``(target_dict, None)`` on success, or ``(None, error_message)`` on failure.
    """
    entity_id = params.get("entity_id")
    area_id = params.get("area_id")
    device_id = params.get("device_id")
    if entity_id is None and area_id is None and device_id is None:
        return None, "one of entity_id, area_id, or device_id is required"
    target: dict[str, Any] = {}
    if entity_id is not None:
        if not isinstance(entity_id, (str, list)):
            return None, "entity_id must be a string or list of strings"
        target["entity_id"] = entity_id
    if area_id is not None:
        target["area_id"] = area_id
    if device_id is not None:
        target["device_id"] = device_id
    return target, None


async def handle_ha_light_turn_on(params: dict[str, Any]) -> dict[str, Any]:
    """Turn on one or more lights.

    Params:
        entity_id (str | list[str], optional): Target entity or entities.
        area_id (str, optional): Target area.
        device_id (str, optional): Target device.
        brightness (int, optional): 0-255.
        brightness_pct (float, optional): 0-100.
        color_temp_kelvin (int, optional): Colour temperature in Kelvin.
        rgb_color (list[int], optional): [R, G, B] 0-255 each.
        transition (float, optional): Transition time in seconds.

    One of ``entity_id``, ``area_id``, or ``device_id`` is required.

    Returns:
        ``{ok: True, changed_states}`` or an error dict.
    """
    target, err = _build_light_target(params)
    if err is not None:
        return _error("MISSING_PARAM", err)

    data: dict[str, Any] = {}
    for key in ("brightness", "brightness_pct", "color_temp_kelvin", "rgb_color", "transition"):
        if key in params:
            data[key] = params[key]

    body: dict[str, Any] = {}
    if data:
        body.update(data)
    if target:
        body.update(target)

    try:
        result = await ha_post("/api/services/light/turn_on", body or None)
    except HAClientError as exc:
        return _to_error(exc)

    changed: list[dict[str, Any]] = result if isinstance(result, list) else []
    return {"ok": True, "changed_states": changed}


async def handle_ha_light_turn_off(params: dict[str, Any]) -> dict[str, Any]:
    """Turn off one or more lights.

    Params:
        entity_id (str | list[str], optional): Target entity or entities.
        area_id (str, optional): Target area.
        device_id (str, optional): Target device.
        transition (float, optional): Transition time in seconds.

    One of ``entity_id``, ``area_id``, or ``device_id`` is required.

    Returns:
        ``{ok: True, changed_states}`` or an error dict.
    """
    target, err = _build_light_target(params)
    if err is not None:
        return _error("MISSING_PARAM", err)

    body: dict[str, Any] = {}
    if "transition" in params:
        body["transition"] = params["transition"]
    if target:
        body.update(target)

    try:
        result = await ha_post("/api/services/light/turn_off", body or None)
    except HAClientError as exc:
        return _to_error(exc)

    changed: list[dict[str, Any]] = result if isinstance(result, list) else []
    return {"ok": True, "changed_states": changed}
