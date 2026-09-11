"""OpenClaw-native exec approval preparation and policy storage.

The Gateway owns approval prompting and execution authorization. This module
implements the node-side wire contracts used to prepare an approval-bound
``system.run`` request and to manage the node's exec approval policy.

It deliberately does not execute commands. The existing ``system.run``
handler remains fail-closed behind its legacy gate until a follow-up change
binds execution to the prepared native approval context.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
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
_LOCK_ACQUIRE_ATTEMPTS: Final[int] = 5

# The only argv this node will describe to a human by its inline payload rather
# than by its full canonical text. ``buildNodeShellCommand`` emits exactly these
# literal forms for a POSIX node host, so showing the payload stays truthful:
# the executable is literally ``/bin/sh``.
#
# These are matched literally. Normalising the executable, for example by case
# folding or by reinterpreting backslashes as separators, would let an unrelated
# binary such as ``/tmp/BASH`` be presented to an approver as its payload alone.
# Any other argv must present a rawCommand equal to the canonical text or be
# rejected; parity with the Gateway's full shell-wrapper resolver is
# deliberately not reimplemented here.
_INLINE_SHELL_ARGV0: Final[str] = "/bin/sh"
_INLINE_SHELL_FLAGS: Final[frozenset[str]] = frozenset({"-c", "-lc"})

# Characters matched by the JavaScript ``\s`` class. Python has no equivalent
# set: ``str.isspace`` also matches U+001C-U+001F and U+0085 and does not match
# U+FEFF, so it is spelled out by code point and reused everywhere the Gateway
# schema or renderer depends on JavaScript whitespace semantics.
_JS_WHITESPACE: Final[frozenset[str]] = frozenset(
    chr(code)
    for code in (
        0x0009,  # tab
        0x000A,  # line feed
        0x000B,  # vertical tab
        0x000C,  # form feed
        0x000D,  # carriage return
        0x0020,  # space
        0x00A0,  # no-break space
        0x1680,  # ogham space mark
        0x2028,  # line separator
        0x2029,  # paragraph separator
        0x202F,  # narrow no-break space
        0x205F,  # medium mathematical space
        0x3000,  # ideographic space
        0xFEFF,  # zero width no-break space
        *range(0x2000, 0x200B),  # en quad through hair space
    )
)

# ``formatExecCommand`` quotes on ``/\s|"/``.
_JS_QUOTE_TRIGGERS: Final[frozenset[str]] = _JS_WHITESPACE | {'"'}


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


def _format_exec_command(argv: list[str]) -> str:
    r"""Render argv exactly like OpenClaw's ``formatExecCommand``.

    The quoting trigger is ``/\s|"/`` in JavaScript, which is not the same set
    as :meth:`str.isspace`. ``str.isspace`` also matches U+001C-U+001F and
    U+0085 and does not match U+FEFF, so the set is spelled out rather than
    inferred. A divergence here would make the node advertise approval text that
    the Gateway renders differently for the same argv.
    """
    parts: list[str] = []
    for arg in argv:
        if not arg:
            parts.append('""')
        elif not any(char in _JS_QUOTE_TRIGGERS for char in arg):
            parts.append(arg)
        else:
            parts.append('"' + arg.replace('"', '\\"') + '"')
    return " ".join(parts)


def _inline_shell_payload(argv: list[str]) -> str | None:
    """Return the inline payload of the one literal wrapper form we display.

    Every element is compared literally. The executable is not normalised in any
    way, so this can only ever match the argv the Gateway itself builds for a
    POSIX node host.
    """
    if len(argv) != 3 or argv[0] != _INLINE_SHELL_ARGV0 or argv[1] not in _INLINE_SHELL_FLAGS:
        return None
    payload = argv[2].strip()
    return payload or None


def _resolve_command_text(
    argv: list[str], raw_command: Any
) -> tuple[tuple[str, str | None] | None, dict[str, Any] | None]:
    """Bind approval text to argv, returning ``(commandText, commandPreview)``.

    ``commandText`` is always the canonical rendering of the argv that would
    actually run, never caller-supplied text. A ``rawCommand`` that describes a
    different command is rejected instead of being echoed back, because the
    Gateway uses ``plan.commandText`` as both the approval prompt and the
    transport raw command.
    """
    if raw_command is not None and not isinstance(raw_command, str):
        return None, _error("INVALID_PARAM", "rawCommand must be a string")
    raw = raw_command.strip() if isinstance(raw_command, str) else ""
    command_text = _format_exec_command(argv)
    payload = _inline_shell_payload(argv)
    preview = payload if payload is not None and payload != command_text else None
    if not raw:
        return (command_text, preview), None
    if raw != command_text and raw != payload:
        return None, {
            "ok": False,
            "error": "RAW_COMMAND_MISMATCH",
            "message": "INVALID_REQUEST: rawCommand does not match command",
            "inferred": command_text,
            "formattedArgv": command_text,
        }
    return (command_text, preview), None


def _is_one_of(value: Any, allowed: frozenset[str]) -> bool:
    """Test string membership without assuming the value is hashable."""
    return isinstance(value, str) and value in allowed


def _validate_optional_string(
    scope: str, container: dict[str, Any], field: str
) -> dict[str, Any] | None:
    """Validate an optional string, where an explicit null is not an absent key.

    ``Type.Optional(Type.String())`` accepts a missing key but rejects ``null``,
    so presence is tested rather than truthiness.
    """
    if field not in container:
        return None
    if not isinstance(container[field], str):
        return _error("INVALID_PARAM", f"{scope}.{field} must be a string")
    return None


def _validate_min_length_string(scope: str, value: Any) -> dict[str, Any] | None:
    """Require a string of at least one character, matching ``NonEmptyString``."""
    if not isinstance(value, str) or not value:
        return _error("INVALID_PARAM", f"{scope} must be a non-empty string")
    return None


def _validate_non_blank_string(scope: str, value: Any) -> dict[str, Any] | None:
    r"""Require a string with a character outside JavaScript's ``\s`` class.

    The Gateway spells this as ``pattern: "\S"``. ``str.strip()`` is not the
    same test: a value of only U+FEFF survives ``strip`` but is whitespace to
    JavaScript, so the Gateway would reject a document the node had persisted.
    """
    if not isinstance(value, str) or not value:
        return _error("INVALID_PARAM", f"{scope} must be a non-empty string")
    if all(char in _JS_WHITESPACE for char in value):
        return _error("INVALID_PARAM", f"{scope} must contain a non-whitespace character")
    return None


def _validate_timestamp(
    scope: str, container: dict[str, Any], field: str, *, required: bool
) -> dict[str, Any] | None:
    """Validate a non-negative finite number, rejecting an explicit null."""
    if field not in container:
        if required:
            return _error("INVALID_PARAM", f"{scope}.{field} is required")
        return None
    value = container[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _error("INVALID_PARAM", f"{scope}.{field} must be a number")
    if value != value or value in (float("inf"), float("-inf")):
        return _error("INVALID_PARAM", f"{scope}.{field} must be a finite number")
    if value < 0:
        return _error("INVALID_PARAM", f"{scope}.{field} must be greater than or equal to 0")
    return None


_ALLOWLIST_ENTRY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "pattern",
        "source",
        "commandText",
        "argPattern",
        "lastUsedAt",
        "lastUsedCommand",
        "lastResolvedPath",
    }
)
_MCP_TOOL_FIELDS: Final[frozenset[str]] = frozenset(
    {"server", "tool", "source", "addedAt", "lastUsedAt"}
)


def _validate_allowlist_entry(scope: str, entry: Any) -> dict[str, Any] | None:
    """Validate one allowlist entry against the Gateway's closed schema."""
    if not isinstance(entry, dict):
        return _error("INVALID_PARAM", f"{scope} must be an object")
    unknown = set(entry) - _ALLOWLIST_ENTRY_FIELDS
    if unknown:
        return _error("INVALID_PARAM", f"{scope} contains unknown fields: {sorted(unknown)}")
    if not isinstance(entry.get("pattern"), str):
        return _error("INVALID_PARAM", f"{scope}.pattern must be a string")
    if "id" in entry:
        error = _validate_min_length_string(f"{scope}.id", entry["id"])
        if error is not None:
            return error
    if "source" in entry and entry["source"] != "allow-always":
        return _error("INVALID_PARAM", f"{scope}.source must be 'allow-always'")
    for field_name in ("commandText", "argPattern", "lastUsedCommand", "lastResolvedPath"):
        error = _validate_optional_string(scope, entry, field_name)
        if error is not None:
            return error
    return _validate_timestamp(scope, entry, "lastUsedAt", required=False)


