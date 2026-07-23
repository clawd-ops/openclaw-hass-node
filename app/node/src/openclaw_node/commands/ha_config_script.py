"""Home Assistant script configuration command (``ha.config.script``).

HA-native REST path for per-id script config under
``/api/config/script/config/<id>``. This command talks to HA's REST
API via :func:`openclaw_node.ha_client.ha_get`, :func:`ha_post`, and
:func:`ha_delete`; it MUST NOT fall back to any WebSocket frame.

Single command with an ``action`` param. Supported actions:

- ``get`` — read one script by id.
- ``save`` — write one script (proposal-gated).
- ``delete`` — delete one script (proposal-gated).

HA core does not expose a collection route for script configs; enumerate
via state (``script.*`` entities from ``ha.list_states``).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final

from openclaw_node.ha_client import HAClientError, ha_delete, ha_get, ha_post

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

_ACTIONS: Final[frozenset[str]] = frozenset({"get", "save", "delete"})


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _to_error(exc: HAClientError) -> dict[str, Any]:
    return _error(exc.code, exc.message)


def _require_proposal(params: dict[str, Any], action: str) -> dict[str, Any] | None:
    """Enforce proposal gating on mutating script actions.

    Mirrors ``ha.config.automation``: mutating calls must carry an explicit
    ``proposal_id`` naming the agent-bridge proposal that authorised the
    change. ``"direct"`` is refused so operator-facing audits can always
    trace the mutation back to a review record.
    """
    label = f"ha.config.script action={action}"
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


_ID_RE: Final = re.compile(r"^[a-z0-9_]+$")


def _require_id(params: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Extract required ``id`` (script id) from params; return ``(id, error)``.

    HA registers the script config endpoint with ``cv.slug`` validation on
    the path key: ``id`` must match ``^[a-z0-9_]+$`` (lowercase letters,
    digits, underscores). Reject anything that would produce a malformed
    URL or hit a different resource than intended (path separators,
    reserved characters, whitespace, etc.).
    """
    raw = params.get("id")
    if not isinstance(raw, str):
        return None, _error("MISSING_PARAM", "id must be a string and is required")
    trimmed = raw.strip()
    if not trimmed:
        return None, _error("MISSING_PARAM", "id must be a non-empty string")
    if not _ID_RE.match(trimmed):
        return None, _error(
            "INVALID_PARAM",
            "id must be an HA slug (lowercase letters, digits, underscores)",
        )
    return trimmed, None


async def _action_get(params: dict[str, Any]) -> dict[str, Any]:
    script_id, err = _require_id(params)
    if err is not None:
        return err
    try:
        result = await ha_get(f"/api/config/script/config/{script_id}")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(result, dict):
        return _error(
            "HA_BAD_RESPONSE",
            "Expected dict from /api/config/script/config/<id>",
        )
    return {"ok": True, "id": script_id, "config": result}


async def _action_save(params: dict[str, Any]) -> dict[str, Any]:
    denied = _require_proposal(params, "save")
    if denied is not None:
        return denied

    script_id, err = _require_id(params)
    if err is not None:
        return err

    config = params.get("config")
    if not isinstance(config, dict):
        return _error("MISSING_PARAM", "config must be a dict and is required")

    proposal_id = str(params["proposal_id"]).strip()
    _LOG.warning(
        "ha.config.script save invoked id=%r proposal=%s",
        script_id,
        proposal_id,
    )
    try:
        await ha_post(f"/api/config/script/config/{script_id}", config)
    except HAClientError as exc:
        return _to_error(exc)
    return {"ok": True, "id": script_id, "proposal_id": proposal_id}


async def _action_delete(params: dict[str, Any]) -> dict[str, Any]:
    denied = _require_proposal(params, "delete")
    if denied is not None:
        return denied

    script_id, err = _require_id(params)
    if err is not None:
        return err

    proposal_id = str(params["proposal_id"]).strip()
    _LOG.warning(
        "ha.config.script delete invoked id=%r proposal=%s",
        script_id,
        proposal_id,
    )
    try:
        await ha_delete(f"/api/config/script/config/{script_id}")
    except HAClientError as exc:
        return _to_error(exc)
    return {"ok": True, "id": script_id, "proposal_id": proposal_id}


async def handle_ha_config_script(params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a script config action.

    Params:
        action (str): Required; one of ``get``, ``save``, ``delete``.
            (Enumeration: read ``script.*`` entities from state via
            ``ha.list_states``; HA does not expose a collection-level
            script config route.)
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
