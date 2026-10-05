"""Who needs native approval for a gated mutation.

The rule table lives in ``contracts/approval-gated-commands.json`` (shared with
the plugin tests). A gated call without a valid approval marker is refused
``PROPOSAL_REQUIRED`` unless :func:`approval_exempt` says this resolved caller
may run it unprompted. The caller is node-owned (the Assist turn registry), never
a value the invoker supplied; with no resolved caller nothing is exempt.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Final

from openclaw_node.caller import Caller

ActionTable = dict[str, frozenset[str]]

#: Always needs approval, for every caller.
DESTRUCTIVE: Final[ActionTable] = {
    "ha.config.automation": frozenset({"delete"}),
    "ha.config.script": frozenset({"delete"}),
    "ha.config.scene": frozenset({"delete"}),
    "ha.config.helpers": frozenset({"delete"}),
    "ha.config.area_registry": frozenset({"delete"}),
    "ha.config.entity_registry": frozenset({"remove"}),
    "ha.config.config_entries": frozenset({"disable"}),
    "fs.delete": frozenset({""}),
    "fs.move": frozenset({""}),
    "fs.restore": frozenset({""}),
    "ha.update_install": frozenset({""}),
}

#: No approval for a verified HA admin or super_admin in a live Assist turn.
USER_DIRECTED: Final[ActionTable] = {
    "ha.config.automation": frozenset({"save"}),
    "ha.config.script": frozenset({"save"}),
    "ha.config.scene": frozenset({"save"}),
    "ha.config.helpers": frozenset({"create", "update"}),
    "ha.config.area_registry": frozenset({"create", "update"}),
    "ha.config.device_registry": frozenset({"update"}),
    "ha.config.entity_registry": frozenset({"update"}),
    "ha.config.config_entries": frozenset({"enable"}),
    "ha.config.lovelace": frozenset({"save", "resources_create"}),
}

#: Add-on lifecycle commands each role may run with no approval from an Assist turn.
LIFECYCLE_NO_APPROVAL: Final[dict[str, ActionTable]] = {
    "admin": {
        "ha.addon_start": frozenset({""}),
        "ha.addon_restart": frozenset({""}),
    },
    "super_admin": {
        "ha.addon_start": frozenset({""}),
        "ha.addon_stop": frozenset({""}),
        "ha.addon_restart": frozenset({""}),
    },
}

#: The caller of the call being dispatched; set by the dispatcher around the handler.
CURRENT_CALLER: ContextVar[Caller | None] = ContextVar("current_caller", default=None)


def _listed(table: ActionTable, command: str, action: str) -> bool:
    return action in table.get(command, frozenset())


def approval_exempt(caller: Caller | None, command: str, action: str) -> bool:
    """Return whether ``caller`` may run this gated call with no approval marker.

    Destructive pairs appear in neither exemption table (the tests assert it), so
    they are never exempt.
    """
    if caller is None or caller.role not in LIFECYCLE_NO_APPROVAL:
        return False
    if caller.actor_id == "<anonymous>":
        return False
    return _listed(USER_DIRECTED, command, action) or _listed(
        LIFECYCLE_NO_APPROVAL[caller.role], command, action
    )
