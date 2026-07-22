"""Home Assistant Lovelace configuration commands (`ha.config.lovelace.*`).

HA-native path for editing dashboards. All commands talk to HA's WebSocket
API via :func:`openclaw_node.ha_client.ha_ws_call`; the node never touches
``/config/.storage/`` directly for lovelace state.

Commands:

- ``ha.config.lovelace.get`` — read a dashboard config (default or named).
- ``ha.config.lovelace.save`` — write a dashboard config (proposal-gated).
- ``ha.config.lovelace.dashboards_list`` — list configured dashboards.
- ``ha.config.lovelace.resources_list`` — list registered resources.
- ``ha.config.lovelace.resources_create`` — register a new resource
  (proposal-gated).
"""

from __future__ import annotations

import logging
from typing import Any, Final

from openclaw_node.ha_client import HAClientError, ha_ws_call

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

_LOVELACE_RESOURCE_TYPES: Final[frozenset[str]] = frozenset({"module", "css", "js", "html"})


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _to_error(exc: HAClientError) -> dict[str, Any]:
    return _error(exc.code, exc.message)


def _require_proposal(params: dict[str, Any], command: str) -> dict[str, Any] | None:
    """Enforce proposal gating on mutating lovelace commands.

    Mirrors the fs.write / fs.patch convention: mutating calls must carry an
    explicit ``proposal_id`` naming the agent-bridge proposal that authorised
    the change. ``"direct"`` is refused so operator-facing audits can always
    trace the mutation back to a review record.
    """
    raw = params.get("proposal_id")
    if not isinstance(raw, str):
        return _error("PROPOSAL_REQUIRED", f"{command}: proposal_id is required")
    proposal_id = raw.strip()
    if not proposal_id:
        return _error("PROPOSAL_REQUIRED", f"{command}: proposal_id is required")
    if proposal_id == "direct":
        return _error(
            "PROPOSAL_REQUIRED",
            f"{command}: proposal_id='direct' is not accepted for HA-native mutations",
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


async def handle_ha_config_lovelace_get(params: dict[str, Any]) -> dict[str, Any]:
    """Return a Lovelace dashboard configuration.

    Params:
        url_path (str, optional): Dashboard ``url_path``. When omitted, the
            default dashboard is returned. When provided, the WS message type
            is ``lovelace_<url_path>/config`` (HA's convention for
            non-default dashboards); ``url_path=None`` uses ``lovelace/config``.

    Returns:
        ``{ok: True, url_path, config}`` where ``config`` is the raw WS
        result, or an error dict.
    """
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


async def handle_ha_config_lovelace_save(params: dict[str, Any]) -> dict[str, Any]:
    """Save a Lovelace dashboard configuration (proposal-gated).

    Params:
        config (dict): Required; the full dashboard config payload.
        url_path (str, optional): Dashboard ``url_path``. Omitted → default
            dashboard.
        proposal_id (str): Required; agent-bridge proposal that authorised
            this change. ``"direct"`` is refused.

    Returns:
        ``{ok: True, url_path, proposal_id}`` on success, or an error dict.
    """
    denied = _require_proposal(params, "ha.config.lovelace.save")
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
        "ha.config.lovelace.save invoked url_path=%r proposal=%s",
        url_path,
        proposal_id,
    )
    try:
        await ha_ws_call("lovelace/config/save", payload)
    except HAClientError as exc:
        return _to_error(exc)
    return {"ok": True, "url_path": url_path, "proposal_id": proposal_id}


async def handle_ha_config_lovelace_dashboards_list(
    _params: dict[str, Any],
) -> dict[str, Any]:
    """List all configured Lovelace dashboards.

    Returns:
        ``{ok: True, count, dashboards}`` or an error dict.
    """
    try:
        result = await ha_ws_call("lovelace/dashboards/list")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(result, list):
        return _error("HA_BAD_RESPONSE", "Expected list from lovelace/dashboards/list")
    return {"ok": True, "count": len(result), "dashboards": result}


async def handle_ha_config_lovelace_resources_list(
    _params: dict[str, Any],
) -> dict[str, Any]:
    """List all Lovelace resources (custom cards, CSS, modules).

    Returns:
        ``{ok: True, count, resources}`` or an error dict.
    """
    try:
        result = await ha_ws_call("lovelace/resources")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(result, list):
        return _error("HA_BAD_RESPONSE", "Expected list from lovelace/resources")
    return {"ok": True, "count": len(result), "resources": result}


async def handle_ha_config_lovelace_resources_create(
    params: dict[str, Any],
) -> dict[str, Any]:
    """Register a new Lovelace resource (proposal-gated).

    Params:
        url (str): Required; resource URL (e.g. ``"/local/foo.js"``).
        res_type (str): Required; one of ``module``, ``css``, ``js``,
            ``html``.
        proposal_id (str): Required; agent-bridge proposal that authorised
            this mutation.

    Returns:
        ``{ok: True, resource, proposal_id}`` where ``resource`` is HA's
        response payload (includes the assigned id), or an error dict.
    """
    denied = _require_proposal(params, "ha.config.lovelace.resources_create")
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
        "ha.config.lovelace.resources_create invoked url=%r res_type=%s proposal=%s",
        url,
        res_type,
        proposal_id,
    )
    try:
        result = await ha_ws_call("lovelace/resources/create", payload)
    except HAClientError as exc:
        return _to_error(exc)
    return {"ok": True, "resource": result, "proposal_id": proposal_id}
