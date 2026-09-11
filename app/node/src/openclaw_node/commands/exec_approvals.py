"""OpenClaw-native exec approval preparation and policy storage.

The Gateway owns approval prompting and execution authorization. This module
implements the node-side wire contracts used to prepare an approval-bound
``system.run`` request and to manage the node's exec approval policy.

It deliberately does not execute commands. The existing ``system.run``
handler remains fail-closed behind its legacy gate until a follow-up change
binds execution to the prepared native approval context.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Final, cast

from openclaw_node.config import allowed_roots_for_env
from openclaw_node.safe_path import NoAllowedRootsError, OutOfBoundsError, resolve_safe

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

APPROVALS_DOC_VERSION: Final[int] = 1
_VALID_SECURITY: Final[frozenset[str]] = frozenset({"deny", "allowlist", "full"})
_VALID_ASK: Final[frozenset[str]] = frozenset({"off", "on-miss", "always"})
_POLICY_FIELDS: Final[frozenset[str]] = frozenset(
    {"security", "ask", "askFallback", "autoAllowSkills"}
)
_DEFAULT_POLICY: Final[dict[str, Any]] = {
    "security": "deny",
    "ask": "on-miss",
    "askFallback": "deny",
    "autoAllowSkills": False,
}
_MAX_ARGV: Final[int] = 256
_MAX_ARG_LEN: Final[int] = 8192
_MISSING_HASH: Final[str] = "missing:" + hashlib.sha256(b"").hexdigest()


def _error(code: str, message: str) -> dict[str, Any]:
    """Return a standard node command error payload."""
    return {"ok": False, "error": code, "message": message}


def _approvals_path() -> Path:
    """Return the node-local exec approvals document path."""
    from openclaw_node.config import load_config

    return load_config().data_dir / "exec-approvals.json"


def _default_document() -> dict[str, Any]:
    """Return an explicit fail-closed policy document."""
    return {
        "version": APPROVALS_DOC_VERSION,
        "defaults": dict(_DEFAULT_POLICY),
        "agents": {},
    }


def _validate_cwd(cwd: str) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve a proposed working directory beneath an allowed root."""
    try:
        resolved = resolve_safe(cwd, allowed_roots_for_env())
    except NoAllowedRootsError:
        return None, _error("NO_ALLOWED_ROOTS", "No filesystem roots configured")
    except OutOfBoundsError:
        return None, _error("PATH_NOT_ALLOWED", f"cwd is outside the allowed roots: {cwd!r}")
    if not resolved.is_dir():
        return None, _error("INVALID_PARAM", f"cwd is not a directory: {cwd!r}")
    return str(resolved), None


def _validate_argv(value: Any) -> tuple[list[str] | None, dict[str, Any] | None]:
    """Validate the Gateway's canonical command argument vector."""
    if value is None:
        return None, _error("MISSING_PARAM", "command is required")
    if isinstance(value, str) or not isinstance(value, list):
        return None, _error("INVALID_PARAM", "command must be a list of strings")
    if not value or not all(isinstance(item, str) for item in value):
        return None, _error("INVALID_PARAM", "command must be a non-empty list of strings")
    if not value[0]:
        return None, _error("INVALID_PARAM", "command[0] must be a non-empty executable name")
    if len(value) > _MAX_ARGV:
        return None, _error("INVALID_PARAM", f"command has more than {_MAX_ARGV} arguments")
    if any(len(item) > _MAX_ARG_LEN for item in value):
        return None, _error(
            "INVALID_PARAM", f"command contains an argument longer than {_MAX_ARG_LEN}"
        )
    if any("\x00" in item for item in value):
        return None, _error("INVALID_PARAM", "command arguments must not contain NUL bytes")
    return list(value), None


def _validate_env(value: Any) -> dict[str, Any] | None:
    """Validate optional environment bindings without persisting their values."""
    if value is None:
        return None
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and key and isinstance(item, str) for key, item in value.items()
    ):
        return _error("INVALID_PARAM", "env must be an object of non-empty string keys and values")
    return None


