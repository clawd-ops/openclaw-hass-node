"""Gateway-contract tests for node-side exec approval participation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from openclaw_node.commands import exec_approvals
from openclaw_node.commands.dispatcher import dispatch


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point approval storage at a temporary directory."""
    target = tmp_path / "data"
    target.mkdir()
    monkeypatch.setattr(exec_approvals, "_approvals_path", lambda: target / "exec-approvals.json")
    return target


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Constrain command working directories to one temporary root."""
    root = tmp_path / "config"
    root.mkdir()
    monkeypatch.setattr(exec_approvals, "allowed_roots_for_env", lambda: (root,))
    return root


def _prepare_params(**updates: Any) -> dict[str, Any]:
    """Return the exact request shape emitted by ``prepareNodeSystemRun``."""
    params: dict[str, Any] = {
        "command": ["/bin/sh", "-lc", "ls -la"],
        "security": "allowlist",
        "ask": "on-miss",
        "rawCommand": "ls -la",
        "env": {"LANG": "C"},
        "agentId": "clawd",
        "sessionKey": "agent:clawd:main",
    }
    params.update(updates)
    return params


def _policy() -> dict[str, Any]:
    """Return a schema-valid fail-closed approval document."""
    return {
        "version": 1,
        "defaults": {
            "security": "allowlist",
            "ask": "on-miss",
            "askFallback": "deny",
            "autoAllowSkills": False,
        },
        "agents": {},
    }


def test_prepare_matches_gateway_wire_contract(data_dir: Path, roots: Path) -> None:
    """The exact Gateway request yields a parseable current plan envelope."""
    cwd = roots / "scripts"
    cwd.mkdir()
    result = exec_approvals.handle_system_run_prepare(_prepare_params(cwd=str(cwd)))

    assert "ok" not in result
    assert result["plan"] == {
        "argv": ["/bin/sh", "-lc", "ls -la"],
        "cwd": str(cwd),
        "commandText": '/bin/sh -lc "ls -la"',
        "commandPreview": "ls -la",
        "agentId": "clawd",
        "sessionKey": "agent:clawd:main",
        "policySnapshot": {
            "security": "deny",
            "ask": "on-miss",
            "askFallback": "deny",
            "autoAllowSkills": False,
            "allowlistRules": [],
        },
    }
    assert result["execPolicy"] == {"security": "deny", "ask": "on-miss"}
    assert result["allowAlwaysCoverage"] == {"complete": False, "patterns": []}


def test_prepare_resolves_exact_policy_before_wildcard(data_dir: Path, roots: Path) -> None:
    """An exact agent policy overrides wildcard fields and combines allowlists."""
    policy = _policy()
    policy["agents"] = {
        "*": {
            "security": "full",
            "allowlist": [{"pattern": "/bin/echo"}],
        },
        "clawd": {
            "security": "deny",
            "ask": "always",
            "allowlist": [{"pattern": "/bin/true", "source": "allow-always"}],
        },
    }
    (data_dir / "exec-approvals.json").write_text(json.dumps(policy), encoding="utf-8")

    result = exec_approvals.handle_system_run_prepare(_prepare_params())
    snapshot = result["plan"]["policySnapshot"]
    assert snapshot["security"] == "deny"
    assert snapshot["ask"] == "always"
    assert snapshot["allowlistRules"] == [
        {"pattern": "/bin/echo"},
        {"pattern": "/bin/true", "source": "allow-always"},
    ]


def test_prepare_never_executes(
    data_dir: Path, roots: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preparation cannot start a process."""
    import subprocess

    def explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("system.run.prepare must not execute")

    monkeypatch.setattr(subprocess, "run", explode)
    assert "plan" in exec_approvals.handle_system_run_prepare(_prepare_params())


def test_prepare_accepts_omitted_optional_fields(data_dir: Path, roots: Path) -> None:
    """The Gateway may omit cwd, env, agent, and session context."""
    result = exec_approvals.handle_system_run_prepare({"command": ["/bin/true"]})
    assert result["plan"]["cwd"] is None
    assert result["plan"]["agentId"] is None
    assert result["plan"]["sessionKey"] is None
    assert result["plan"]["commandText"] == "/bin/true"
    assert result["plan"]["commandPreview"] is None


