"""Rules for the ``ha.ws`` / ``ha.rest`` pass-through: kinds, pattern table, roles.

One small table decides what a request *is* (its kind); one small table decides
who may do each kind. Nothing here is per-command. Everything is pure data plus
two lookups, so the whole policy reads top to bottom in this file.

Kinds: ``control`` (lights, scenes, media), ``diagnose`` (reads), ``fix``
(restart an add-on, reload an integration, restart Core), ``configure`` (edit
automations, registries, dashboards) and ``admin`` (delete, anything unknown).

A request is described by a target string and matched against :data:`RULES`,
first match wins:

- ``ws <type>``            a WebSocket request, e.g. ``ws config/entity_registry/list``
- ``svc <domain>.<service>``  a service call, whether over WS or REST
- ``<METHOD> <path>``      a REST request, e.g. ``GET /api/states/light.kitchen``

Within a pattern, ``*`` matches exactly one segment (segments split on ``/``,
or on ``.`` for ``svc``), glob characters work inside a segment, and a final
``**`` matches any remaining segments. No match means ``admin``.

Items marked hard are always ``admin`` and always need the owner's explicit
approval of the job that carries them, whoever asks; no role allowance covers
them (see :func:`decide`).
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Final, Literal

from openclaw_node.authz import HOUSEHOLD_ALLOWED_SERVICES
from openclaw_node.caller import Caller

RULES_VERSION: Final = 1

Kind = Literal["control", "diagnose", "fix", "configure", "admin"]
Decision = Literal["allow", "approval", "refuse"]
PolicyRole = Literal["owner", "household", "operator", "guest"]

# Services that run an arbitrary configured action sequence (scripts, buttons wired to
# one, automation triggers). Always admin; scenes only set states, so they stay control.
INDIRECT_EXECUTORS: Final[tuple[str, ...]] = (
    "svc script.*",
    "svc button.press",
    "svc automation.trigger",
)
_EXECUTOR_DOMAINS: Final[frozenset[str]] = frozenset(
    pattern.split(" ")[1].split(".")[0] for pattern in INDIRECT_EXECUTORS
)

# (pattern, kind, hard). Order matters: hard-deny first, then specific rules,
# then the broad read rules. Anything not matched is ``admin``.
RULES: Final[tuple[tuple[str, Kind, bool], ...]] = (
    # Hard-deny: updates, host power, backup deletion, users/auth, Supervisor tunnels.
    ("ws supervisor/**", "admin", True),
    ("ws hassio/**", "admin", True),
    ("ws auth/**", "admin", True),
    ("ws config/auth*/**", "admin", True),
    ("ws backup/remove", "admin", True),
    ("ws backup/delete*", "admin", True),
    ("svc update.install", "admin", True),
    ("svc hassio.*_update", "admin", True),
    ("svc hassio.host_reboot", "admin", True),
    ("svc hassio.host_shutdown", "admin", True),
    # Fix: restart or reload something that is failing. Never an update.
    ("svc homeassistant.restart", "fix", False),
    ("svc homeassistant.reload_*", "fix", False),
    ("svc homeassistant.update_entity", "fix", False),
    ("svc hassio.addon_start", "fix", False),
    ("svc hassio.addon_restart", "fix", False),
    ("svc hassio.app_start", "fix", False),
    ("svc hassio.app_restart", "fix", False),
    ("svc *.reload", "fix", False),
    ("POST /api/config/config_entries/entry/*/reload", "fix", False),
    # Admin: services that run a user-defined action sequence. What a script or
    # automation does is not visible here, so it could reach any privileged
    # service. They are listed before Control so no wildcard or table can claim them.
    *((pattern, "admin", False) for pattern in INDIRECT_EXECUTORS),
    # Diagnose: reads.
    ("svc homeassistant.check_config", "diagnose", False),
    ("POST /api/config/core/check_config", "diagnose", False),
    ("POST /api/template", "diagnose", False),
    ("GET /api/**", "diagnose", False),
    ("ws get_*", "diagnose", False),
    ("ws ping", "diagnose", False),
    ("ws render_template", "diagnose", False),
    ("ws */list", "diagnose", False),
    ("ws */*/list", "diagnose", False),
    ("ws */get", "diagnose", False),
    ("ws */info", "diagnose", False),
    ("ws config/*/get*", "diagnose", False),
    ("ws config/config_entries/entry", "diagnose", False),
    ("ws logbook/get_events", "diagnose", False),
    ("ws history/*", "diagnose", False),
    ("ws recorder/statistics_during_period", "diagnose", False),
    ("ws recorder/list_statistic_ids", "diagnose", False),
    ("ws recorder/get_statistics_metadata", "diagnose", False),
    ("ws lovelace/config", "diagnose", False),
    ("ws lovelace/resources", "diagnose", False),
    ("ws repairs/list_issues", "diagnose", False),
    ("ws trace/*", "diagnose", False),
    ("ws search/related", "diagnose", False),
    ("ws entity/source", "diagnose", False),
    ("ws zwave_js/get_*", "diagnose", False),
    ("ws energy/get_prefs", "diagnose", False),
    ("ws energy/validate", "diagnose", False),
    # Configure: change how the house is set up.
    ("svc hassio.addon_stop", "configure", False),
    ("svc hassio.app_stop", "configure", False),
    ("svc hassio.backup_*", "configure", False),
    ("POST /api/config/*/config/*", "configure", False),
    ("ws config/*/create", "configure", False),
    ("ws config/*/update", "configure", False),
    ("ws */create", "configure", False),
    ("ws */update", "configure", False),
    ("ws lovelace/config/save", "configure", False),
    ("ws lovelace/resources/create", "configure", False),
    ("ws lovelace/resources/update", "configure", False),
    ("ws lovelace/dashboards/create", "configure", False),
    ("ws lovelace/dashboards/update", "configure", False),
    ("ws zwave_js/set_config_parameter", "configure", False),
    ("ws energy/save_prefs", "configure", False),
    # Control: the everyday-control services, from the one table in ``authz``.
    *(
        (f"svc {domain}.{service}", "control", False)
        for domain, services in HOUSEHOLD_ALLOWED_SERVICES.items()
        for service in sorted(services)
        if domain not in _EXECUTOR_DOMAINS
    ),
)

# What each role may do per kind. ``approval`` means the owner must approve the
# job (see ``commands.ha_passthrough``); ``refuse`` is final.
#
# - owner: the HA super admin in a verified Assist turn.
# - household: any other verified HA user in a verified Assist turn.
# - operator: a call that arrives with no verified person (agent sessions, the
#   local API). Which human is behind it is unknown, so it gets household
#   power plus the approval route; the owner's approval is the identity proof.
# - guest: an Assist turn with no verified actor, or no caller at all.
ROLE_KINDS: Final[dict[PolicyRole, dict[Kind, Decision]]] = {
    "owner": {
        "control": "allow",
        "diagnose": "allow",
        "fix": "allow",
        "configure": "allow",
        "admin": "approval",
    },
    "operator": {
        "control": "allow",
        "diagnose": "allow",
        "fix": "allow",
        "configure": "approval",
        "admin": "approval",
    },
    "household": {
        "control": "allow",
        "diagnose": "allow",
        "fix": "allow",
        "configure": "refuse",
        "admin": "refuse",
    },
    "guest": {
        "control": "allow",
        "diagnose": "allow",
        "fix": "refuse",
        "configure": "refuse",
        "admin": "refuse",
    },
}

_UNVERIFIED_ACTORS: Final[frozenset[str]] = frozenset({"<anonymous>", "<untrusted>"})


@dataclass(frozen=True)
class Classification:
    """What a request is: its kind, whether it is hard-deny, and the rule that said so."""

    kind: Kind
    hard: bool
    rule: str


def _segments(text: str, sep: str) -> list[str]:
    return text.strip(sep).split(sep)


def _matches(pattern: str, target: str) -> bool:
    p_head, _, p_rest = pattern.partition(" ")
    t_head, _, t_rest = target.partition(" ")
    if not fnmatchcase(t_head, p_head):
        return False
    sep = "." if t_head == "svc" else "/"
    p, t = _segments(p_rest, sep), _segments(t_rest, sep)
    if p[-1] == "**":
        return len(t) >= len(p) - 1 and all(
            fnmatchcase(a, b) for a, b in zip(t, p[:-1], strict=False)
        )
    return len(t) == len(p) and all(fnmatchcase(a, b) for a, b in zip(t, p, strict=True))


def classify(target: str) -> Classification:
    """Classify a request target; no match is ``admin``."""
    for pattern, kind, hard in RULES:
        if _matches(pattern, target):
            return Classification(kind, hard, pattern)
    return Classification("admin", False, "unknown")


def policy_role(caller: Caller | None) -> PolicyRole:
    """Map the node-owned caller to a policy role; anything unverified is least privilege."""
    if caller is None:
        return "guest"
    if caller.role == "operator":
        return "operator"
    if caller.actor_id in _UNVERIFIED_ACTORS:
        return "guest"
    return "owner" if caller.role == "super_admin" else "household"


def decide(role: PolicyRole, classification: Classification) -> Decision:
    """The role's decision for a classified request. Hard-deny never resolves to ``allow``."""
    decision = ROLE_KINDS[role][classification.kind]
    if classification.hard and decision == "allow":
        return "approval"
    return decision
