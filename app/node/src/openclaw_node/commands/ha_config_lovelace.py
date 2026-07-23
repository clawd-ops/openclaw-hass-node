"""Home Assistant Lovelace configuration command (``ha.config.lovelace``).

HA-native path for editing dashboards. The command talks to HA's
WebSocket API via :func:`openclaw_node.ha_client.ha_ws_call`; the node
never touches ``/config/.storage/`` directly for lovelace state.

Single command with an ``action`` param. Supported actions:

- ``get`` — read a dashboard config (default or named).
- ``save`` — write a dashboard config (proposal-gated).
- ``dashboards_list`` — list configured dashboards.
- ``resources_list`` — list registered resources.
- ``resources_create`` — register a new resource (proposal-gated).
"""

from __future__ import annotations

import logging
from typing import Any, Final

from openclaw_node.ha_client import HAClientError, ha_ws_call

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

_LOVELACE_RESOURCE_TYPES: Final[frozenset[str]] = frozenset({"module", "css", "js", "html"})

_ACTIONS: Final[frozenset[str]] = frozenset(
    {"get", "save", "dashboards_list", "resources_list", "resources_create"}
)
_MUTATING_ACTIONS: Final[frozenset[str]] = frozenset({"save", "resources_create"})


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _to_error(exc: HAClientError) -> dict[str, Any]:
    return _error(exc.code, exc.message)


def _require_proposal(params: dict[str, Any], action: str) -> dict[str, Any] | None:
    """Enforce proposal gating on mutating lovelace actions.

    Mirrors the fs.write / fs.patch convention: mutating calls must carry an
    explicit ``proposal_id`` naming the agent-bridge proposal that authorised
    the change. ``"direct"`` is refused so operator-facing audits can always
    trace the mutation back to a review record.
    """
    label = f"ha.config.lovelace action={action}"
    raw = params.get("proposal_id")
    if not isinstance(raw, str):
        return _error("PROPOSAL_REQUIRED", f"{label}: proposal_id is required")
    proposal_id = raw.strip()
    if not proposal_id:
        return _error("PROPOSAL_REQUIRED", f"{label}: proposal_id is required")
    if proposal_id == "direct":
        return _error(
            "PROPOSAL_REQUIRED",
            f"{label}: proposal_id='direct' is not accepted for HA-native mutations",
        )
    return None


def _optional_url_path(params: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Extract ``url_path`` from params; return ``(value, error)``."""
    raw = params.get("url_path")
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, _error("INVALID_PARAM", "url_path must be a string")
    trimmed = raw.strip()
    if not trimmed:
        return None, None
    return trimmed, None


async def _action_get(params: dict[str, Any]) -> dict[str, Any]:
    url_path, err = _optional_url_path(params)
    if err is not None:
        return err

    payload: dict[str, Any] = {}
    if url_path is not None:
        payload["url_path"] = url_path

    try:
        result = await ha_ws_call("lovelace/config", payload)
    except HAClientError as exc:
        return _to_error(exc)

    if not isinstance(result, dict):
        return _error("HA_BAD_RESPONSE", "Expected dict from lovelace/config")
    return {"ok": True, "url_path": url_path, "config": result}


async def _action_save(params: dict[str, Any]) -> dict[str, Any]:
    denied = _require_proposal(params, "save")
    if denied is not None:
        return denied

    config = params.get("config")
    if not isinstance(config, dict):
        return _error("MISSING_PARAM", "config must be a dict and is required")

    url_path, err = _optional_url_path(params)
    if err is not None:
        return err

    payload: dict[str, Any] = {"config": config}
    if url_path is not None:
        payload["url_path"] = url_path

    proposal_id = str(params["proposal_id"]).strip()
    _LOG.warning(
        "ha.config.lovelace save invoked url_path=%r proposal=%s",
        url_path,
        proposal_id,
    )
    try:
        await ha_ws_call("lovelace/config/save", payload)
    except HAClientError as exc:
        return _to_error(exc)
    return {"ok": True, "url_path": url_path, "proposal_id": proposal_id}


async def _action_dashboards_list(_params: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await ha_ws_call("lovelace/dashboards/list")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(result, list):
        return _error("HA_BAD_RESPONSE", "Expected list from lovelace/dashboards/list")
    return {"ok": True, "count": len(result), "dashboards": result}


async def _action_resources_list(_params: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await ha_ws_call("lovelace/resources")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(result, list):
        return _error("HA_BAD_RESPONSE", "Expected list from lovelace/resources")
    return {"ok": True, "count": len(result), "resources": result}


async def _action_resources_create(params: dict[str, Any]) -> dict[str, Any]:
    denied = _require_proposal(params, "resources_create")
    if denied is not None:
        return denied

    url = params.get("url")
    if not isinstance(url, str) or not url.strip():
        return _error("MISSING_PARAM", "url is required")
    res_type = params.get("res_type")
    if not isinstance(res_type, str) or not res_type.strip():
        return _error("MISSING_PARAM", "res_type is required")
    if res_type not in _LOVELACE_RESOURCE_TYPES:
        return _error(
            "INVALID_PARAM",
            f"res_type must be one of {sorted(_LOVELACE_RESOURCE_TYPES)}, got {res_type!r}",
        )

    proposal_id = str(params["proposal_id"]).strip()
    payload = {"url": url.strip(), "res_type": res_type}
    _LOG.warning(
        "ha.config.lovelace resources_create invoked url=%r res_type=%s proposal=%s",
        url,
        res_type,
        proposal_id,
    )
    try:
        result = await ha_ws_call("lovelace/resources/create", payload)
    except HAClientError as exc:
        return _to_error(exc)
    return {"ok": True, "resource": result, "proposal_id": proposal_id}


async def handle_ha_config_lovelace(params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a Lovelace config action.

    Params:
        action (str): Required; one of ``get``, ``save``, ``dashboards_list``,
            ``resources_list``, ``resources_create``.
        (per-action params — see the module docstring and per-action helpers.)

    Returns:
        The action's result dict, or an error dict when action is
        missing/unknown or params are invalid.
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
    if action == "get":
        return await _action_get(params)
    if action == "save":
        return await _action_save(params)
    if action == "dashboards_list":
        return await _action_dashboards_list(params)
    if action == "resources_list":
        return await _action_resources_list(params)
    return await _action_resources_create(params)
