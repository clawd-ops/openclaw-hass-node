"""Shared parameter-key validation for node commands.

A handler that reads keys with ``params.get`` silently ignores a misspelled
key and runs with defaults, which reads as a honoured request. Handlers call
:func:`strict_keys_error` first, before any filesystem or HA access.
"""

from __future__ import annotations

from typing import Any


def strict_keys_error(
    params: dict[str, Any],
    allowed: frozenset[str],
    nullable: frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    """Return an ``INVALID_PARAM`` error for an unknown key or an unusable null.

    Args:
        params: The caller-supplied parameter dict.
        allowed: Every key the handler accepts.
        nullable: Accepted keys for which an explicit ``null`` is a documented
            value. Any other accepted key that is present as ``None`` is refused.

    Returns:
        An error dict naming the offending key and, for an unknown key, the
        allowed set; ``None`` when every key is allowed and no null is misplaced.
    """
    unknown = sorted(set(params) - allowed)
    if unknown:
        return {
            "ok": False,
            "error": "INVALID_PARAM",
            "message": (
                f"unknown parameter(s): {', '.join(unknown)}; allowed: {', '.join(sorted(allowed))}"
            ),
        }
    for name in sorted(allowed - nullable):
        if name in params and params[name] is None:
            return {
                "ok": False,
                "error": "INVALID_PARAM",
                "message": f"{name} must not be null; omit it instead",
            }
    return None
