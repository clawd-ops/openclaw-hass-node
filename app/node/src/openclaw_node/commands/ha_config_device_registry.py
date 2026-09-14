"""Home Assistant device registry command (``ha.config.device_registry``).

WS: ``config/device_registry/{list,update}``. HA does not expose create
or delete for devices — they're populated by integrations.

Mutations currently fail closed with ``PROPOSAL_REQUIRED``: no caller-supplied
``proposal_id`` can authorize a mutation. The retained API adapters are dormant
until a trusted approval verifier and human approval round-trip are implemented.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from openclaw_node.commands.config_mutation import require_config_mutation_approval
from openclaw_node.commands.ha import (
    DEVICE_REGISTRY_FILTERS,
    filter_device_registry,
    filter_param_error,
)
from openclaw_node.ha_client import HAClientError, ha_ws_call

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

_ACTIONS: Final[frozenset[str]] = frozenset({"list", "update"})


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _to_error(exc: HAClientError) -> dict[str, Any]:
    return _error(exc.code, exc.message)


async def handle_ha_config_device_registry(params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a device-registry action.

    The ``list`` action accepts optional ``area_id`` and ``config_entry_id``
    filters, AND-combined and applied after the fetch. They bound the response
    the caller receives; HA accepts no server-side narrowing on this frame.
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
        invalid = filter_param_error(params, DEVICE_REGISTRY_FILTERS)
        if invalid is not None:
            return invalid
        # Read each filter through a literal key here rather than forwarding
        # `params` wholesale: the command-coverage generator derives an action's
        # accepted parameters by AST-walking its branch for literal
        # ``params.get`` keys, and a forwarded dict leaves the ledger claiming
        # this action takes only `action`.
        filters = {
            "area_id": params.get("area_id"),
            "config_entry_id": params.get("config_entry_id"),
        }
        try:
            result = await ha_ws_call("config/device_registry/list")
        except HAClientError as exc:
            return _to_error(exc)
        if not isinstance(result, list):
            return _error("HA_BAD_RESPONSE", "Expected list from config/device_registry/list")
        devices = filter_device_registry(result, filters)
        return {"ok": True, "count": len(devices), "devices": devices}

    denied = require_config_mutation_approval("ha.config.device_registry", action)
    if denied is not None:
        return denied
    proposal_id = str(params["proposal_id"]).strip()

    device_id_raw = params.get("device_id")
    if not isinstance(device_id_raw, str) or not device_id_raw.strip():
        return _error("MISSING_PARAM", "device_id must be a non-empty string")
    device_id = device_id_raw.strip()

    attrs = params.get("attrs")
    if not isinstance(attrs, dict):
        return _error("MISSING_PARAM", "attrs must be a dict and is required")

    payload = {"device_id": device_id, **attrs}
    _LOG.warning(
        "ha.config.device_registry update device_id=%s proposal=%s", device_id, proposal_id
    )
    try:
        result = await ha_ws_call("config/device_registry/update", payload)
    except HAClientError as exc:
        return _to_error(exc)
    return {
        "ok": True,
        "device_id": device_id,
        "proposal_id": proposal_id,
        "device": result,
    }
