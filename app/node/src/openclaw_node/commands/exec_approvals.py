"""Node-side exec approval participation.

OpenClaw gates ``system.run`` on node hosts through its own exec approval
flow rather than through any add-on-specific secret. To take part, a node
host must advertise three commands:

``system.run.prepare``
    Canonicalise a requested command into a stable plan. The Gateway sends
    that plan with ``exec.approval.request`` and, after approval, replays it
    as the authoritative command context. Because the plan is canonical and
    hashed, a caller cannot change the command between prepare and run.

``system.execApprovals.get`` / ``system.execApprovals.set``
    Read and write this node's exec approvals document so the policy is
    editable from the Gateway with ``openclaw approvals --node <id>``.

This module does not execute anything. It only canonicalises, validates,
and persists policy. Execution stays in :mod:`openclaw_node.commands.system_run`.

See ``docs/design/AUTHORIZATION-MODEL.md`` for why the previous
``OPENCLAW_ADMIN_TOKEN`` gate is being retired in favour of this path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Final

from openclaw_node.config import allowed_roots_for_env
from openclaw_node.safe_path import NoAllowedRootsError, OutOfBoundsError, resolve_safe

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

APPROVALS_DOC_VERSION: Final[int] = 1
"""Schema version written to the approvals document."""

_VALID_SECURITY: Final[frozenset[str]] = frozenset({"deny", "allowlist", "full"})
_VALID_ASK: Final[frozenset[str]] = frozenset({"off", "on-miss", "always"})
_VALID_ASK_FALLBACK: Final[frozenset[str]] = frozenset({"deny", "allowlist", "full"})

_DEFAULT_POLICY: Final[dict[str, Any]] = {
    "security": "deny",
    "ask": "on-miss",
    "askFallback": "deny",
}

_MAX_ARGV: Final[int] = 256
_MAX_ARG_LEN: Final[int] = 8192


def _error(code: str, message: str) -> dict[str, Any]:
    """Build the node's standard error envelope.

    Args:
        code: Stable machine-readable error code.
        message: Human-readable explanation.

    Returns:
        An error dict with ``ok`` false.
    """
    return {"ok": False, "error": {"code": code, "message": message}}


def _approvals_path() -> Path:
    """Return the on-disk location of the exec approvals document.

    Returns:
        Path to ``exec-approvals.json`` inside the node data directory.
    """
    from openclaw_node.config import load_config

    return load_config().data_dir / "exec-approvals.json"


def _validate_cwd(cwd: str) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve *cwd* beneath an allowed root.

    ``system.run`` historically documented this restriction without enforcing
    it. Validation happens here so an approved plan can never carry a working
    directory outside the node's allowed roots.

    Args:
        cwd: Caller-supplied working directory.

    Returns:
        A tuple of the resolved directory and ``None``, or ``None`` and an
        error dict when the directory is not acceptable.
    """
    try:
        resolved = resolve_safe(cwd, allowed_roots_for_env())
    except NoAllowedRootsError:
        return None, _error("NO_ALLOWED_ROOTS", "No filesystem roots configured")
    except OutOfBoundsError:
        return None, _error("PATH_NOT_ALLOWED", f"cwd is outside the allowed roots: {cwd!r}")
    if not resolved.is_dir():
        return None, _error("INVALID_PARAM", f"cwd is not a directory: {cwd!r}")
    return str(resolved), None


