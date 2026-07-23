"""Home Assistant area registry command (``ha.config.area_registry``).

WS: ``config/area_registry/{list,create,update,delete}``.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from openclaw_node.ha_client import HAClientError, ha_ws_call

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

_ACTIONS: Final[frozenset[str]] = frozenset({"list", "create", "update", "delete"})


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _to_error(exc: HAClientError) -> dict[str, Any]:
    return _error(exc.code, exc.message)


def _require_proposal(params: dict[str, Any], action: str) -> dict[str, Any] | None:
    label = f"ha.config.area_registry action={action}"
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


def _require_area_id(params: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    raw = params.get("area_id")
    if not isinstance(raw, str):
        return None, _error("MISSING_PARAM", "area_id must be a string and is required")
    trimmed = raw.strip()
    if not trimmed:
        return None, _error("MISSING_PARAM", "area_id must be a non-empty string")
    return trimmed, None


def _require_name(params: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    raw = params.get("name")
    if not isinstance(raw, str):
        return None, _error("MISSING_PARAM", "name must be a string and is required")
    trimmed = raw.strip()
    if not trimmed:
        return None, _error("MISSING_PARAM", "name must be a non-empty string")
    return trimmed, None


async def handle_ha_config_area_registry(params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch an area-registry action."""
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
            result = await ha_ws_call("config/area_registry/list")
        except HAClientError as exc:
            return _to_error(exc)
        if not isinstance(result, list):
            return _error("HA_BAD_RESPONSE", "Expected list from config/area_registry/list")
        return {"ok": True, "count": len(result), "areas": result}

    denied = _require_proposal(params, action)
    if denied is not None:
        return denied
    proposal_id = str(params["proposal_id"]).strip()

    if action == "create":
        name, err = _require_name(params)
        if err is not None:
            return err
        payload: dict[str, Any] = {"name": name}
        if isinstance(params.get("attrs"), dict):
            payload.update(params["attrs"])
        _LOG.warning("ha.config.area_registry create name=%s proposal=%s", name, proposal_id)
        try:
            result = await ha_ws_call("config/area_registry/create", payload)
        except HAClientError as exc:
            return _to_error(exc)
        return {"ok": True, "proposal_id": proposal_id, "area": result}

    area_id, err = _require_area_id(params)
    if err is not None:
        return err

    if action == "update":
        attrs = params.get("attrs")
        if not isinstance(attrs, dict):
            return _error("MISSING_PARAM", "attrs must be a dict and is required")
        payload = {"area_id": area_id, **attrs}
        _LOG.warning("ha.config.area_registry update area_id=%s proposal=%s", area_id, proposal_id)
        try:
            result = await ha_ws_call("config/area_registry/update", payload)
        except HAClientError as exc:
            return _to_error(exc)
        return {"ok": True, "area_id": area_id, "proposal_id": proposal_id, "area": result}

    # delete
    _LOG.warning("ha.config.area_registry delete area_id=%s proposal=%s", area_id, proposal_id)
    try:
        await ha_ws_call("config/area_registry/delete", {"area_id": area_id})
    except HAClientError as exc:
        return _to_error(exc)
    return {"ok": True, "area_id": area_id, "proposal_id": proposal_id}
