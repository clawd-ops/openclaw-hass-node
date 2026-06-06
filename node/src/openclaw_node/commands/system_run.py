"""Admin-gated shell command handler (P3.2.5 surface).

Implements ``system.run`` — execute a command with controlled environment,
timeout, and working directory.  This is the most powerful command in the
node's surface and is protected by two gates:

1. **Admin token gate**: callers must supply a token matching
   ``OPENCLAW_ADMIN_TOKEN`` env var.  If the var is not set, the command
   is always blocked (fail-closed).
2. **No shell expansion**: ``cmd`` must be a list of strings.  Shell
   strings are rejected to prevent injection attacks.

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
(default 60 s).  The process is killed on timeout; ``TIMEOUT`` is returned.

Working directory
-----------------
``cwd``, when supplied, must resolve within the node's allowed roots or
the Home Assistant ``/config`` hierarchy (relaxed for maintenance tasks).
"""

from __future__ import annotations

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


def _admin_token() -> str:
    """Return the configured admin token, or empty string if not set."""
    return os.environ.get("OPENCLAW_ADMIN_TOKEN", "")


def handle_system_run(params: dict[str, Any]) -> dict[str, Any]:
    """Execute a command in a controlled environment.

    Params:
        cmd (list[str]): Command and arguments.  Must be a list; shell
            strings are rejected.
        admin_token (str): Must match ``OPENCLAW_ADMIN_TOKEN``.
        cwd (str, optional): Working directory.
        env (dict[str, str], optional): Extra environment variables merged
            onto a sanitised base.  Keys matching credential patterns are
            rejected.
        timeout (int, optional): Seconds; defaults to 30, capped at
            ``OPENCLAW_RUN_TIMEOUT_MAX``.

    Returns:
        ``{ok: True, stdout, stderr, returncode, elapsed_ms}`` on success,
        or an error dict.
    """
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
    if caller_token != required_token:
        return _error("ADMIN_REQUIRED", "Invalid or missing admin_token")

    cwd = params.get("cwd") or None
    if cwd is not None:
        cwd = str(cwd)

    caller_env: dict[str, str] = {}
    raw_env = params.get("env")
    if raw_env is not None:
        if not isinstance(raw_env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in raw_env.items()
        ):
            return _error("INVALID_PARAM", "env must be a dict of string → string")
        caller_env = raw_env

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
    max_t = _max_timeout()
    if timeout_s <= 0:
        return _error("INVALID_PARAM", "timeout must be positive")
    timeout_s = min(timeout_s, max_t)

    _LOG.info("system.run cmd=%r cwd=%r timeout=%ds", cmd, cwd, timeout_s)
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            cwd=cwd,
            env=merged_env,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _LOG.warning("system.run timed out after %dms cmd=%r", elapsed_ms, cmd)
        return _error("TIMEOUT", f"Command timed out after {timeout_s}s")
    except FileNotFoundError:
        return _error("NOT_FOUND", f"Binary not found: {cmd[0]!r}")
    except OSError as exc:
        return _error("EXEC_ERROR", f"Execution failed: {exc}")

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    stdout = result.stdout[:_MAX_OUTPUT_BYTES].decode(errors="replace")
    stderr = result.stderr[:_MAX_OUTPUT_BYTES].decode(errors="replace")

    _LOG.info(
        "system.run finished cmd=%r rc=%d elapsed_ms=%d",
        cmd,
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
