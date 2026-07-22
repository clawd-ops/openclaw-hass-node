"""Home Assistant automation configuration commands (`ha.config.automation.*`).

HA-native path for editing automations. All commands talk to HA's REST API at
``/api/config/automation/config[/<id>]``. Callers are responsible for calling
``ha.call_service automation reload`` after a mutation when they want the
change to take effect without waiting for the next HA restart.

Commands:

- ``ha.config.automation.list`` — list configured automations.
- ``ha.config.automation.get`` — read a single automation config by id.
- ``ha.config.automation.save`` — write an automation config (proposal-gated).
- ``ha.config.automation.delete`` — delete an automation config (proposal-gated).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final

from openclaw_node.ha_client import HAClientError, ha_delete, ha_get, ha_post

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

# HA automation config ids are numeric timestamps by default and user-editable
# slugs otherwise. Restrict to a conservative charset so a caller cannot build
# a URL that escapes ``/api/config/automation/config/`` with ``..`` / ``/``.
_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")

_AUTOMATION_BASE: Final[str] = "/api/config/automation/config"


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _to_error(exc: HAClientError) -> dict[str, Any]:
    return _error(exc.code, exc.message)


def _require_proposal(params: dict[str, Any], command: str) -> dict[str, Any] | None:
    """Enforce proposal gating on mutating automation commands.

    Mirrors :mod:`openclaw_node.commands.ha_config_lovelace`: mutating calls
    must carry a non-empty ``proposal_id`` naming the agent-bridge proposal
    that authorised the change. ``"direct"`` is refused so operator audits can
    always trace the mutation back to a review record.
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


def _require_id(params: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Extract and validate an ``id`` param.

    Returns ``(id, None)`` on success or ``(None, error_dict)`` on failure.
    """
    raw = params.get("id")
    if not isinstance(raw, str):
        return None, _error("MISSING_PARAM", "id must be a string and is required")
    trimmed = raw.strip()
    if not trimmed:
        return None, _error("MISSING_PARAM", "id is required")
    if not _ID_PATTERN.match(trimmed):
        return None, _error(
            "INVALID_PARAM",
            f"id must match {_ID_PATTERN.pattern} (got {trimmed!r})",
        )
    return trimmed, None


async def handle_ha_config_automation_list(_params: dict[str, Any]) -> dict[str, Any]:
    """List all automations configured via HA's UI/REST store.

    Returns:
        ``{ok: True, count, automations}`` where ``automations`` is the raw
        list HA returned, or an error dict.
    """
    try:
        result = await ha_get(_AUTOMATION_BASE)
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(result, list):
        return _error("HA_BAD_RESPONSE", f"Expected list from GET {_AUTOMATION_BASE}")
    return {"ok": True, "count": len(result), "automations": result}


async def handle_ha_config_automation_get(params: dict[str, Any]) -> dict[str, Any]:
    """Return the raw configuration for a single automation.

    Params:
        id (str): Required automation id (the ``id`` field on the automation,
            not the ``entity_id``).

    Returns:
        ``{ok: True, id, config}`` where ``config`` is the raw dict HA
        returned, or an error dict.
    """
    automation_id, err = _require_id(params)
    if err is not None:
        return err
    assert automation_id is not None  # for type checker

    try:
        result = await ha_get(f"{_AUTOMATION_BASE}/{automation_id}")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(result, dict):
        return _error(
            "HA_BAD_RESPONSE",
            f"Expected dict from GET {_AUTOMATION_BASE}/<id>",
        )
    return {"ok": True, "id": automation_id, "config": result}


async def handle_ha_config_automation_save(params: dict[str, Any]) -> dict[str, Any]:
    """Save an automation configuration (proposal-gated).

    Params:
        id (str): Required automation id.
        config (dict): Required; the full automation body (``alias``,
            ``trigger``, ``condition``, ``action``, etc.).
        proposal_id (str): Required; agent-bridge proposal that authorised
            this change. ``"direct"`` is refused.

    Returns:
        ``{ok: True, id, proposal_id, result}`` on success, or an error dict.
        ``result`` is HA's raw response payload.
    """
    denied = _require_proposal(params, "ha.config.automation.save")
    if denied is not None:
        return denied

    automation_id, err = _require_id(params)
    if err is not None:
        return err
    assert automation_id is not None

    config = params.get("config")
    if not isinstance(config, dict):
        return _error("MISSING_PARAM", "config must be a dict and is required")

    proposal_id = str(params["proposal_id"]).strip()
    _LOG.warning(
        "ha.config.automation.save invoked id=%s proposal=%s",
        automation_id,
        proposal_id,
    )
    try:
        result = await ha_post(f"{_AUTOMATION_BASE}/{automation_id}", config)
    except HAClientError as exc:
        return _to_error(exc)
    return {
        "ok": True,
        "id": automation_id,
        "proposal_id": proposal_id,
        "result": result,
    }


async def handle_ha_config_automation_delete(params: dict[str, Any]) -> dict[str, Any]:
    """Delete an automation configuration (proposal-gated).

    Params:
        id (str): Required automation id.
        proposal_id (str): Required; agent-bridge proposal that authorised
            this mutation. ``"direct"`` is refused.

    Returns:
        ``{ok: True, id, proposal_id, result}`` on success, or an error dict.
    """
    denied = _require_proposal(params, "ha.config.automation.delete")
    if denied is not None:
        return denied

    automation_id, err = _require_id(params)
    if err is not None:
        return err
    assert automation_id is not None

    proposal_id = str(params["proposal_id"]).strip()
    _LOG.warning(
        "ha.config.automation.delete invoked id=%s proposal=%s",
        automation_id,
        proposal_id,
    )
    try:
        result = await ha_delete(f"{_AUTOMATION_BASE}/{automation_id}")
    except HAClientError as exc:
        return _to_error(exc)
    return {
        "ok": True,
        "id": automation_id,
        "proposal_id": proposal_id,
        "result": result,
    }