@pytest.mark.parametrize(
    ("updates", "code"),
    [
        ({"command": None}, "MISSING_PARAM"),
        ({"command": "ls"}, "INVALID_PARAM"),
        ({"command": []}, "INVALID_PARAM"),
        ({"command": ["", "x"]}, "INVALID_PARAM"),
        ({"command": ["ls", 7]}, "INVALID_PARAM"),
        ({"command": ["ls", "a\x00b"]}, "INVALID_PARAM"),
        ({"command": ["ls"] + ["x"] * 300}, "INVALID_PARAM"),
        ({"command": ["ls", "x" * 9000]}, "INVALID_PARAM"),
        ({"rawCommand": 7}, "INVALID_PARAM"),
        ({"env": {"X": 7}}, "INVALID_PARAM"),
        ({"agentId": ""}, "INVALID_PARAM"),
        ({"sessionKey": ""}, "INVALID_PARAM"),
    ],
)
def test_prepare_rejects_invalid_gateway_fields(
    data_dir: Path, roots: Path, updates: dict[str, Any], code: str
) -> None:
    """Malformed Gateway fields fail closed before producing a plan."""
    result = exec_approvals.handle_system_run_prepare(_prepare_params(**updates))
    assert result["ok"] is False
    assert result["error"] == code


def test_prepare_rejects_cwd_outside_roots(data_dir: Path, roots: Path, tmp_path: Path) -> None:
    """Preparation rejects a working directory outside configured roots."""
    outside = tmp_path / "outside"
    outside.mkdir()
    result = exec_approvals.handle_system_run_prepare(_prepare_params(cwd=str(outside)))
    assert result["error"] == "PATH_NOT_ALLOWED"


def test_prepare_rejects_cwd_file(data_dir: Path, roots: Path) -> None:
    """Preparation rejects a non-directory working path."""
    target = roots / "file"
    target.write_text("x", encoding="utf-8")
    result = exec_approvals.handle_system_run_prepare(_prepare_params(cwd=str(target)))
    assert result["error"] == "INVALID_PARAM"


