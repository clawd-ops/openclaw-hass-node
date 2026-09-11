"""Execute a shell command bound to an approved OpenClaw exec plan.

Authorization for ``system.run`` is the native OpenClaw exec-approval
contract. The Gateway prepares a canonical ``systemRunPlan`` via
``system.run.prepare``, prompts an operator, and only forwards a
``system.run`` invoke after the plan is approved. The Gateway rejects
generic ``nodes.invoke system.run`` calls before they reach the node
and rejects a forward whose ``command``, ``rawCommand``, ``cwd``,
``agentId``, or ``sessionKey`` disagrees with the stored plan.

This handler is the node-side fail-closed gate. It refuses to execute
unless the forwarded request carries the authorization envelope the
Gateway attaches to an approved ``system.run`` invoke:

- ``systemRunPlan``: the authoritative stored plan. Missing plan is
  treated as an unauthorized direct invoke and refused.
- ``runId``: the approval/run correlation id.
- one of ``approved: true``, ``approvalDecision in {allow-once,
  allow-always}``, or a non-empty ``approvalSource`` string (the ask
  fallback / allowlist source label the Gateway attaches).

The forwarded ``command``, ``cwd``, ``rawCommand``, ``agentId``, and
``sessionKey`` are then cross-checked against ``systemRunPlan``; any
mismatch is refused rather than trusted because the wire arrived from
the Gateway. The working directory is re-validated against the node's
own allowed roots as defense in depth, and the subprocess inherits
only a minimal environment.

A ``proposalId`` is accepted as audit metadata and never as
authorization. There is no add-on admin token: ``OPENCLAW_ADMIN_TOKEN``
was documented as inert (never surfaced by ``app/config.yaml``,
never exported by ``app/run.sh``) and has been removed.

The successful result payload uses the native exec wire contract:
``success: bool``, ``exitCode: int | None``, ``timedOut: bool``,
``stdout``, ``stderr``, ``elapsed_ms``. The request timeout is read
from ``timeoutMs`` (milliseconds), matching the Gateway's forwarded
field name.

Reference:
- ``docs/nodes/index.md`` (approval-bound-parameter contract, cwd re-validation)
- ``docs/gateway/protocol/operator-methods.md`` (``systemRunPlan`` forwarding)
- ``docs/gateway/protocol/rpc-methods.md`` (``success``/``exitCode``/``timedOut`` wire contract)
- ``docs/design/AUTHORIZATION-MODEL.md`` (Class 3: shell)
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Any, Final

from openclaw_node.commands.exec_approvals import (
    resolve_command_text,
    validate_argv,
    validate_cwd,
    validate_env_shape,
)

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_MS: Final[int] = 30_000
_DEFAULT_MAX_TIMEOUT_MS: Final[int] = 60_000
_MAX_OUTPUT_BYTES: Final[int] = 256 * 1024  # 256 KiB per stream

_SAFE_ENV_KEYS: Final[frozenset[str]] = frozenset(
    ["PATH", "HOME", "LANG", "TZ", "USER", "TERM", "LOGNAME"]
)

_BLOCKED_KEY_SUBSTRINGS: Final[tuple[str, ...]] = (
    "TOKEN",
    "SECRET",
    "KEY",
    "PASS",
    "CREDENTIAL",
    "AUTH",
    "PWD",
)

_APPROVAL_DECISIONS: Final[frozenset[str]] = frozenset({"allow-once", "allow-always"})


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _base_env() -> dict[str, str]:
    env = os.environ
    return {k: env[k] for k in _SAFE_ENV_KEYS if k in env}


def _is_blocked_key(key: str) -> bool:
    upper = key.upper()
    return any(s in upper for s in _BLOCKED_KEY_SUBSTRINGS)


def _merge_env(caller_env: dict[str, str]) -> dict[str, str] | None:
    for key in caller_env:
        if _is_blocked_key(key):
            return None
    base = _base_env()
    base.update(caller_env)
    return base


def _max_timeout_ms() -> int:
    try:
        return int(os.environ.get("OPENCLAW_RUN_TIMEOUT_MAX_MS", _DEFAULT_MAX_TIMEOUT_MS))
    except ValueError:
        return _DEFAULT_MAX_TIMEOUT_MS


def default_timeout_ms() -> int:
    """Return the default command timeout in milliseconds."""
    return _DEFAULT_TIMEOUT_MS


def max_timeout_ms() -> int:
    """Return the maximum permitted command timeout in milliseconds."""
    return _max_timeout_ms()


def _validate_optional_identifier(value: Any, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return _error("INVALID_PARAM", f"{field} must be a non-empty string")
    return None


def _verify_authorization(params: dict[str, Any]) -> dict[str, Any] | None:
    """Refuse the run unless the Gateway attached an approval envelope.

    The Gateway always forwards ``system.run`` with a ``systemRunPlan``,
    a ``runId``, and one of ``approved: true`` / ``approvalDecision`` /
    ``approvalSource``. A payload missing every one of those signals is
    a direct invoke that never reached the exec approval path, and this
    handler refuses it as if the plan had mutated.
    """
    plan = params.get("systemRunPlan")
    if plan is None:
        return _error(
            "UNAUTHORIZED",
            "system.run requires the Gateway-forwarded systemRunPlan",
        )
    if not isinstance(plan, dict):
        return _error("INVALID_PARAM", "systemRunPlan must be an object")

    run_id = params.get("runId")
    if not isinstance(run_id, str) or not run_id.strip():
        return _error(
            "UNAUTHORIZED",
            "system.run requires a non-empty runId from the approved forward",
        )

    approved = params.get("approved") is True
    decision = params.get("approvalDecision")
    decision_ok = isinstance(decision, str) and decision in _APPROVAL_DECISIONS
    source = params.get("approvalSource")
    source_ok = isinstance(source, str) and bool(source.strip())
    if not (approved or decision_ok or source_ok):
        return _error(
            "UNAUTHORIZED",
            "system.run requires approved=true, approvalDecision, "
            "or approvalSource from the Gateway approval envelope",
        )
    return None


def _verify_plan_consistency(
    plan: dict[str, Any],
    argv: list[str],
    resolved_cwd: str | None,
    command_text: str,
    params: dict[str, Any],
) -> dict[str, Any] | None:
    """Verify the stored plan matches the forwarded canonical fields."""
    plan_argv = plan.get("argv")
    if not isinstance(plan_argv, list) or plan_argv != argv:
        return _error(
            "PLAN_MISMATCH",
            "command does not match the approved systemRunPlan argv",
        )
    plan_text = plan.get("commandText")
    if isinstance(plan_text, str) and plan_text != command_text:
        return _error(
            "PLAN_MISMATCH",
            "commandText does not match the approved systemRunPlan",
        )
    plan_cwd = plan.get("cwd")
    if plan_cwd is not None and plan_cwd != resolved_cwd:
        return _error(
            "PLAN_MISMATCH",
            "cwd does not match the approved systemRunPlan",
        )
    for field in ("agentId", "sessionKey"):
        plan_value = plan.get(field)
        if plan_value is None:
            continue
        if params.get(field) != plan_value:
            return _error(
                "PLAN_MISMATCH",
                f"{field} does not match the approved systemRunPlan",
            )
    return None


def _resolve_timeout_ms(params: dict[str, Any]) -> tuple[int | None, dict[str, Any] | None]:
    """Read the native ``timeoutMs`` field with the node's max as a ceiling."""
    if "timeout" in params and "timeoutMs" not in params:
        return None, _error(
            "INVALID_PARAM",
            "timeout is not accepted; use timeoutMs (native exec wire contract)",
        )
    raw = params.get("timeoutMs", _DEFAULT_TIMEOUT_MS)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None, _error(
            "INVALID_PARAM",
            f"timeoutMs must be an integer number of milliseconds, got {raw!r}",
        )
    if raw <= 0:
        return None, _error("INVALID_PARAM", "timeoutMs must be positive")
    return min(raw, _max_timeout_ms()), None


