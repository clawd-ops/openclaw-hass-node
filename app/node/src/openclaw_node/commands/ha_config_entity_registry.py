"""Home Assistant entity registry command (``ha.config.entity_registry``).

WS: ``config/entity_registry/{list,get,update,remove}``.

Mutations currently fail closed with ``PROPOSAL_REQUIRED``: no caller-supplied
``proposal_id`` can authorize a mutation. The retained API adapters are dormant
until a trusted approval verifier and human approval round-trip are implemented.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from openclaw_node.commands.config_mutation import require_config_mutation_approval
from openclaw_node.commands.ha import (
    ENTITY_REGISTRY_FILTERS,
    filter_entity_registry,
    filter_param_error,
)
from openclaw_node.ha_client import HAClientError, ha_ws_call

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

_ACTIONS: Final[frozenset[str]] = frozenset({"list", "get", "update", "remove"})


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _to_error(exc: HAClientError) -> dict[str, Any]:
    return _error(exc.code, exc.message)


def _require_entity_id(params: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    raw = params.get("entity_id")
    if not isinstance(raw, str):
        return None, _error("MISSING_PARAM", "entity_id must be a string and is required")
    trimmed = raw.strip()
    if not trimmed:
        return None, _error("MISSING_PARAM", "entity_id must be a non-empty string")
    return trimmed, None


async def handle_ha_config_entity_registry(params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch an entity-registry action.

    The ``list`` action accepts optional ``domain``, ``platform``, ``area_id``,
    and ``device_id`` filters, AND-combined and applied after the fetch. They
    bound the response the caller receives; HA accepts no server-side narrowing
    on this frame, which is what exceeded the transport ceiling in issue #316.
    """
    action = params.get("action")
    if not isinstance(action, str) or not action.strip():
        return _error("INVALID_PARAM", "action is required")
    action = action.strip()
    if action not in _ACTIONS:
        return _error(
            "INVALID_PARAM",
            f"action must be one of {sorted(_ACTIONS)}, got {action!r}",
        )

    if action == "list":
        invalid = filter_param_error(params, ENTITY_REGISTRY_FILTERS)
        if invalid is not None:
            return invalid
        # Read each filter through a literal key here rather than forwarding
        # `params` wholesale: the command-coverage generator derives an action's
        # accepted parameters by AST-walking its branch for literal
        # ``params.get`` keys, and a forwarded dict leaves the ledger claiming
        # this action takes only `action`.
        filters = {
            "domain": params.get("domain"),
            "platform": params.get("platform"),
            "area_id": params.get("area_id"),
            "device_id": params.get("device_id"),
        }
        try:
            result = await ha_ws_call("config/entity_registry/list")
        except HAClientError as exc:
            return _to_error(exc)
        if not isinstance(result, list):
            return _error("HA_BAD_RESPONSE", "Expected list from config/entity_registry/list")
        entities = filter_entity_registry(result, filters)
        return {"ok": True, "count": len(entities), "entities": entities}

    if action in {"update", "remove"}:
        denied = require_config_mutation_approval("ha.config.entity_registry", action)
        if denied is not None:
            return denied

    entity_id, err = _require_entity_id(params)
    if err is not None:
        return err

    if action == "get":
        try:
            result = await ha_ws_call("config/entity_registry/get", {"entity_id": entity_id})
        except HAClientError as exc:
            return _to_error(exc)
        if not isinstance(result, dict):
            return _error("HA_BAD_RESPONSE", "Expected dict from config/entity_registry/get")
        return {"ok": True, "entity_id": entity_id, "entity": result}

    proposal_id = str(params["proposal_id"]).strip()

    if action == "update":
        attrs = params.get("attrs")
        if not isinstance(attrs, dict):
            return _error("MISSING_PARAM", "attrs must be a dict and is required")
        payload = {"entity_id": entity_id, **attrs}
        _LOG.warning(
            "ha.config.entity_registry update entity=%s proposal=%s", entity_id, proposal_id
        )
        try:
            result = await ha_ws_call("config/entity_registry/update", payload)
        except HAClientError as exc:
            return _to_error(exc)
        return {
            "ok": True,
            "entity_id": entity_id,
            "proposal_id": proposal_id,
            "entity": result,
        }

    # remove
    _LOG.warning("ha.config.entity_registry remove entity=%s proposal=%s", entity_id, proposal_id)
    try:
        await ha_ws_call("config/entity_registry/remove", {"entity_id": entity_id})
    except HAClientError as exc:
        return _to_error(exc)
    return {"ok": True, "entity_id": entity_id, "proposal_id": proposal_id}
