"""Admin-gated shell command handler (P3.2.5 surface).

Implements ``system.run`` — execute a command with controlled environment,
timeout, and working directory.  Two authorisation paths are supported:

1. **Plan-bound path** (preferred): the gateway forwards an approved native
   ``systemRunPlan`` alongside the request.  Execution is gated on
   ``approved is True`` and on the executed argv matching the plan's argv;
   the gateway's prepare step is the validation boundary for argv/cwd.
2. **Legacy admin-token path**: callers supply ``admin_token`` matching
   ``OPENCLAW_ADMIN_TOKEN``.  Fail-closed when the env var is unset.  This
   path is preserved unchanged for backwards compatibility and will be
   removed once callers have migrated.

Environment sanitisation
------------------------
The subprocess inherits only a minimal base env (``PATH``, ``HOME``,
``LANG``, ``TZ``, ``USER``).  Caller-supplied ``env`` entries are merged
on top.  Env keys containing ``TOKEN``, ``SECRET``, ``KEY``, ``PASS``,
``CREDENTIAL``, or ``AUTH`` (case-insensitive) are rejected to prevent
accidental credential leakage back to the gateway.

Timeout
-------
``timeout`` defaults to 30 s and is hard-capped at ``OPENCLAW_RUN_TIMEOUT_MAX``
(default 60 s).  On the plan-bound path, ``timeoutMs`` (milliseconds)
takes precedence over ``timeout`` (seconds).  The process is killed on
timeout; ``TIMEOUT`` is returned.

Working directory
-----------------
On the plan-bound path, ``cwd`` comes from the approved plan
(``systemRunPlan.cwd``, falling back to top-level ``cwd`` if the plan
does not carry one); the gateway's prepare step is the validation
boundary.  The legacy path passes ``cwd`` through directly and is
unchanged.
"""

from __future__ import annotations

import hmac
import logging
import os
import subprocess
import time
from typing import Any, Final

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
    "PWD",  # avoid leaking $PWD shadowing
)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _base_env() -> dict[str, str]:
    """Return a minimal sanitised base environment."""
    env = os.environ
    return {k: env[k] for k in _SAFE_ENV_KEYS if k in env}


def _is_blocked_key(key: str) -> bool:
    upper = key.upper()
    return any(s in upper for s in _BLOCKED_KEY_SUBSTRINGS)


def _merge_env(caller_env: dict[str, str]) -> dict[str, str] | None:
    """Merge *caller_env* onto the safe base.  Returns None if any key is blocked."""
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
    """Return the default command timeout in seconds.

    Returns:
        The timeout applied when a caller supplies none.
    """
    return _DEFAULT_TIMEOUT_S


def max_timeout_s() -> int:
    """Return the maximum permitted command timeout in seconds.

    Returns:
        The ceiling, honouring ``OPENCLAW_RUN_TIMEOUT_MAX``.
    """
    return _max_timeout()


def _admin_token() -> str:
    """Return the configured admin token, or empty string if not set."""
    return os.environ.get("OPENCLAW_ADMIN_TOKEN", "")


def _is_plan_bound(params: dict[str, Any]) -> bool:
    """Return True iff a native ``systemRunPlan`` dict is present."""
    plan = params.get("systemRunPlan")
    return isinstance(plan, dict)


def _resolve_env(params: dict[str, Any]) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    """Resolve the merged env dict from *params*.

    Returns:
        (merged_env, error).  Exactly one of the two is non-None.
    """
    caller_env: dict[str, str] = {}
    raw_env = params.get("env")
    if raw_env is not None:
        if not isinstance(raw_env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in raw_env.items()
        ):
            return None, _error("INVALID_PARAM", "env must be a dict of string → string")
        caller_env = raw_env

    merged_env = _merge_env(caller_env)
    if merged_env is None:
        return None, _error(
            "INVALID_PARAM",
            "env contains a key matching a blocked pattern "
            "(TOKEN, SECRET, KEY, PASS, CREDENTIAL, AUTH, PWD)",
        )
    return merged_env, None