def handle_system_run(params: dict[str, Any]) -> dict[str, Any]:
    """Execute the Gateway-forwarded approved plan.

    Params (canonical, sent by the Gateway after operator approval):
        command (list[str]): Canonical argv the operator approved.
        rawCommand (str): Human-readable command text; must match the
            argv canonicalisation.
        systemRunPlan (dict): Authoritative stored plan (argv, commandText,
            cwd, agentId, sessionKey). Cross-checked against the forwarded
            fields; a mismatch fails closed.
        runId (str): Approval/run correlation id.
        approved (bool), approvalDecision (str), approvalSource (str):
            Approval envelope. At least one must be present.
        cwd (str, optional): Working directory. Re-validated within the
            node's allowed roots.
        env (dict[str, str], optional): Extra environment. Rejected if
            any key matches a credential pattern.
        timeoutMs (int, optional): Milliseconds; defaults to
            :data:`_DEFAULT_TIMEOUT_MS`, capped at
            ``OPENCLAW_RUN_TIMEOUT_MAX_MS``.
        agentId (str, optional), sessionKey (str, optional): Approval
            identifiers. Cross-checked against the plan.
        proposalId (str, optional): Audit metadata. Never authorization.

    Returns:
        On invocation failure (missing/invalid params, unauthorized,
        plan mismatch, subprocess spawn error), an
        ``{ok: False, error, message}`` dict that becomes a
        ``node.invoke.result`` error frame.

        On invocation success (the subprocess ran or hit its timeout),
        ``{ok: True, success, exitCode, timedOut, stdout, stderr,
        elapsed_ms}`` — the native ``system.run`` payload the exec tool
        parses.
    """
    argv, error = validate_argv(params.get("command"))
    if error is not None:
        return error
    assert argv is not None

    auth_error = _verify_authorization(params)
    if auth_error is not None:
        return auth_error
    plan = params["systemRunPlan"]
    assert isinstance(plan, dict)

    resolved_text, error = resolve_command_text(argv, params.get("rawCommand"))
    if error is not None:
        return error
    assert resolved_text is not None
    command_text = resolved_text[0]

    env_error = validate_env_shape(params.get("env"))
    if env_error is not None:
        return env_error

    resolved_cwd: str | None = None
    raw_cwd = params.get("cwd")
    if raw_cwd is not None:
        if not isinstance(raw_cwd, str) or not raw_cwd.strip():
            return _error("INVALID_PARAM", "cwd must be a non-empty string")
        resolved_cwd, error = validate_cwd(raw_cwd)
        if error is not None:
            return error

    for field in ("agentId", "sessionKey", "proposalId", "runId", "approvalSource"):
        identifier_error = _validate_optional_identifier(params.get(field), field)
        if identifier_error is not None:
            return identifier_error

    plan_error = _verify_plan_consistency(plan, argv, resolved_cwd, command_text, params)
    if plan_error is not None:
        return plan_error

    caller_env: dict[str, str] = {}
    raw_env = params.get("env")
    if raw_env is not None:
        caller_env = dict(raw_env)

    merged_env = _merge_env(caller_env)
    if merged_env is None:
        return _error(
            "INVALID_PARAM",
            "env contains a key matching a blocked pattern "
            "(TOKEN, SECRET, KEY, PASS, CREDENTIAL, AUTH, PWD)",
        )

    timeout_ms, timeout_error = _resolve_timeout_ms(params)
    if timeout_error is not None:
        return timeout_error
    assert timeout_ms is not None
    timeout_s = timeout_ms / 1000.0

    _LOG.info(
        "system.run argv=%r cwd=%r timeoutMs=%d runId=%r proposalId=%r",
        argv,
        resolved_cwd,
        timeout_ms,
        params.get("runId"),
        params.get("proposalId"),
    )
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            cwd=resolved_cwd,
            env=merged_env,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _LOG.warning("system.run timed out after %dms argv=%r", elapsed_ms, argv)
        return {
            "ok": True,
            "success": False,
            "exitCode": None,
            "timedOut": True,
            "stdout": "",
            "stderr": "",
            "error": f"Command timed out after {timeout_ms}ms",
            "elapsed_ms": elapsed_ms,
        }
    except FileNotFoundError:
        return _error("NOT_FOUND", f"Binary not found: {argv[0]!r}")
    except OSError as exc:
        return _error("EXEC_ERROR", f"Execution failed: {exc}")

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    stdout = result.stdout[:_MAX_OUTPUT_BYTES].decode(errors="replace")
    stderr = result.stderr[:_MAX_OUTPUT_BYTES].decode(errors="replace")

    _LOG.info(
        "system.run finished argv=%r exitCode=%d elapsed_ms=%d",
        argv,
        result.returncode,
        elapsed_ms,
    )
    return {
        "ok": True,
        "success": result.returncode == 0,
        "exitCode": result.returncode,
        "timedOut": False,
        "stdout": stdout,
        "stderr": stderr,
        "elapsed_ms": elapsed_ms,
    }