def test_prepare_reports_missing_roots(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cwd cannot be prepared without configured roots."""
    monkeypatch.setattr(exec_approvals, "allowed_roots_for_env", tuple)
    result = exec_approvals.handle_system_run_prepare(_prepare_params(cwd="/tmp"))
    assert result["error"] == "NO_ALLOWED_ROOTS"


def test_get_missing_matches_gateway_snapshot(data_dir: Path) -> None:
    """A missing file returns the canonical missing hash and deny policy."""
    result = exec_approvals.handle_system_exec_approvals_get({})
    assert set(result) == {"path", "exists", "hash", "file"}
    assert result["exists"] is False
    assert result["hash"] == "missing:" + hashlib.sha256(b"").hexdigest()
    assert result["file"]["defaults"]["security"] == "deny"


def test_get_existing_hashes_exact_raw_bytes(data_dir: Path) -> None:
    """Snapshot hash covers the exact serialized file bytes."""
    raw = b'{"version":1,"agents":{}}\n'
    (data_dir / "exec-approvals.json").write_bytes(raw)
    result = exec_approvals.handle_system_exec_approvals_get({})
    assert result["exists"] is True
    assert result["hash"] == hashlib.sha256(raw).hexdigest()
    assert result["file"] == {"version": 1, "agents": {}}


@pytest.mark.parametrize("raw", [b"{bad", b"[]"])
def test_get_malformed_is_fail_closed_but_hashes_raw(data_dir: Path, raw: bytes) -> None:
    """Malformed persisted bytes yield deny policy without hiding their hash."""
    (data_dir / "exec-approvals.json").write_bytes(raw)
    result = exec_approvals.handle_system_exec_approvals_get({})
    assert result["exists"] is True
    assert result["hash"] == hashlib.sha256(raw).hexdigest()
    assert result["file"]["defaults"]["security"] == "deny"


def test_set_matches_gateway_file_base_hash_contract(data_dir: Path) -> None:
    """The Gateway can write from the current snapshot and read a new one."""
    before = exec_approvals.handle_system_exec_approvals_get({})
    result = exec_approvals.handle_system_exec_approvals_set(
        {"file": _policy(), "baseHash": before["hash"]}
    )
    assert set(result) == {"path", "exists", "hash", "file"}
    assert result["exists"] is True
    assert result["file"] == _policy()
    assert (
        result["hash"]
        == hashlib.sha256((json.dumps(_policy(), indent=2) + "\n").encode()).hexdigest()
    )


def test_set_rejects_stale_base_hash_without_mutation(data_dir: Path) -> None:
    """Optimistic concurrency prevents a stale UI from overwriting policy."""
    path = data_dir / "exec-approvals.json"
    path.write_text('{"version":1,"agents":{}}\n', encoding="utf-8")
    original = path.read_bytes()
    result = exec_approvals.handle_system_exec_approvals_set(
        {"file": _policy(), "baseHash": "stale"}
    )
    assert result["error"] == "INVALID_REQUEST"
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "document",
    [
        None,
        {},
        {"version": 2},
        {"version": 1, "unknown": True},
        {"version": 1, "defaults": {"security": "yolo"}},
        {"version": 1, "defaults": {"ask": "sometimes"}},
        {"version": 1, "defaults": {"askFallback": "prompt"}},
        {"version": 1, "defaults": {"autoAllowSkills": "yes"}},
        {"version": 1, "defaults": {"unknown": True}},
        {"version": 1, "defaults": []},
        {"version": 1, "socket": []},
        {"version": 1, "socket": {"path": 7}},
        {"version": 1, "agents": []},
        {"version": 1, "agents": {7: {}}},
        {"version": 1, "agents": {"clawd": {"allowlist": "all"}}},
        {"version": 1, "agents": {"clawd": {"allowlist": [{"id": "x"}]}}},
        {
            "version": 1,
            "agents": {"clawd": {"allowlist": [{"pattern": "x", "unknown": True}]}},
        },
        {
            "version": 1,
            "agents": {"clawd": {"allowlist": [{"pattern": "x", "source": "manual"}]}},
        },
        {"version": 1, "agents": {"clawd": {"mcpTools": "all"}}},
    ],
)
def test_set_rejects_invalid_gateway_file(data_dir: Path, document: Any) -> None:
    """Documents outside the closed Gateway schema are refused."""
    result = exec_approvals.handle_system_exec_approvals_set({"file": document})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"


def test_set_uses_unique_owner_only_temp_and_ignores_preexisting_tmp(data_dir: Path) -> None:
    """A predictable permissive temp file cannot become the final policy."""
    planted = data_dir / "exec-approvals.json.tmp"
    planted.write_text("attacker", encoding="utf-8")
    planted.chmod(0o644)

    result = exec_approvals.handle_system_exec_approvals_set({"file": _policy()})
    assert result["exists"] is True
    assert (data_dir / "exec-approvals.json").stat().st_mode & 0o777 == 0o600
    assert planted.read_text(encoding="utf-8") == "attacker"
    assert not list(data_dir.glob(".exec-approvals.json.*"))


def test_set_cleans_unique_temp_after_replace_failure(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed atomic replacement leaves no generated temporary file."""

    def fail_replace(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    result = exec_approvals.handle_system_exec_approvals_set({"file": _policy()})
    assert result["error"] == "IO_ERROR"
    assert not list(data_dir.glob(".exec-approvals.json.*"))


def test_get_read_error_is_fail_closed(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreadable file cannot make the effective policy permissive."""
    (data_dir / "exec-approvals.json").write_text("{}", encoding="utf-8")

    def fail_read(self: Path) -> bytes:
        raise OSError("read failed")

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    result = exec_approvals.handle_system_exec_approvals_get({})
    assert result["exists"] is False
    assert result["file"]["defaults"]["security"] == "deny"


def test_approvals_path_uses_node_data_dir() -> None:
    """The default path is anchored in the node data directory."""
    assert exec_approvals._approvals_path().name == "exec-approvals.json"


@pytest.mark.parametrize(
    "command",
    ["system.run.prepare", "system.execApprovals.get", "system.execApprovals.set"],
)
def test_commands_are_dispatchable(command: str, data_dir: Path, roots: Path) -> None:
    """Each native approval method is registered in the dispatcher."""
    result = dispatch(command, {})
    assert result.get("error") != "UNKNOWN_COMMAND"


@pytest.mark.parametrize(
    ("argv", "command_text"),
    [
        (["/bin/true"], "/bin/true"),
        (["/bin/echo", "a b"], '/bin/echo "a b"'),
        (["/bin/echo", ""], '/bin/echo ""'),
        (["/bin/echo", 'say "hi"'], '/bin/echo "say \\"hi\\""'),
        (["/bin/sh", "-lc", "ls -la"], '/bin/sh -lc "ls -la"'),
    ],
)
def test_prepare_derives_command_text_from_argv(
    data_dir: Path, roots: Path, argv: list[str], command_text: str
) -> None:
    """Approval text is always the canonical rendering of the real argv."""
    result = exec_approvals.handle_system_run_prepare({"command": argv})
    assert result["plan"]["commandText"] == command_text


@pytest.mark.parametrize(
    ("char", "quoted"),
    [
        # Matched by JavaScript's \s, so the argument is quoted.
        ("\t", True),
        ("\u0020", True),
        ("\u00a0", True),
        ("\u2028", True),
        ("\u3000", True),
        ("\ufeff", True),
        # Matched by str.isspace() but not by JavaScript's \s.
        ("\x1c", False),
        ("\x1f", False),
        ("\x85", False),
    ],
)
def test_command_text_quoting_matches_javascript_whitespace(char: str, quoted: bool) -> None:
    """Quoting follows JavaScript's ``\\s``, not Python's ``str.isspace``."""
    rendered = exec_approvals._format_exec_command([char])
    assert rendered == (f'"{char}"' if quoted else char)


def test_prepare_rejects_raw_command_that_disagrees_with_argv(data_dir: Path, roots: Path) -> None:
    """Approval text cannot describe a command other than the one that would run."""
    result = exec_approvals.handle_system_run_prepare(
        _prepare_params(command=["/bin/sh", "-lc", "rm -rf /"], rawCommand="echo safe")
    )
    assert result["ok"] is False
    assert result["error"] == "RAW_COMMAND_MISMATCH"
    assert result["inferred"] == '/bin/sh -lc "rm -rf /"'


def test_prepare_accepts_canonical_raw_command(data_dir: Path, roots: Path) -> None:
    """The canonical rendering of argv is an accepted rawCommand."""
    result = exec_approvals.handle_system_run_prepare(
        _prepare_params(command=["/bin/echo", "a b"], rawCommand='/bin/echo "a b"')
    )
    assert result["plan"]["commandText"] == '/bin/echo "a b"'
    assert result["plan"]["commandPreview"] is None


@pytest.mark.parametrize(
    "argv",
    [
        ["/usr/bin/env", "-lc", "ls -la"],
        ["/bin/sh", "-x", "ls -la"],
        ["/bin/sh", "-lc", "ls -la", "extra"],
    ],
)
def test_prepare_rejects_shell_payload_outside_supported_form(
    data_dir: Path, roots: Path, argv: list[str]
) -> None:
    """Inline payload text is only accepted for the one supported wrapper shape."""
    result = exec_approvals.handle_system_run_prepare(
        _prepare_params(command=argv, rawCommand="ls -la")
    )
    assert result["error"] == "RAW_COMMAND_MISMATCH"


def test_set_requires_base_hash_for_an_existing_document(data_dir: Path) -> None:
    """An existing policy cannot be replaced without proving what was read."""
    path = data_dir / "exec-approvals.json"
    strict = _policy()
    strict["defaults"]["security"] = "deny"
    exec_approvals.handle_system_exec_approvals_set({"file": strict})
    original = path.read_bytes()

    permissive = _policy()
    permissive["defaults"]["security"] = "full"
    result = exec_approvals.handle_system_exec_approvals_set({"file": permissive})

    assert result["ok"] is False
    assert result["error"] == "INVALID_REQUEST"
    assert "base hash required" in result["message"]
    assert path.read_bytes() == original


def test_set_accepts_matching_base_hash_for_an_existing_document(data_dir: Path) -> None:
    """A caller holding the current hash may replace the policy."""
    exec_approvals.handle_system_exec_approvals_set({"file": _policy()})
    current = exec_approvals.handle_system_exec_approvals_get({})
    permissive = _policy()
    permissive["defaults"]["security"] = "full"

    result = exec_approvals.handle_system_exec_approvals_set(
        {"file": permissive, "baseHash": current["hash"]}
    )
    assert result["file"]["defaults"]["security"] == "full"


def test_set_rejects_mismatched_base_hash_for_a_missing_document(data_dir: Path) -> None:
    """A base hash for a file that does not exist must still match."""
    result = exec_approvals.handle_system_exec_approvals_set(
        {"file": _policy(), "baseHash": "stale"}
    )
    assert result["ok"] is False
    assert result["error"] == "INVALID_REQUEST"
    assert not (data_dir / "exec-approvals.json").exists()


@pytest.mark.parametrize(
    "document",
    [
        {"version": 1, "agents": {"clawd": {"allowlist": [{"pattern": "x", "lastUsedAt": "now"}]}}},
        {"version": 1, "agents": {"clawd": {"allowlist": [{"pattern": "x", "lastUsedAt": -1}]}}},
        {"version": 1, "agents": {"clawd": {"allowlist": [{"pattern": "x", "id": ""}]}}},
        {"version": 1, "agents": {"clawd": {"allowlist": [{"pattern": "x", "commandText": 7}]}}},
        {"version": 1, "agents": {"clawd": {"mcpTools": [{}]}}},
        {
            "version": 1,
            "agents": {
                "clawd": {
                    "mcpTools": [
                        {"server": "s", "tool": "t", "source": "allow-always", "addedAt": -1}
                    ]
                }
            },
        },
        {
            "version": 1,
            "agents": {
                "clawd": {
                    "mcpTools": [
                        {"server": " ", "tool": "t", "source": "allow-always", "addedAt": 0}
                    ]
                }
            },
        },
        {
            "version": 1,
            "agents": {
                "clawd": {
                    "mcpTools": [{"server": "s", "tool": "t", "source": "manual", "addedAt": 0}]
                }
            },
        },
        {
            "version": 1,
            "agents": {
                "clawd": {"mcpTools": [{"server": "s", "tool": "t", "source": "allow-always"}]}
            },
        },
        {
            "version": 1,
            "agents": {
                "clawd": {
                    "mcpTools": [
                        {
                            "server": "s",
                            "tool": "t",
                            "source": "allow-always",
                            "addedAt": 0,
                            "unknown": True,
                        }
                    ]
                }
            },
        },
    ],
)
def test_set_rejects_documents_the_gateway_schema_rejects(data_dir: Path, document: Any) -> None:
    """Nested policy content is validated against the closed Gateway schema."""
    result = exec_approvals.handle_system_exec_approvals_set({"file": document})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"
    assert not (data_dir / "exec-approvals.json").exists()


@pytest.mark.parametrize(
    "document",
    [
        # ``True == 1`` in Python, but ``Type.Literal(1)`` rejects a JSON boolean.
        {"version": True},
        # An explicit null is not an absent key for ``Type.Optional``.
        {"version": 1, "socket": None},
        {"version": 1, "defaults": None},
        {"version": 1, "agents": None},
        {"version": 1, "defaults": {"security": None}},
        {"version": 1, "defaults": {"autoAllowSkills": None}},
        {"version": 1, "agents": {"clawd": {"allowlist": None}}},
        {"version": 1, "agents": {"clawd": {"mcpTools": None}}},
        {"version": 1, "agents": {"clawd": {"allowlist": [{"pattern": "x", "argPattern": None}]}}},
        {"version": 1, "agents": {"clawd": {"allowlist": [{"pattern": "x", "lastUsedAt": None}]}}},
        {"version": 1, "agents": {"clawd": {"allowlist": [{"pattern": "x", "source": None}]}}},
        {"version": 1, "agents": {"clawd": {"allowlist": [{"pattern": "x", "id": None}]}}},
    ],
)
def test_set_rejects_values_python_would_coerce(data_dir: Path, document: Any) -> None:
    """Boolean/int and null/absent coercions cannot widen the accepted schema."""
    result = exec_approvals.handle_system_exec_approvals_set({"file": document})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"
    assert not (data_dir / "exec-approvals.json").exists()


@pytest.mark.parametrize(
    "document",
    [
        {"version": 1, "defaults": {"security": {}}},
        {"version": 1, "defaults": {"ask": []}},
        {"version": 1, "defaults": {"askFallback": {"a": 1}}},
    ],
)
def test_set_rejects_unhashable_policy_values_without_raising(
    data_dir: Path, document: Any
) -> None:
    """An unhashable policy value is a clean rejection, not a TypeError."""
    result = exec_approvals.handle_system_exec_approvals_set({"file": document})
    assert result["error"] == "INVALID_PARAM"


def test_set_accepts_fully_populated_schema_valid_document(data_dir: Path) -> None:
    """A document exercising every closed optional field is accepted."""
    document = _policy()
    document["agents"] = {
        "clawd": {
            "security": "allowlist",
            "allowlist": [
                {
                    "id": "entry-1",
                    "pattern": "/bin/true",
                    "source": "allow-always",
                    "commandText": "/bin/true",
                    "argPattern": "",
                    "lastUsedAt": 0,
                    "lastUsedCommand": "/bin/true",
                    "lastResolvedPath": "/bin/true",
                }
            ],
            "mcpTools": [
                {
                    "server": "openclaw",
                    "tool": "read",
                    "source": "allow-always",
                    "addedAt": 1,
                    "lastUsedAt": 2,
                }
            ],
        }
    }
    result = exec_approvals.handle_system_exec_approvals_set({"file": document})
    assert result["file"]["agents"]["clawd"]["mcpTools"][0]["tool"] == "read"


def test_get_never_discloses_the_socket_token(data_dir: Path) -> None:
    """The socket credential is redacted out of every snapshot response."""
    document = _policy()
    document["socket"] = {"path": "/run/openclaw/exec.sock", "token": "s3cret"}
    (data_dir / "exec-approvals.json").write_text(json.dumps(document), encoding="utf-8")

    result = exec_approvals.handle_system_exec_approvals_get({})
    assert result["file"]["socket"] == {"path": "/run/openclaw/exec.sock"}
    assert "s3cret" not in json.dumps(result)


def test_get_omits_socket_without_a_path(data_dir: Path) -> None:
    """A token-only socket entry is not echoed back as an empty object."""
    document = _policy()
    document["socket"] = {"token": "s3cret"}
    (data_dir / "exec-approvals.json").write_text(json.dumps(document), encoding="utf-8")

    result = exec_approvals.handle_system_exec_approvals_get({})
    assert "socket" not in result["file"]


def test_set_preserves_the_socket_token_across_a_redacted_round_trip(data_dir: Path) -> None:
    """Editing the redacted snapshot cannot erase the stored credential."""
    path = data_dir / "exec-approvals.json"
    document = _policy()
    document["socket"] = {"path": "/run/openclaw/exec.sock", "token": "s3cret"}
    path.write_text(json.dumps(document), encoding="utf-8")

    snapshot = exec_approvals.handle_system_exec_approvals_get({})
    edited = dict(snapshot["file"])
    edited["defaults"] = {**_policy()["defaults"], "ask": "always"}

    result = exec_approvals.handle_system_exec_approvals_set(
        {"file": edited, "baseHash": snapshot["hash"]}
    )
    assert result["file"]["socket"] == {"path": "/run/openclaw/exec.sock"}
    assert "s3cret" not in json.dumps(result)

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["socket"] == {"path": "/run/openclaw/exec.sock", "token": "s3cret"}
    assert stored["defaults"]["ask"] == "always"


def test_set_accepts_an_explicit_socket_replacement(data_dir: Path) -> None:
    """An explicitly supplied socket value still wins over the stored one."""
    path = data_dir / "exec-approvals.json"
    document = _policy()
    document["socket"] = {"path": "/run/openclaw/old.sock", "token": "old"}
    path.write_text(json.dumps(document), encoding="utf-8")
    snapshot = exec_approvals.handle_system_exec_approvals_get({})

    replacement = _policy()
    replacement["socket"] = {"path": "/run/openclaw/new.sock", "token": "new"}
    exec_approvals.handle_system_exec_approvals_set(
        {"file": replacement, "baseHash": snapshot["hash"]}
    )

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["socket"] == {"path": "/run/openclaw/new.sock", "token": "new"}


def test_set_does_not_fabricate_a_socket(data_dir: Path) -> None:
    """The node never mints a socket path or token of its own."""
    result = exec_approvals.handle_system_exec_approvals_set({"file": _policy()})
    assert "socket" not in result["file"]
    stored = json.loads((data_dir / "exec-approvals.json").read_text(encoding="utf-8"))
    assert "socket" not in stored


def test_commands_are_advertised() -> None:
    """Each native approval method is advertised to the Gateway."""
    from openclaw_node.gateway_ws import _NODE_COMMANDS as node_commands

    for command in (
        "system.run.prepare",
        "system.execApprovals.get",
        "system.execApprovals.set",
    ):
        assert command in node_commands
