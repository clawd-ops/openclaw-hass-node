"""Home Assistant automation configuration command (``ha.config.automation``).

HA-native REST path for editing automations under
``/api/config/automation/config``. This command talks to HA's REST API
via :func:`openclaw_node.ha_client.ha_get`, :func:`ha_post`, and
:func:`ha_delete`; it MUST NOT fall back to any WebSocket frame.

Single command with an ``action`` param. Supported actions:

- ``get`` — read one automation by id.
- ``save`` — write one automation (proposal-gated).
- ``delete`` — delete one automation (proposal-gated).

HA core does not expose a collection route for automation configs; use
the existing ``ha.list_automations`` command to enumerate automations.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from openclaw_node.ha_client import HAClientError, ha_delete, ha_get, ha_post

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

_ACTIONS: Final[frozenset[str]] = frozenset({"get", "save", "delete"})


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _to_error(exc: HAClientError) -> dict[str, Any]:
    return _error(exc.code, exc.message)


def _require_proposal(params: dict[str, Any], action: str) -> dict[str, Any] | None:
    """Enforce proposal gating on mutating automation actions.

    Mirrors ``ha.config.lovelace``: mutating calls must carry an explicit
    ``proposal_id`` naming the agent-bridge proposal that authorised the
    change. ``"direct"`` is refused so operator-facing audits can always
    trace the mutation back to a review record.
    """
    label = f"ha.config.automation action={action}"
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


def _require_id(params: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Extract required ``id`` (automation id) from params; return ``(id, error)``."""
    raw = params.get("id")
    if not isinstance(raw, str):
        return None, _error("MISSING_PARAM", "id must be a string and is required")
    trimmed = raw.strip()
    if not trimmed:
        return None, _error("MISSING_PARAM", "id must be a non-empty string")
    return trimmed, None


async def _action_get(params: dict[str, Any]) -> dict[str, Any]:
    automation_id, err = _require_id(params)
    if err is not None:
        return err
    try:
        result = await ha_get(f"/api/config/automation/config/{automation_id}")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(result, dict):
        return _error(
            "HA_BAD_RESPONSE",
            "Expected dict from /api/config/automation/config/<id>",
        )
    return {"ok": True, "id": automation_id, "config": result}


async def _action_save(params: dict[str, Any]) -> dict[str, Any]:
    denied = _require_proposal(params, "save")
    if denied is not None:
        return denied

    automation_id, err = _require_id(params)
    if err is not None:
        return err

    config = params.get("config")
    if not isinstance(config, dict):
        return _error("MISSING_PARAM", "config must be a dict and is required")

    proposal_id = str(params["proposal_id"]).strip()
    _LOG.warning(
        "ha.config.automation save invoked id=%r proposal=%s",
        automation_id,
        proposal_id,
    )
    try:
        await ha_post(f"/api/config/automation/config/{automation_id}", config)
    except HAClientError as exc:
        return _to_error(exc)
    return {"ok": True, "id": automation_id, "proposal_id": proposal_id}


async def _action_delete(params: dict[str, Any]) -> dict[str, Any]:
    denied = _require_proposal(params, "delete")
    if denied is not None:
        return denied

    automation_id, err = _require_id(params)
    if err is not None:
        return err

    proposal_id = str(params["proposal_id"]).strip()
    _LOG.warning(
        "ha.config.automation delete invoked id=%r proposal=%s",
        automation_id,
        proposal_id,
    )
    try:
        await ha_delete(f"/api/config/automation/config/{automation_id}")
    except HAClientError as exc:
        return _to_error(exc)
    return {"ok": True, "id": automation_id, "proposal_id": proposal_id}


async def handle_ha_config_automation(params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch an automation config action.

    Params:
        action (str): Required; one of ``get``, ``save``, ``delete``.
            (Enumeration: use the existing ``ha.list_automations`` command;
            HA does not expose a collection-level automation config route.)
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
    return await _action_delete(params)
