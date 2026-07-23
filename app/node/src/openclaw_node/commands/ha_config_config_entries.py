"""Home Assistant config_entries command (``ha.config.config_entries``).

WS: ``config_entries/get_single`` (single lookup) and
``config_entries/disable`` (both disable-with-``disabled_by="user"`` and
re-enable-with-``disabled_by=null``).

HA does *not* register a ``config_entries/enable`` frame — the
disable frame with ``disabled_by=null`` is the canonical enable path.
Options flows are exposed as HTTP flow views under
``/api/config/config_entries/options/flow/...``, not a registered
websocket command; a websocket-only options flow is not supported by
this handler yet.

Callers should cite a ``docs.lookup`` for the integration before
mutating (soft convention documented in COMMAND-SURFACE.md — the
handler does not hard-enforce a docs-lookup token, but every mutation
is proposal-gated).
"""

from __future__ import annotations

import logging
from typing import Any, Final

from openclaw_node.ha_client import HAClientError, ha_ws_call

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

_ACTIONS: Final[frozenset[str]] = frozenset({"get", "disable", "enable"})
_MUTATING_ACTIONS: Final[frozenset[str]] = frozenset({"disable", "enable"})


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _to_error(exc: HAClientError) -> dict[str, Any]:
    return _error(exc.code, exc.message)


def _require_proposal(params: dict[str, Any], action: str) -> dict[str, Any] | None:
    label = f"ha.config.config_entries action={action}"
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


def _require_entry_id(params: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    raw = params.get("entry_id")
    if not isinstance(raw, str):
        return None, _error("MISSING_PARAM", "entry_id must be a string and is required")
    trimmed = raw.strip()
    if not trimmed:
        return None, _error("MISSING_PARAM", "entry_id must be a non-empty string")
    return trimmed, None


async def handle_ha_config_config_entries(params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a config_entries action."""
    action = params.get("action")
    if not isinstance(action, str) or not action.strip():
        return _error("INVALID_PARAM", "action is required")
    action = action.strip()
    if action not in _ACTIONS:
        return _error(
            "INVALID_PARAM",
            f"action must be one of {sorted(_ACTIONS)}, got {action!r}",
        )

    entry_id, err = _require_entry_id(params)
    if err is not None:
        return err

    if action == "get":
        try:
            result = await ha_ws_call("config_entries/get_single", {"entry_id": entry_id})
        except HAClientError as exc:
            return _to_error(exc)
        if not isinstance(result, dict):
            return _error("HA_BAD_RESPONSE", "Expected dict from config_entries/get_single")
        return {"ok": True, "entry_id": entry_id, "entry": result}

    denied = _require_proposal(params, action)
    if denied is not None:
        return denied
    proposal_id = str(params["proposal_id"]).strip()

    # HA registers only config_entries/disable; enable is the same frame with
    # disabled_by=null (there is no separate config_entries/enable command).
    disabled_by = "user" if action == "disable" else None
    payload: dict[str, Any] = {"entry_id": entry_id, "disabled_by": disabled_by}
    _LOG.warning("ha.config.config_entries %s entry=%s proposal=%s", action, entry_id, proposal_id)
    try:
        result = await ha_ws_call("config_entries/disable", payload)
    except HAClientError as exc:
        return _to_error(exc)
    return {"ok": True, "entry_id": entry_id, "proposal_id": proposal_id, "result": result}