def _validate_mcp_tool(scope: str, entry: Any) -> dict[str, Any] | None:
    """Validate one persisted MCP tool approval against the closed schema."""
    if not isinstance(entry, dict):
        return _error("INVALID_PARAM", f"{scope} must be an object")
    unknown = set(entry) - _MCP_TOOL_FIELDS
    if unknown:
        return _error("INVALID_PARAM", f"{scope} contains unknown fields: {sorted(unknown)}")
    for field_name in ("server", "tool"):
        error = _validate_non_blank_string(f"{scope}.{field_name}", entry.get(field_name))
        if error is not None:
            return error
    if entry.get("source") != "allow-always":
        return _error("INVALID_PARAM", f"{scope}.source must be 'allow-always'")
    error = _validate_timestamp(scope, entry, "addedAt", required=True)
    if error is not None:
        return error
    return _validate_timestamp(scope, entry, "lastUsedAt", required=False)


def _validate_policy(
    scope: str, policy: dict[str, Any], *, allow_allowlist: bool
) -> dict[str, Any] | None:
    """Validate one defaults or agent policy block."""
    allowed = _POLICY_FIELDS | ({"allowlist", "mcpTools"} if allow_allowlist else set())
    unknown = set(policy) - allowed
    if unknown:
        return _error("INVALID_PARAM", f"{scope} contains unknown fields: {sorted(unknown)}")
    if "security" in policy and not _is_one_of(policy["security"], _VALID_SECURITY):
        return _error("INVALID_PARAM", f"{scope}.security must be one of {sorted(_VALID_SECURITY)}")
    if "ask" in policy and not _is_one_of(policy["ask"], _VALID_ASK):
        return _error("INVALID_PARAM", f"{scope}.ask must be one of {sorted(_VALID_ASK)}")
    if "askFallback" in policy and not _is_one_of(policy["askFallback"], _VALID_SECURITY):
        return _error(
            "INVALID_PARAM", f"{scope}.askFallback must be one of {sorted(_VALID_SECURITY)}"
        )
    if "autoAllowSkills" in policy and not isinstance(policy["autoAllowSkills"], bool):
        return _error("INVALID_PARAM", f"{scope}.autoAllowSkills must be boolean")

    if "allowlist" in policy:
        allowlist = policy["allowlist"]
        if not isinstance(allowlist, list):
            return _error("INVALID_PARAM", f"{scope}.allowlist must be a list")
        for index, entry in enumerate(allowlist):
            error = _validate_allowlist_entry(f"{scope}.allowlist[{index}]", entry)
            if error is not None:
                return error
    if "mcpTools" in policy:
        mcp_tools = policy["mcpTools"]
        if not isinstance(mcp_tools, list):
            return _error("INVALID_PARAM", f"{scope}.mcpTools must be a list")
        for index, entry in enumerate(mcp_tools):
            error = _validate_mcp_tool(f"{scope}.mcpTools[{index}]", entry)
            if error is not None:
                return error
    return None


