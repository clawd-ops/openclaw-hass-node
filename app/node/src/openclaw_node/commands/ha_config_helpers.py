"""Home Assistant helpers config command (``ha.config.helpers``).

HA-native WebSocket path for helper entities (input_boolean, input_text,
input_number, input_select, input_datetime, counter, timer, schedule).
This command talks to HA's WebSocket API via
:func:`openclaw_node.ha_client.ha_ws_call`.

Single command with an ``action`` param plus a required ``helper_type``
param selecting the underlying WS namespace. Supported actions:

- ``list`` — list all helpers of the given type.
- ``create`` — create a new helper (proposal-gated).
- ``update`` — update an existing helper (proposal-gated).
- ``delete`` — delete a helper (proposal-gated).

HA does not register a ``<helper_type>/get`` frame in its storage-
collection websocket surface; single-item lookup is served by reading
the state and entity registry. update/delete require the item key
named ``<helper_type>_id`` (e.g. ``input_boolean_id``), not
``entity_id``.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from openclaw_node.ha_client import HAClientError, ha_ws_call

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

_ACTIONS: Final[frozenset[str]] = frozenset({"list", "create", "update", "delete"})
_MUTATING_ACTIONS: Final[frozenset[str]] = frozenset({"create", "update", "delete"})
_HELPER_TYPES: Final[frozenset[str]] = frozenset(
    {
        "input_boolean",
        "input_text",
        "input_number",
        "input_select",
        "input_datetime",
        "counter",
        "timer",
        "schedule",
    }
)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _to_error(exc: HAClientError) -> dict[str, Any]:
    return _error(exc.code, exc.message)


def _require_proposal(params: dict[str, Any], action: str) -> dict[str, Any] | None:
    """Enforce proposal gating on mutating helper actions.

    Mirrors ``ha.config.automation``: mutating calls must carry an
    explicit ``proposal_id`` naming the agent-bridge proposal.
    ``"direct"`` is refused so mutations remain traceable to a review.
    """
    label = f"ha.config.helpers action={action}"
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


def _require_helper_type(params: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    raw = params.get("helper_type")
    if not isinstance(raw, str):
        return None, _error("MISSING_PARAM", "helper_type must be a string and is required")
    ht = raw.strip()
    if not ht:
        return None, _error("MISSING_PARAM", "helper_type must be a non-empty string")
    if ht not in _HELPER_TYPES:
        return None, _error(
            "INVALID_PARAM",
            f"helper_type must be one of {sorted(_HELPER_TYPES)}, got {ht!r}",
        )
    return ht, None


def _require_attrs(params: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    raw = params.get("attrs")
    if not isinstance(raw, dict):
        return None, _error("MISSING_PARAM", "attrs must be a dict and is required")
    return raw, None


async def _action_list(params: dict[str, Any]) -> dict[str, Any]:
    ht, err = _require_helper_type(params)
    if err is not None:
        return err
    try:
        result = await ha_ws_call(f"{ht}/list")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(result, list):
        return _error("HA_BAD_RESPONSE", f"Expected list from {ht}/list")
    return {"ok": True, "helper_type": ht, "count": len(result), "helpers": result}


async def _action_create(params: dict[str, Any]) -> dict[str, Any]:
    denied = _require_proposal(params, "create")
    if denied is not None:
        return denied
    ht, err = _require_helper_type(params)
    if err is not None:
        return err
    attrs, err = _require_attrs(params)
    if err is not None:
        return err
    proposal_id = str(params["proposal_id"]).strip()
    _LOG.warning("ha.config.helpers create helper_type=%s proposal=%s", ht, proposal_id)
    try:
        result = await ha_ws_call(f"{ht}/create", attrs)
    except HAClientError as exc:
        return _to_error(exc)
    return {"ok": True, "helper_type": ht, "proposal_id": proposal_id, "helper": result}


def _require_item_id(
    params: dict[str, Any], helper_type: str
) -> tuple[str | None, dict[str, Any] | None]:
    """Extract the ``<helper_type>_id`` item key required by HA."""
    key = f"{helper_type}_id"
    raw = params.get(key)
    if not isinstance(raw, str):
        return None, _error("MISSING_PARAM", f"{key} must be a string and is required")
    trimmed = raw.strip()
    if not trimmed:
        return None, _error("MISSING_PARAM", f"{key} must be a non-empty string")
    return trimmed, None


async def _action_update(params: dict[str, Any]) -> dict[str, Any]:
    denied = _require_proposal(params, "update")
    if denied is not None:
        return denied
    ht, err = _require_helper_type(params)
    if err is not None or ht is None:
        return err or _error("MISSING_PARAM", "helper_type required")
    item_id, err = _require_item_id(params, ht)
    if err is not None:
        return err
    attrs, err = _require_attrs(params)
    if err is not None or attrs is None:
        return err or _error("MISSING_PARAM", "attrs required")
    item_key = f"{ht}_id"
    # Refuse `attrs` that carries the item key — otherwise a caller could
    # log/return one id but actually target a different helper in the WS
    # frame. Fail loud rather than silently pick a winner.
    if item_key in attrs:
        return _error(
            "INVALID_PARAM",
            f"attrs must not contain {item_key!r}; pass it as the top-level param",
        )
    proposal_id = str(params["proposal_id"]).strip()
    _LOG.warning(
        "ha.config.helpers update helper_type=%s %s=%s proposal=%s",
        ht,
        item_key,
        item_id,
        proposal_id,
    )
    payload = {**attrs, item_key: item_id}
    try:
        result = await ha_ws_call(f"{ht}/update", payload)
    except HAClientError as exc:
        return _to_error(exc)
    return {
        "ok": True,
        "helper_type": ht,
        item_key: item_id,
        "proposal_id": proposal_id,
        "helper": result,
    }


async def _action_delete(params: dict[str, Any]) -> dict[str, Any]:
    denied = _require_proposal(params, "delete")
    if denied is not None:
        return denied
    ht, err = _require_helper_type(params)
    if err is not None or ht is None:
        return err or _error("MISSING_PARAM", "helper_type required")
    item_id, err = _require_item_id(params, ht)
    if err is not None:
        return err
    proposal_id = str(params["proposal_id"]).strip()
    item_key = f"{ht}_id"
    _LOG.warning(
        "ha.config.helpers delete helper_type=%s %s=%s proposal=%s",
        ht,
        item_key,
        item_id,
        proposal_id,
    )
    try:
        await ha_ws_call(f"{ht}/delete", {item_key: item_id})
    except HAClientError as exc:
        return _to_error(exc)
    return {
        "ok": True,
        "helper_type": ht,
        item_key: item_id,
        "proposal_id": proposal_id,
    }


async def handle_ha_config_helpers(params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a helper config action."""
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
        return await _action_list(params)
    if action == "create":
        return await _action_create(params)
    if action == "update":
        return await _action_update(params)
    return await _action_delete(params)