def _plan_hash(argv: list[str], cwd: str | None, timeout_s: int) -> str:
    """Hash the canonical fields that an approval is bound to.

    Args:
        argv: Canonical argument vector.
        cwd: Resolved working directory, or ``None``.
        timeout_s: Effective timeout in seconds.

    Returns:
        A ``sha256:`` prefixed hex digest over the canonical plan fields.
    """
    payload = json.dumps(
        {"argv": argv, "cwd": cwd, "timeoutSeconds": timeout_s},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def handle_system_run_prepare(params: dict[str, Any]) -> dict[str, Any]:
    """Canonicalise a ``system.run`` request into an approvable plan.

    This performs every validation ``system.run`` performs, except the
    execution itself, so an operator approves exactly what would run. It
    never starts a process.

    Params:
        cmd (list[str]): Command and arguments. Shell strings are rejected.
        cwd (str, optional): Working directory. Must resolve beneath an
            allowed root.
        timeout (int, optional): Seconds. Clamped to the node maximum.

    Returns:
        ``{ok: True, systemRunPlan: {...}}`` where the plan carries ``argv``,
        ``rawCommand``, ``cwd``, ``timeoutSeconds``, and ``planHash``, or an
        error dict when the request is not valid.
    """
    from openclaw_node.commands.system_run import default_timeout_s, max_timeout_s

    cmd = params.get("cmd")
    if not cmd:
        return _error("MISSING_PARAM", "cmd is required")
    if isinstance(cmd, str):
        return _error(
            "INVALID_PARAM",
            "cmd must be a list of strings; shell strings are rejected to prevent injection",
        )
    if not isinstance(cmd, list) or not all(isinstance(a, str) for a in cmd):
        return _error("INVALID_PARAM", "cmd must be a list of strings")
    if not cmd[0]:
        return _error("INVALID_PARAM", "cmd[0] must be a non-empty executable name")
    if len(cmd) > _MAX_ARGV:
        return _error("INVALID_PARAM", f"cmd has more than {_MAX_ARGV} arguments")
    if any(len(a) > _MAX_ARG_LEN for a in cmd):
        return _error("INVALID_PARAM", f"cmd contains an argument longer than {_MAX_ARG_LEN}")
    if any("\x00" in a for a in cmd):
        return _error("INVALID_PARAM", "cmd arguments must not contain NUL bytes")

    resolved_cwd: str | None = None
    raw_cwd = params.get("cwd") or None
    if raw_cwd is not None:
        resolved_cwd, err = _validate_cwd(str(raw_cwd))
        if err is not None:
            return err

    raw_timeout = params.get("timeout", default_timeout_s())
    try:
        timeout_s = int(raw_timeout)
    except (TypeError, ValueError):
        return _error("INVALID_PARAM", f"timeout must be an integer, got {raw_timeout!r}")
    if timeout_s <= 0:
        return _error("INVALID_PARAM", "timeout must be positive")
    timeout_s = min(timeout_s, max_timeout_s())

    argv = list(cmd)
    plan = {
        "argv": argv,
        "rawCommand": " ".join(argv),
        "cwd": resolved_cwd,
        "timeoutSeconds": timeout_s,
        "planHash": _plan_hash(argv, resolved_cwd, timeout_s),
    }
    _LOG.info(
        "system.run.prepare argv0=%r argc=%d cwd=%r timeout=%ds hash=%s",
        argv[0],
        len(argv),
        resolved_cwd,
        timeout_s,
        plan["planHash"],
    )
    return {"ok": True, "systemRunPlan": plan}


def _default_document() -> dict[str, Any]:
    """Return a fail-closed approvals document.

    Returns:
        A document denying exec until an operator configures otherwise.
    """
    return {"version": APPROVALS_DOC_VERSION, "defaults": dict(_DEFAULT_POLICY), "agents": {}}


def handle_system_exec_approvals_get(_params: dict[str, Any]) -> dict[str, Any]:
    """Read this node's exec approvals document.

    A missing or unreadable document is reported as the fail-closed default
    rather than as an error, so an operator can always see the effective
    policy and write a corrected one.

    Params:
        None.

    Returns:
        ``{ok: True, approvals: {...}, path: str, exists: bool}``.
    """
    path = _approvals_path()
    if not path.exists():
        return {"ok": True, "approvals": _default_document(), "path": str(path), "exists": False}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _LOG.warning("exec approvals document unreadable at %s: %s", path, exc)
        return {
            "ok": True,
            "approvals": _default_document(),
            "path": str(path),
            "exists": True,
            "unreadable": True,
        }
    if not isinstance(raw, dict):
        return {
            "ok": True,
            "approvals": _default_document(),
            "path": str(path),
            "exists": True,
            "unreadable": True,
        }
    return {"ok": True, "approvals": raw, "path": str(path), "exists": True}


def _validate_policy(scope: str, policy: dict[str, Any]) -> dict[str, Any] | None:
    """Validate one policy block.

    Args:
        scope: Label used in error messages, such as ``defaults``.
        policy: Policy mapping to check.

    Returns:
        An error dict, or ``None`` when the policy is acceptable.
    """
    security = policy.get("security", _DEFAULT_POLICY["security"])
    ask = policy.get("ask", _DEFAULT_POLICY["ask"])
    fallback = policy.get("askFallback", _DEFAULT_POLICY["askFallback"])
    if security not in _VALID_SECURITY:
        return _error("INVALID_PARAM", f"{scope}.security must be one of {sorted(_VALID_SECURITY)}")
    if ask not in _VALID_ASK:
        return _error("INVALID_PARAM", f"{scope}.ask must be one of {sorted(_VALID_ASK)}")
    if fallback not in _VALID_ASK_FALLBACK:
        return _error(
            "INVALID_PARAM", f"{scope}.askFallback must be one of {sorted(_VALID_ASK_FALLBACK)}"
        )
    allowlist = policy.get("allowlist")
    if allowlist is not None:
        if not isinstance(allowlist, list):
            return _error("INVALID_PARAM", f"{scope}.allowlist must be a list")
        for entry in allowlist:
            if not isinstance(entry, dict) or not isinstance(entry.get("pattern"), str):
                return _error(
                    "INVALID_PARAM", f"{scope}.allowlist entries require a string pattern"
                )
    return None


def handle_system_exec_approvals_set(params: dict[str, Any]) -> dict[str, Any]:
    """Replace this node's exec approvals document.

    The document is validated before it is written, so a malformed payload
    cannot leave the node without an enforceable policy. The write is atomic
    and the file is created with owner-only permissions.

    Params:
        approvals (dict): Full replacement document. Must contain a
            ``defaults`` policy block; ``agents`` is optional.

    Returns:
        ``{ok: True, path: str}`` or an error dict.
    """
    approvals = params.get("approvals")
    if not isinstance(approvals, dict):
        return _error("INVALID_PARAM", "approvals must be an object")

    defaults = approvals.get("defaults")
    if not isinstance(defaults, dict):
        return _error("INVALID_PARAM", "approvals.defaults is required and must be an object")
    err = _validate_policy("defaults", defaults)
    if err is not None:
        return err

    agents = approvals.get("agents", {})
    if not isinstance(agents, dict):
        return _error("INVALID_PARAM", "approvals.agents must be an object")
    for agent_id, policy in agents.items():
        if not isinstance(policy, dict):
            return _error("INVALID_PARAM", f"approvals.agents.{agent_id} must be an object")
        err = _validate_policy(f"agents.{agent_id}", policy)
        if err is not None:
            return err

    document = dict(approvals)
    document["version"] = APPROVALS_DOC_VERSION

    path = _approvals_path()
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(document, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            pass
        os.replace(tmp, path)
    except OSError as exc:
        _LOG.warning("failed to write exec approvals document at %s: %s", path, exc)
        return _error("IO_ERROR", f"Could not write approvals document: {exc}")

    _LOG.warning("exec approvals document replaced at %s", path)
    return {"ok": True, "path": str(path)}