def _validate_document(value: Any) -> dict[str, Any] | None:
    """Validate the file-backed policy schema accepted by the Gateway."""
    if not isinstance(value, dict):
        return _error("INVALID_PARAM", "file must be an object")
    if set(value) - {"version", "socket", "defaults", "agents"}:
        return _error("INVALID_PARAM", "file contains unknown fields")
    version = value.get("version")
    # ``True == 1`` in Python but ``Type.Literal(1)`` rejects a JSON boolean.
    if isinstance(version, bool) or version != APPROVALS_DOC_VERSION:
        return _error("INVALID_PARAM", f"file.version must be {APPROVALS_DOC_VERSION}")
    if "socket" in value:
        socket = value["socket"]
        if not isinstance(socket, dict) or set(socket) - {"path", "token"}:
            return _error("INVALID_PARAM", "file.socket is invalid")
        if any(not isinstance(item, str) for item in socket.values()):
            return _error("INVALID_PARAM", "file.socket values must be strings")
    if "defaults" in value:
        defaults = value["defaults"]
        if not isinstance(defaults, dict):
            return _error("INVALID_PARAM", "file.defaults must be an object")
        error = _validate_policy("file.defaults", defaults, allow_allowlist=False)
        if error is not None:
            return error
    if "agents" in value:
        agents = value["agents"]
        if not isinstance(agents, dict):
            return _error("INVALID_PARAM", "file.agents must be an object")
        for agent_id, policy in agents.items():
            if not isinstance(agent_id, str) or not isinstance(policy, dict):
                return _error("INVALID_PARAM", "file.agents entries must be policy objects")
            error = _validate_policy(f"file.agents.{agent_id}", policy, allow_allowlist=True)
            if error is not None:
                return error
    return None


