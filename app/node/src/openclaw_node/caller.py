"""Who is invoking a node command.

Pure data. Nothing here proves identity: a ``Caller`` is built by node-owned
code (the relay's turn registry, or an explicit operator call site), never from
a value the invoker supplied. The default is untrusted so a new call site that
forgets to pass one is restricted, not privileged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from openclaw_node.authz import TurnAuthz, forbidden_for_role
from openclaw_node.config import IdentityConfig

CallerRole = Literal["user", "admin", "super_admin", "operator"]


@dataclass(frozen=True)
class Caller:
    """Principal for one command invocation."""

    role: CallerRole
    actor_id: str
    forbidden: tuple[str, ...] = ()

    @classmethod
    def operator(cls, actor_id: str) -> Caller:
        """An authenticated operator invoke (no Assist context)."""
        return cls(role="operator", actor_id=actor_id)

    @classmethod
    def from_turn(cls, authz: TurnAuthz) -> Caller:
        """The principal of a resolved Assist turn, with its patched forbidden set."""
        actor_id = authz.actor.user_id if authz.actor else "<anonymous>"
        return cls(role=authz.role, actor_id=actor_id, forbidden=authz.forbidden)


UNTRUSTED: Final[Caller] = Caller(
    role="user",
    actor_id="<untrusted>",
    forbidden=forbidden_for_role(IdentityConfig(), "user"),
)
