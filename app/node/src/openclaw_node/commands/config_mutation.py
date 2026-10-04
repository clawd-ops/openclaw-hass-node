"""Fail-closed boundary for HA-native configuration mutations.

Proposal identifiers supplied by a caller are audit metadata, not proof of
approval. Every mutation is refused with ``PROPOSAL_REQUIRED`` except those
that opt in to the native approval marker via :func:`consume_approval_marker`
(prototype: ``ha.config.automation`` save). The marker is minted by the gateway
plugin's ``before_tool_call`` hook when it requests approval; OpenClaw applies it
only after the approval succeeds. See
``docs/design/AUTHORIZATION-MODEL.md`` for the trust model and its known gap.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from decimal import Decimal
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
    keys, no whitespace, UTF-8, non-ASCII kept, numbers as ECMAScript prints
    them) of ``{command, action, params}`` with reserved fields removed from
    ``params``.
    """
    body = {k: v for k, v in params.items() if k not in _RESERVED}
    canonical = _canonical({"command": command, "action": action, "params": body})
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _es_number(value: float) -> str:
    """Format a float exactly like ECMAScript ``Number.prototype.toString``."""
    if not math.isfinite(value):
        return "null"  # JSON.stringify renders NaN and Infinity as null
    if value == 0:
        return "0"
    sign, raw_digits, exponent = Decimal(repr(value)).as_tuple()
    digits = "".join(map(str, raw_digits)).rstrip("0")
    k = len(digits)
    n = len(raw_digits) + int(exponent)  # decimal point position relative to digits
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * -n + digits
    else:
        tail = "." + digits[1:] if k > 1 else ""
        body = f"{digits[0]}{tail}e{'+' if n > 0 else '-'}{abs(n - 1)}"
    return ("-" if sign else "") + body


def _canonical(value: Any) -> str:
    """Canonical JSON: sorted keys, no whitespace, ES number formatting."""
    if isinstance(value, dict):
        items = ",".join(
            f"{json.dumps(k, ensure_ascii=False)}:{_canonical(value[k])}" for k in sorted(value)
        )
        return "{" + items + "}"
    if isinstance(value, list):
        return "[" + ",".join(_canonical(v) for v in value) + "]"
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    return _es_number(value)


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
