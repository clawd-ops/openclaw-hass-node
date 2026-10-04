"""Effect policy for service-bearing commands (pure; not wired to dispatch).

``check`` returns a structured refusal or ``None``. It decides before any
handler runs, so a refusal makes zero HA requests. The service denylist is the
single one in ``commands.ha``; the auto-allow set is the single constant in
``authz``. No approval is ever inferred from a parameter.

Principal x outcome:

- ``user``: deny/unclassified/forbidden refused; auto-allow allowed.
- ``admin``/``super_admin``: unclassified refused ``APPROVAL_REQUIRED``.
- ``operator``: only deny refused; unclassified allowed (temporary, logged).
"""

from __future__ import annotations

import logging
from typing import Any, Final, Literal

from openclaw_node.authz import (
    HOUSEHOLD_AUTO_ALLOW_SERVICES,
    is_forbidden,
    service_for_command,
)
from openclaw_node.caller import Caller
from openclaw_node.commands.ha import _INTERIM_DENIED_SERVICE_PATTERNS, _interim_service_denial

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

Effect = Literal["deny", "auto_allow", "unclassified"]

DENY: Final = _INTERIM_DENIED_SERVICE_PATTERNS
AUTO_ALLOW: Final = HOUSEHOLD_AUTO_ALLOW_SERVICES


def classify(service: str | None) -> Effect:
    """Classify a canonical ``domain.service``; malformed names are denied."""
    if service is None:
        return "deny"
    domain, _, name = service.partition(".")
    if _interim_service_denial(domain, name):
        return "deny"
    if service in AUTO_ALLOW:
        return "auto_allow"
    return "unclassified"


def _refusal(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def check(caller: Caller, command: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Return a refusal for this call, or ``None`` when it may proceed."""
    operator = caller.role == "operator"
    if not operator and is_forbidden(caller.forbidden, command, params):
        return _refusal("PERMISSION_DENIED", f"{command} is not permitted for this caller")
    if command != "ha.call_service" and service_for_command(command, params) is None:
        return None
    service = service_for_command(command, params)
    effect = classify(service)
    if effect == "deny":
        if operator and service is None:
            return None  # the handler reports its own validation error
        return _refusal("PERMISSION_DENIED", f"service {service or 'is malformed'} is denied")
    if effect == "auto_allow":
        return None
    if operator:
        _LOG.info("[authz] operator %s calls unclassified service %s", caller.actor_id, service)
        return None
    if caller.role == "user":
        return _refusal("PERMISSION_DENIED", f"service {service} is not permitted for this caller")
    return _refusal(
        "APPROVAL_REQUIRED",
        f"service {service} needs approval; native approval is not available in this build",
    )