def _read_snapshot(path: Path | None = None) -> tuple[dict[str, Any], bytes | None]:
    """Read a valid snapshot, replacing malformed content with deny policy."""
    path = _approvals_path() if path is None else path
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


def _redact_socket_value(value: str) -> str:
    """Return the trimmed form ``redactExecApprovals`` exposes for a socket value."""
    return value.strip()


def _redact_document(document: dict[str, Any]) -> dict[str, Any]:
    """Drop the socket credential exactly like OpenClaw's ``redactExecApprovals``.

    The socket token authenticates the local exec host. It is never disclosed to
    a caller, and a caller therefore cannot echo it back, which is why
    ``_merge_socket`` restores it on write. The exposed path is trimmed to match
    the native implementation, so ``_merge_socket`` treats an echoed trimmed path
    as "unchanged" rather than as a request to repoint the socket.
    """
    redacted = dict(document)
    socket = document.get("socket")
    socket_path = socket.get("path") if isinstance(socket, dict) else None
    if isinstance(socket_path, str) and socket_path.strip():
        redacted["socket"] = {"path": _redact_socket_value(socket_path)}
    else:
        redacted.pop("socket", None)
    return redacted


def _merge_socket(document: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Carry the stored socket path and token across a redacted round trip.

    Unlike the Gateway's own host, this node never fabricates a socket path or
    mints a token. It only preserves what is already stored, so a read-edit-write
    through the redacted snapshot cannot erase the credential.
    """
    incoming = document.get("socket")
    incoming = incoming if isinstance(incoming, dict) else {}
    stored = current.get("socket")
    stored = stored if isinstance(stored, dict) else {}

    def stored_str(field: str) -> str | None:
        value = stored.get(field)
        return value if isinstance(value, str) else None

    def pick_path() -> str | None:
        # The exact stored string is carried over, not a trimmed copy. The closed
        # schema allows any string, so normalising here would repoint a socket
        # whose filename legitimately has leading or trailing spaces.
        stored_value = stored_str("path")
        incoming_value = incoming.get("path")
        if not isinstance(incoming_value, str):
            return stored_value
        # The path *is* disclosed, in trimmed form. An unchanged round trip
        # therefore returns the redacted view of what is stored, which is not a
        # request to change the value, so keep the exact stored string.
        #
        # Known limitation: a deliberate edit from a whitespace-padded path to
        # exactly its own trimmed form is indistinguishable from that echo and
        # keeps the padded value. Repointing to any other path works normally.
        if stored_value is not None and incoming_value == _redact_socket_value(stored_value):
            return stored_value
        return incoming_value

    def pick_token() -> str | None:
        # The token is never disclosed, so a caller cannot echo it back. Any
        # present string is therefore an explicit write and must be honoured
        # exactly, including an empty string that clears the credential.
        # Applying the redacted-view comparison here would silently discard a
        # genuine replacement or revocation and report success.
        incoming_value = incoming.get("token")
        if not isinstance(incoming_value, str):
            return stored_str("token")
        return incoming_value

    merged = dict(document)
    picked = {"path": pick_path(), "token": pick_token()}
    socket = {field: value for field, value in picked.items() if value is not None}
    if socket:
        merged["socket"] = socket
    else:
        merged.pop("socket", None)
    return merged


def _snapshot(path: Path | None = None, *, redact: bool = True) -> dict[str, Any]:
    """Return the exact file-backed snapshot shape consumed by the Gateway.

    The caller may pin the path so that a read, a concurrency check, and a write
    all refer to the same target even if configuration changes in between.
    """
    path = _approvals_path() if path is None else path
    document, raw = _read_snapshot(path)
    return {
        "path": str(path),
        "exists": raw is not None,
        "hash": _MISSING_HASH if raw is None else hashlib.sha256(raw).hexdigest(),
        "file": _redact_document(document) if redact else document,
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

    resolved_text, error = _resolve_command_text(argv, params.get("rawCommand"))
    if error is not None:
        return error
    assert resolved_text is not None
    command_text, command_preview = resolved_text

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
            "commandText": command_text,
            "commandPreview": command_preview,
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


def _locks_same_file(fd: int, lock_path: Path) -> bool:
    """Report whether ``fd`` still refers to the file now at ``lock_path``."""
    locked = os.fstat(fd)
    try:
        current = os.stat(lock_path)
    except OSError:
        return False
    return (current.st_dev, current.st_ino) == (locked.st_dev, locked.st_ino)


@contextmanager
def _approvals_write_lock(path: Path) -> Iterator[None]:
    """Serialize approval writers against a lock file beside the document.

    ``os.replace`` on its own is atomic but unconditional, so two writers that
    each validated against the same base hash would both commit and the later
    one would silently discard the earlier policy. Every writer takes this lock
    before re-reading, so the hash comparison and the replacement are one step.

    The lock is opened without following symlinks, its mode is repaired on every
    acquisition because the ``os.open`` mode argument does not apply to an
    existing file, and the locked descriptor is confirmed to still be the file at
    ``lock_path`` afterwards. That check narrows the window in which a rename
    leaves two writers holding different inodes; it does not close it. The
    identity is verified once, before ``yield``, so a replacement landing after
    the check still splits the writers.

    Closing that window entirely needs a coordination primitive whose identity
    cannot be swapped mid-section. It is deliberately not built here: the lock
    lives in the node's private data directory, and an attacker able to rename a
    file there can already rewrite the policy document directly. The trust
    boundary is the directory, not this lock.
    """
    lock_path = path.with_name(path.name + ".lock")
    for _ in range(_LOCK_ACQUIRE_ATTEMPTS):
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            if not _locks_same_file(fd, lock_path):
                # The lock file was replaced while we waited, so this lock now
                # protects an inode nobody else will contend for. Start over.
                continue
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
            return
        finally:
            os.close(fd)
    message = f"could not acquire a stable exec approvals lock at {lock_path}"
    raise OSError(message)


def handle_system_exec_approvals_set(params: dict[str, Any]) -> dict[str, Any]:
    """Replace policy using the Gateway's file/baseHash concurrency contract."""
    document = params.get("file")
    error = _validate_document(document)
    if error is not None:
        return error
    document = cast(dict[str, Any], document)

    raw_base_hash = params.get("baseHash")
    base_hash = raw_base_hash.strip() if isinstance(raw_base_hash, str) else ""

    # One resolution for the whole operation. Re-resolving would let the check
    # and the write land on different files.
    path = _approvals_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _LOG.warning("failed to prepare exec approvals directory for %s: %s", path, exc)
        return _error("IO_ERROR", f"Could not write approvals document: {exc}")

    temp_path: Path | None = None
    fd: int | None = None
    try:
        with _approvals_write_lock(path):
            # Read under the lock so the comparison describes the file that is
            # about to be replaced, not one observed earlier.
            current = _snapshot(path, redact=False)
            if current["exists"]:
                if not current["hash"]:
                    return _error(
                        "INVALID_REQUEST",
                        "exec approvals base hash unavailable; reload and retry",
                    )
                if not base_hash:
                    return _error(
                        "INVALID_REQUEST", "exec approvals base hash required; reload and retry"
                    )
                if base_hash != current["hash"]:
                    return _error("INVALID_REQUEST", "exec approvals changed; reload and retry")
            elif base_hash and base_hash != current["hash"]:
                return _error("INVALID_REQUEST", "exec approvals changed; reload and retry")

            merged = _merge_socket(document, cast(dict[str, Any], current["file"]))

            fd, raw_temp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            temp_path = Path(raw_temp_path)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                fd = None
                handle.write(_serialize_document(merged))
                handle.flush()
                os.fsync(handle.fileno())
            # os.replace is the commit point. The temp descriptor was already
            # fchmod'ed to 0600 above, so the destination has its final mode the
            # instant it becomes visible. Nothing fallible may run after this:
            # an error raised post-commit would report IO_ERROR for a policy that
            # is already active, which is the opposite of fail-closed.
            os.replace(temp_path, path)
            temp_path = None
            return _snapshot(path)
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
