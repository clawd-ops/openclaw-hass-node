"""Home Assistant entity registry command (``ha.config.entity_registry``).

WS: ``config/entity_registry/{list,get,update,remove}``.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from openclaw_node.ha_client import HAClientError, ha_ws_call

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

_ACTIONS: Final[frozenset[str]] = frozenset({"list", "get", "update", "remove"})


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _to_error(exc: HAClientError) -> dict[str, Any]:
    return _error(exc.code, exc.message)


def _require_proposal(params: dict[str, Any], action: str) -> dict[str, Any] | None:
    label = f"ha.config.entity_registry action={action}"
    raw = params.get("proposal_id")
    if not isinstance(raw, str):
        return _error("PROPOSAL_REQUIRED", f"{label}: proposal_id is required")
    proposal_id = raw.strip()
    if not proposal_id:
        return _error("PROPOSAL_REQUIRED", f"{label}: proposal_id is required")
    if proposal_id == "direct":
        return _error(
            "PROPOSAL_REQUIRED",
            f"{label}: proposal_id='direct' is not a valid proposal",
        )
    return None


def _require_entity_id(params: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    raw = params.get("entity_id")
    if not isinstance(raw, str):
        return None, _error("MISSING_PARAM", "entity_id must be a string and is required")
    trimmed = raw.strip()
    if not trimmed:
        return None, _error("MISSING_PARAM", "entity_id must be a non-empty string")
    return trimmed, None


async def handle_ha_config_entity_registry(params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch an entity-registry action."""
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
        try:
            result = await ha_ws_call("config/entity_registry/list")
        except HAClientError as exc:
            return _to_error(exc)
        if not isinstance(result, list):
            return _error("HA_BAD_RESPONSE", "Expected list from config/entity_registry/list")
        return {"ok": True, "count": len(result), "entities": result}

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

    denied = _require_proposal(params, action)
    if denied is not None:
        return denied
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
