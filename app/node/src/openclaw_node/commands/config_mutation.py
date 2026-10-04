"""Fail-closed boundary for HA-native configuration mutations.

Proposal identifiers supplied by a caller are audit metadata, not proof of
approval. Every mutation is refused with ``PROPOSAL_REQUIRED`` except those
that opt in to the native approval marker via :func:`consume_approval_marker`
(prototype: ``ha.config.automation`` save). The marker is minted by the gateway
plugin's ``before_tool_call`` hook only after an OpenClaw approval succeeds; see
``docs/design/AUTHORIZATION-MODEL.md`` for the trust model and its known gap.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Final

#: Reserved invoke param carrying the approval marker. Never reaches HA.
APPROVAL_PARAM: Final[str] = "_openclaw_approval"
_RESERVED: Final[frozenset[str]] = frozenset({APPROVAL_PARAM, "_openclaw_caller"})

# Marker id -> expiry (epoch seconds). Process-local: a node restart forgets ids,
# which is safe because markers live at most a few minutes and the expiry check
# still applies.
_USED_IDS: dict[str, int] = {}


def approval_bind(command: str, action: str, params: dict[str, Any]) -> str:
    """Digest binding an approval to one exact call.

    The plugin computes the same value: sha256 hex of canonical JSON (sorted
    keys, no whitespace, UTF-8, non-ASCII kept) of
    ``{command, action, params}`` with reserved fields removed from ``params``.
    """
    body = {k: v for k, v in params.items() if k not in _RESERVED}
    canonical = json.dumps(
        {"command": command, "action": action, "params": body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _refusal(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def consume_approval_marker(
    command: str, action: str, params: dict[str, Any]
) -> dict[str, Any] | None:
    """Verify and consume the approval marker; return ``None`` when it authorizes the call.

    Pops the reserved field from ``params`` on every path so it can never be
    forwarded. A missing marker yields ``PROPOSAL_REQUIRED`` (unchanged); a
    malformed, expired, mismatched, or already-used marker yields
    ``APPROVAL_INVALID``. Refusals happen before any Home Assistant request.
    """
    if APPROVAL_PARAM not in params:
        return require_config_mutation_approval(command, action)
    marker = params.pop(APPROVAL_PARAM)
    if not (
        isinstance(marker, dict)
        and set(marker) == {"id", "exp", "bind"}
        and isinstance(marker["id"], str)
        and marker["id"]
        and type(marker["exp"]) is int
        and isinstance(marker["bind"], str)
    ):
        return _refusal("APPROVAL_INVALID", "approval marker is malformed")
    now = time.time()
    for used_id, used_exp in list(_USED_IDS.items()):
        if used_exp < now:
            del _USED_IDS[used_id]
    if marker["exp"] < now:
        return _refusal("APPROVAL_INVALID", "approval marker has expired")
    if marker["bind"] != approval_bind(command, action, params):
        return _refusal("APPROVAL_INVALID", "approval marker does not match this call")
    if marker["id"] in _USED_IDS:
        return _refusal("APPROVAL_INVALID", "approval marker was already used")
    _USED_IDS[marker["id"]] = marker["exp"]
    return None


def require_config_mutation_approval(command: str, action: str) -> dict[str, Any] | None:
    """Refuse a configuration mutation that has no approval path.

    Args:
        command: Registered HA configuration command name.
        action: Validated action requested by the caller.

    Returns:
        A fail-closed error without performing any Home Assistant request.
    """
    return {
        "ok": False,
        "error": "PROPOSAL_REQUIRED",
        "message": (
            f"{command} action={action}: mutation unavailable until a trusted "
            "approval verifier is implemented; proposal_id alone is not authorization"
        ),
    }
