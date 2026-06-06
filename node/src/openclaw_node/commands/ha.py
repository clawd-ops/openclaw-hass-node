"""Home Assistant control surface (P4.1 commands).

Implements the read and call-service primitives that replace the equivalent
``mcp__homeassistant__*`` MCP tools.  All handlers are async and talk to HA
via :mod:`openclaw_node.ha_client`.

Commands in this module:

- ``ha.list_states``  — return state of all entities.
- ``ha.get_state``    — return state of one entity.
- ``ha.call_service`` — call a service (domain, service, target, data).
"""

from __future__ import annotations

import logging
from typing import Any, Final

from openclaw_node.ha_client import HAClientError, ha_get, ha_post

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
