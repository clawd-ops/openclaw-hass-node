"""Home Assistant Lovelace configuration command (``ha.config.lovelace``).

HA-native path for editing dashboards. The command talks to HA's
WebSocket API via :func:`openclaw_node.ha_client.ha_ws_call`; the node
never touches ``/config/.storage/`` directly for lovelace state.

Single command with an ``action`` param. Supported actions:

- ``get`` — read a dashboard config (default or named).
- ``save`` — write a dashboard config (requires a valid native approval marker).
- ``dashboards_list`` — list configured dashboards.
- ``resources_list`` — list registered resources.
- ``resources_create`` — register a new resource (requires a valid native approval marker).

Mutations require a valid native approval marker (see
:mod:`openclaw_node.commands.config_mutation`); without one they return
``PROPOSAL_REQUIRED``. ``proposal_id`` is audit metadata and never authorizes.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from openclaw_node.commands.config_mutation import consume_approval_marker
from openclaw_node.commands.params import strict_keys_error
from openclaw_node.ha_client import HAClientError, ha_ws_call

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

_LOVELACE_RESOURCE_TYPES: Final[frozenset[str]] = frozenset({"module", "css", "js", "html"})

_ACTIONS: Final[frozenset[str]] = frozenset(
    {"get", "save", "dashboards_list", "resources_list", "resources_create"}
)
_MUTATING_ACTIONS: Final[frozenset[str]] = frozenset({"save", "resources_create"})


_ACTION_KEYS: Final[dict[str, frozenset[str]]] = {
    "get": frozenset({"action", "url_path"}),
    "save": frozenset({"action", "url_path", "config", "proposal_id", "_openclaw_approval"}),
    "dashboards_list": frozenset({"action"}),
    "resources_list": frozenset({"action"}),
    "resources_create": frozenset(
        {"action", "url", "res_type", "proposal_id", "_openclaw_approval"}
    ),
}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _to_error(exc: HAClientError) -> dict[str, Any]:
    return _error(exc.code, exc.message)


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
    denied = consume_approval_marker("ha.config.lovelace", "save", params)
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

    _LOG.warning(
        "ha.config.lovelace save invoked url_path=%r proposal=%r",
        url_path,
        params.get("proposal_id"),
    )
    try:
        await ha_ws_call("lovelace/config/save", payload)
    except HAClientError as exc:
        return _to_error(exc)
    return {"ok": True, "url_path": url_path}


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
    denied = consume_approval_marker("ha.config.lovelace", "resources_create", params)
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

    payload = {"url": url.strip(), "res_type": res_type}
    _LOG.warning(
        "ha.config.lovelace resources_create invoked url=%r res_type=%s proposal=%r",
        url,
        res_type,
        params.get("proposal_id"),
    )
    try:
        result = await ha_ws_call("lovelace/resources/create", payload)
    except HAClientError as exc:
        return _to_error(exc)
    return {"ok": True, "resource": result}


async def handle_ha_config_lovelace(params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a Lovelace config action.

    Params:
        action (str): Required; one of ``get``, ``save``, ``dashboards_list``,
            ``resources_list``, ``resources_create``.
        url_path (str | None): Optional dashboard path for ``get`` and
            ``save``. Omit it for the default dashboard.
        config (dict): Required for ``save``; the complete Lovelace
            configuration submitted to Home Assistant.
        url (str): Required for ``resources_create``; the resource URL.
        res_type (str): Required for ``resources_create``; one of
            ``module``, ``css``, ``js``, or ``html``.
        proposal_id (str): Audit metadata for ``save`` and
            ``resources_create``; it never grants authorization.

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
    invalid = strict_keys_error(params, _ACTION_KEYS[action], frozenset({"url_path"}))
    if invalid is not None:
        return invalid
    if action == "get":
        return await _action_get(params)
    if action == "save":
        return await _action_save(params)
    if action == "dashboards_list":
        return await _action_dashboards_list(params)
    if action == "resources_list":
        return await _action_resources_list(params)
    return await _action_resources_create(params)