def _validate_policy(
    scope: str, policy: dict[str, Any], *, allow_allowlist: bool
) -> dict[str, Any] | None:
    """Validate one defaults or agent policy block."""
    allowed = _POLICY_FIELDS | ({"allowlist", "mcpTools"} if allow_allowlist else set())
    unknown = set(policy) - allowed
    if unknown:
        return _error("INVALID_PARAM", f"{scope} contains unknown fields: {sorted(unknown)}")
    security = policy.get("security")
    ask = policy.get("ask")
    fallback = policy.get("askFallback")
    auto_allow = policy.get("autoAllowSkills")
    if security is not None and security not in _VALID_SECURITY:
        return _error("INVALID_PARAM", f"{scope}.security must be one of {sorted(_VALID_SECURITY)}")
    if ask is not None and ask not in _VALID_ASK:
        return _error("INVALID_PARAM", f"{scope}.ask must be one of {sorted(_VALID_ASK)}")
    if fallback is not None and fallback not in _VALID_SECURITY:
        return _error(
            "INVALID_PARAM", f"{scope}.askFallback must be one of {sorted(_VALID_SECURITY)}"
        )
    if auto_allow is not None and not isinstance(auto_allow, bool):
        return _error("INVALID_PARAM", f"{scope}.autoAllowSkills must be boolean")

    allowlist = policy.get("allowlist")
    if allowlist is not None:
        if not isinstance(allowlist, list):
            return _error("INVALID_PARAM", f"{scope}.allowlist must be a list")
        allowed_entry_fields = {
            "id",
            "pattern",
            "source",
            "commandText",
            "argPattern",
            "lastUsedAt",
            "lastUsedCommand",
            "lastResolvedPath",
        }
        for index, entry in enumerate(allowlist):
            if not isinstance(entry, dict) or not isinstance(entry.get("pattern"), str):
                return _error(
                    "INVALID_PARAM", f"{scope}.allowlist[{index}] requires a string pattern"
                )
            if set(entry) - allowed_entry_fields:
                return _error(
                    "INVALID_PARAM", f"{scope}.allowlist[{index}] contains unknown fields"
                )
            if entry.get("source") not in (None, "allow-always"):
                return _error("INVALID_PARAM", f"{scope}.allowlist[{index}].source is invalid")
    mcp_tools = policy.get("mcpTools")
    if mcp_tools is not None and not isinstance(mcp_tools, list):
        return _error("INVALID_PARAM", f"{scope}.mcpTools must be a list")
    return None


def _validate_document(value: Any) -> dict[str, Any] | None:
    """Validate the file-backed policy schema accepted by the Gateway."""
    if not isinstance(value, dict):
        return _error("INVALID_PARAM", "file must be an object")
    if set(value) - {"version", "socket", "defaults", "agents"}:
        return _error("INVALID_PARAM", "file contains unknown fields")
    if value.get("version") != APPROVALS_DOC_VERSION:
        return _error("INVALID_PARAM", f"file.version must be {APPROVALS_DOC_VERSION}")
    socket = value.get("socket")
    if socket is not None:
        if not isinstance(socket, dict) or set(socket) - {"path", "token"}:
            return _error("INVALID_PARAM", "file.socket is invalid")
        if any(not isinstance(item, str) for item in socket.values()):
            return _error("INVALID_PARAM", "file.socket values must be strings")
    defaults = value.get("defaults")
    if defaults is not None:
        if not isinstance(defaults, dict):
            return _error("INVALID_PARAM", "file.defaults must be an object")
        error = _validate_policy("file.defaults", defaults, allow_allowlist=False)
        if error is not None:
            return error
    agents = value.get("agents")
    if agents is not None:
        if not isinstance(agents, dict):
            return _error("INVALID_PARAM", "file.agents must be an object")
        for agent_id, policy in agents.items():
            if not isinstance(agent_id, str) or not isinstance(policy, dict):
                return _error("INVALID_PARAM", "file.agents entries must be policy objects")
            error = _validate_policy(f"file.agents.{agent_id}", policy, allow_allowlist=True)
            if error is not None:
                return error
    return None


def _read_snapshot() -> tuple[dict[str, Any], bytes | None]:
    """Read a valid snapshot, replacing malformed content with deny policy."""
    path = _approvals_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _default_document(), None
    except OSError as exc:
        _LOG.warning("exec approvals document unreadable at %s: %s", path, exc)
        return _default_document(), None
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        _LOG.warning("exec approvals document malformed at %s: %s", path, exc)
        return _default_document(), raw
    if _validate_document(document) is not None:
        _LOG.warning("exec approvals document failed validation at %s", path)
        return _default_document(), raw
    return document, raw


def _snapshot() -> dict[str, Any]:
    """Return the exact file-backed snapshot shape consumed by the Gateway."""
    path = _approvals_path()
    document, raw = _read_snapshot()
    return {
        "path": str(path),
        "exists": raw is not None,
        "hash": _MISSING_HASH if raw is None else hashlib.sha256(raw).hexdigest(),
        "file": document,
    }


