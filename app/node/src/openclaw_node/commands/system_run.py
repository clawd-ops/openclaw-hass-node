"""Execute a shell command bound to an approved OpenClaw exec plan.

Authorization for ``system.run`` is the native OpenClaw exec-approval
contract. The Gateway prepares a canonical ``systemRunPlan`` via
``system.run.prepare``, prompts an operator, and only forwards a
``system.run`` invoke after the plan is approved. The Gateway rejects
generic ``nodes.invoke system.run`` calls before they reach the node
and rejects a forward whose ``command``, ``rawCommand``, ``cwd``,
``agentId``, or ``sessionKey`` disagrees with the stored plan.

This handler therefore treats its params as the Gateway-forwarded
canonical plan. It fails closed if any field would have been rejected
at prepare time, re-validates the working directory against the node's
own allowed roots (defense in depth for the pre-run check the Gateway
performs against its cached roots), and executes with a minimal
environment.

A ``proposalId`` is accepted as audit metadata and never as
authorization. There is no add-on admin token: ``OPENCLAW_ADMIN_TOKEN``
was documented as inert (never surfaced by ``app/config.yaml``,
never exported by ``app/run.sh``) and has been removed.

Reference:
- ``docs/nodes/index.md`` (approval-bound-parameter contract, cwd re-validation)
- ``docs/gateway/protocol/operator-methods.md`` (``systemRunPlan`` forwarding)
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

_DEFAULT_TIMEOUT_S: Final[int] = 30
_DEFAULT_MAX_TIMEOUT_S: Final[int] = 60
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


def _max_timeout() -> int:
    try:
        return int(os.environ.get("OPENCLAW_RUN_TIMEOUT_MAX", _DEFAULT_MAX_TIMEOUT_S))
    except ValueError:
        return _DEFAULT_MAX_TIMEOUT_S


def default_timeout_s() -> int:
    """Return the default command timeout in seconds."""
    return _DEFAULT_TIMEOUT_S


def max_timeout_s() -> int:
    """Return the maximum permitted command timeout in seconds."""
    return _max_timeout()


def _validate_optional_identifier(value: Any, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return _error("INVALID_PARAM", f"{field} must be a non-empty string")
    return None


def handle_system_run(params: dict[str, Any]) -> dict[str, Any]:
    """Execute the Gateway-forwarded approved plan.

    Params:
        command (list[str]): Canonical argv the operator approved.
        cwd (str, optional): Working directory. Re-validated within the
            node's allowed roots.
        rawCommand (str, optional): Human-readable command text. Must
            match the argv canonicalisation, otherwise the run is
            refused as if the plan had mutated.
        env (dict[str, str], optional): Extra environment. Rejected if
            any key matches a credential pattern.
        timeout (int, optional): Seconds; defaults to
            :data:`_DEFAULT_TIMEOUT_S`, capped at
            ``OPENCLAW_RUN_TIMEOUT_MAX``.
        agentId (str, optional): Approval agent identifier. Audit only.
        sessionKey (str, optional): Approval session identifier. Audit only.
        proposalId (str, optional): Audit metadata. Never authorization.

    Returns:
        ``{ok: True, stdout, stderr, returncode, elapsed_ms}`` on success,
        or an error dict.
    """
    argv, error = validate_argv(params.get("command"))
    if error is not None:
        return error
    assert argv is not None

    resolved_text, error = resolve_command_text(argv, params.get("rawCommand"))
    if error is not None:
        return error
    assert resolved_text is not None

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

    for field in ("agentId", "sessionKey", "proposalId"):
        identifier_error = _validate_optional_identifier(params.get(field), field)
        if identifier_error is not None:
            return identifier_error

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

    raw_timeout = params.get("timeout", _DEFAULT_TIMEOUT_S)
    try:
        timeout_s = int(raw_timeout)
    except (TypeError, ValueError):
        return _error("INVALID_PARAM", f"timeout must be an integer, got {raw_timeout!r}")
    if timeout_s <= 0:
        return _error("INVALID_PARAM", "timeout must be positive")
    timeout_s = min(timeout_s, _max_timeout())

    _LOG.info(
        "system.run argv=%r cwd=%r timeout=%ds proposalId=%r",
        argv,
        resolved_cwd,
        timeout_s,
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
        return _error("TIMEOUT", f"Command timed out after {timeout_s}s")
    except FileNotFoundError:
        return _error("NOT_FOUND", f"Binary not found: {argv[0]!r}")
    except OSError as exc:
        return _error("EXEC_ERROR", f"Execution failed: {exc}")

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    stdout = result.stdout[:_MAX_OUTPUT_BYTES].decode(errors="replace")
    stderr = result.stderr[:_MAX_OUTPUT_BYTES].decode(errors="replace")

    _LOG.info(
        "system.run finished argv=%r rc=%d elapsed_ms=%d",
        argv,
        result.returncode,
        elapsed_ms,
    )
    return {
        "ok": True,
        "stdout": stdout,
        "stderr": stderr,
        "returncode": result.returncode,
        "elapsed_ms": elapsed_ms,
    }