def _run_subprocess(
    argv: list[str],
    cwd: str | None,
    env: dict[str, str],
    timeout_s: int,
) -> dict[str, Any]:
    """Invoke *argv* and shape the response dict."""
    _LOG.info("system.run cmd=%r cwd=%r timeout=%ds", argv, cwd, timeout_s)
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            cwd=cwd,
            env=env,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _LOG.warning("system.run timed out after %dms cmd=%r", elapsed_ms, argv)
        return _error("TIMEOUT", f"Command timed out after {timeout_s}s")
    except FileNotFoundError:
        return _error("NOT_FOUND", f"Binary not found: {argv[0]!r}")
    except OSError as exc:
        return _error("EXEC_ERROR", f"Execution failed: {exc}")

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    stdout = result.stdout[:_MAX_OUTPUT_BYTES].decode(errors="replace")
    stderr = result.stderr[:_MAX_OUTPUT_BYTES].decode(errors="replace")

    _LOG.info(
        "system.run finished cmd=%r rc=%d elapsed_ms=%d",
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


def _handle_plan_bound(params: dict[str, Any]) -> dict[str, Any]:
    """Execute the plan-bound path.  Fail-closed on any authorisation gap."""
    plan = params["systemRunPlan"]
    assert isinstance(plan, dict)  # guaranteed by _is_plan_bound

    # Approval gate — must be a bool True, not truthy strings.
    approved = params.get("approved")
    if approved is not True:
        return _error("AUTHORIZATION_REQUIRED", "system.run plan not approved")

    argv = plan.get("argv")
    if not isinstance(argv, list) or len(argv) == 0 or not all(isinstance(a, str) for a in argv):
        return _error("INVALID_PARAM", "systemRunPlan.argv must be a non-empty list of strings")

    # Defense-in-depth: if the top-level command is present, it must match.
    top_cmd = params.get("command")
    if top_cmd is not None and (
        not isinstance(top_cmd, list)
        or not all(isinstance(a, str) for a in top_cmd)
        or list(top_cmd) != list(argv)
    ):
        return _error(
            "PLAN_MISMATCH",
            "top-level command argv drifted from systemRunPlan.argv",
        )

    # cwd: prefer plan.cwd, else top-level cwd.  No allowed_roots check here;
    # the gateway's prepare step validated it.
    raw_cwd = plan.get("cwd")
    if raw_cwd is None:
        raw_cwd = params.get("cwd")
    cwd: str | None = None if raw_cwd is None else str(raw_cwd)

    merged_env, env_err = _resolve_env(params)
    if env_err is not None:
        return env_err
    assert merged_env is not None

    # Timeout: prefer timeoutMs (ms) over timeout (s).
    max_t = _max_timeout()
    raw_ms = params.get("timeoutMs")
    if raw_ms is not None:
        try:
            ms = int(raw_ms)
        except (TypeError, ValueError):
            return _error("INVALID_PARAM", f"timeoutMs must be an integer, got {raw_ms!r}")
        if ms <= 0:
            return _error("INVALID_PARAM", "timeoutMs must be positive")
        timeout_s = max(1, (ms + 999) // 1000)
    else:
        raw_timeout = params.get("timeout", _DEFAULT_TIMEOUT_S)
        try:
            timeout_s = int(raw_timeout)
        except (TypeError, ValueError):
            return _error("INVALID_PARAM", f"timeout must be an integer, got {raw_timeout!r}")
        if timeout_s <= 0:
            return _error("INVALID_PARAM", "timeout must be positive")
    timeout_s = min(timeout_s, max_t)

    return _run_subprocess(list(argv), cwd, merged_env, timeout_s)


def handle_system_run(params: dict[str, Any]) -> dict[str, Any]:
    """Execute a command in a controlled environment.

    Two authorisation paths are supported:

    - **Plan-bound**: when ``systemRunPlan`` (dict) is present, execution
      is gated on ``approved is True`` and the plan's ``argv``.  No
      ``admin_token`` is required.  ``timeoutMs`` takes precedence over
      ``timeout`` on this path.
    - **Legacy**: when no ``systemRunPlan`` is supplied, the caller must
      pass ``admin_token`` matching ``OPENCLAW_ADMIN_TOKEN``.

    Returns:
        ``{ok: True, stdout, stderr, returncode, elapsed_ms}`` on success,
        or an error dict.
    """
    if _is_plan_bound(params):
        return _handle_plan_bound(params)

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

    caller_token = str(params.get("admin_token", ""))
    required_token = _admin_token()
    if not required_token:
        return _error(
            "ADMIN_REQUIRED",
            "system.run is disabled: OPENCLAW_ADMIN_TOKEN is not configured",
        )
    if not hmac.compare_digest(caller_token, required_token):
        return _error("ADMIN_REQUIRED", "Invalid or missing admin_token")

    cwd = params.get("cwd") or None
    if cwd is not None:
        cwd = str(cwd)

    merged_env, env_err = _resolve_env(params)
    if env_err is not None:
        return env_err
    assert merged_env is not None

    raw_timeout = params.get("timeout", _DEFAULT_TIMEOUT_S)
    try:
        timeout_s = int(raw_timeout)
    except (TypeError, ValueError):
        return _error("INVALID_PARAM", f"timeout must be an integer, got {raw_timeout!r}")
    max_t = _max_timeout()
    if timeout_s <= 0:
        return _error("INVALID_PARAM", "timeout must be positive")
    timeout_s = min(timeout_s, max_t)

    return _run_subprocess(list(cmd), cwd, merged_env, timeout_s)