def _resolve_policy(document: dict[str, Any], agent_id: str | None) -> dict[str, Any]:
    """Resolve exact-agent, wildcard, and default policy for approval binding."""
    raw_defaults = document.get("defaults")
    defaults = cast(dict[str, Any], raw_defaults) if isinstance(raw_defaults, dict) else {}
    raw_agents = document.get("agents")
    agents = cast(dict[str, Any], raw_agents) if isinstance(raw_agents, dict) else {}
    raw_wildcard = agents.get("*")
    wildcard = cast(dict[str, Any], raw_wildcard) if isinstance(raw_wildcard, dict) else {}
    raw_exact = agents.get(agent_id) if agent_id else None
    exact = cast(dict[str, Any], raw_exact) if isinstance(raw_exact, dict) else {}

    def field(name: str) -> Any:
        return exact.get(name, wildcard.get(name, defaults.get(name, _DEFAULT_POLICY[name])))

    rules: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for source in (wildcard, exact):
        entries = source.get("allowlist", []) if isinstance(source.get("allowlist"), list) else []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("pattern"), str):
                continue
            rule = {"pattern": entry["pattern"]}
            if isinstance(entry.get("argPattern"), str):
                rule["argPattern"] = entry["argPattern"]
            if entry.get("source") == "allow-always":
                rule["source"] = "allow-always"
            key = (rule["pattern"], rule.get("argPattern"), rule.get("source"))
            if key not in seen:
                seen.add(key)
                rules.append(rule)
    rules.sort(
        key=lambda item: (
            item["pattern"].encode(),
            (item.get("argPattern") or "").encode(),
            (item.get("source") or "").encode(),
        )
    )
    return {
        "security": field("security"),
        "ask": field("ask"),
        "askFallback": field("askFallback"),
        "autoAllowSkills": field("autoAllowSkills"),
        "allowlistRules": rules,
    }


def handle_system_run_prepare(params: dict[str, Any]) -> dict[str, Any]:
    """Prepare the exact approval plan expected by current OpenClaw Gateways."""
    argv, error = _validate_argv(params.get("command"))
    if error is not None:
        return error
    assert argv is not None

    raw_command = params.get("rawCommand")
    if not isinstance(raw_command, str) or not raw_command.strip():
        return _error("INVALID_PARAM", "rawCommand must be a non-empty string")
    env_error = _validate_env(params.get("env"))
    if env_error is not None:
        return env_error

    resolved_cwd: str | None = None
    raw_cwd = params.get("cwd")
    if raw_cwd is not None:
        if not isinstance(raw_cwd, str) or not raw_cwd.strip():
            return _error("INVALID_PARAM", "cwd must be a non-empty string")
        resolved_cwd, error = _validate_cwd(raw_cwd)
        if error is not None:
            return error

    agent_id = params.get("agentId")
    session_key = params.get("sessionKey")
    if agent_id is not None and (not isinstance(agent_id, str) or not agent_id.strip()):
        return _error("INVALID_PARAM", "agentId must be a non-empty string")
    if session_key is not None and (not isinstance(session_key, str) or not session_key.strip()):
        return _error("INVALID_PARAM", "sessionKey must be a non-empty string")

    document, _raw = _read_snapshot()
    policy = _resolve_policy(document, agent_id)
    return {
        "plan": {
            "argv": argv,
            "cwd": resolved_cwd,
            "commandText": raw_command,
            "agentId": agent_id,
            "sessionKey": session_key,
            "policySnapshot": policy,
        },
        "execPolicy": {"security": policy["security"], "ask": policy["ask"]},
        "allowAlwaysCoverage": {"complete": False, "patterns": []},
    }


def handle_system_exec_approvals_get(_params: dict[str, Any]) -> dict[str, Any]:
    """Return the exact node approval snapshot expected by the Gateway."""
    return _snapshot()


def _serialize_document(document: dict[str, Any]) -> bytes:
    """Serialize policy exactly like OpenClaw's file-backed implementation."""
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def handle_system_exec_approvals_set(params: dict[str, Any]) -> dict[str, Any]:
    """Replace policy using the Gateway's file/baseHash concurrency contract."""
    document = params.get("file")
    error = _validate_document(document)
    if error is not None:
        return error
    document = cast(dict[str, Any], document)

    current = _snapshot()
    base_hash = params.get("baseHash")
    if base_hash is not None and base_hash != current["hash"]:
        return _error("INVALID_REQUEST", "exec approvals changed; reload and retry")

    path = _approvals_path()
    temp_path: Path | None = None
    fd: int | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_temp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(raw_temp_path)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(_serialize_document(document))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
        os.chmod(path, 0o600)
    except OSError as exc:
        _LOG.warning("failed to write exec approvals document at %s: %s", path, exc)
        return _error("IO_ERROR", f"Could not write approvals document: {exc}")
    finally:
        if fd is not None:
            os.close(fd)
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                _LOG.warning("failed to clean exec approvals temp file %s: %s", temp_path, exc)

    return _snapshot()
