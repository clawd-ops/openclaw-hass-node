"""Interim fail-closed boundary for HA-native configuration mutations.

Proposal identifiers supplied by a caller are audit metadata, not proof of
approval. There is deliberately no bypass, token comparison, or approval
lookup here: the trusted approval verifier and human round-trip do not exist
yet. Re-enabling mutations requires that implementation, not a params flag.
"""

from __future__ import annotations

from typing import Any


def require_config_mutation_approval(command: str, action: str) -> dict[str, Any] | None:
    """Refuse a configuration mutation until trusted approval is implemented.

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
