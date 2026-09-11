"""Tests for node-side exec approval participation.

These cover the three commands a node host must advertise to take part in
OpenClaw's exec approval flow, and the properties an operator relies on:
a canonical plan that binds what was approved, and a policy document that
fails closed when absent or malformed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openclaw_node.commands import exec_approvals
from openclaw_node.commands.dispatcher import dispatch


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the approvals document at a temporary directory.

    Args:
        tmp_path: pytest temporary directory.
        monkeypatch: pytest patcher.

    Returns:
        The directory the approvals document is written to.
    """
    target = tmp_path / "data"
    target.mkdir()
    monkeypatch.setattr(exec_approvals, "_approvals_path", lambda: target / "exec-approvals.json")
    return target


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Constrain allowed roots to a temporary directory.

    Args:
        tmp_path: pytest temporary directory.
        monkeypatch: pytest patcher.

    Returns:
        The single allowed root.
    """
    root = tmp_path / "config"
    root.mkdir()
    monkeypatch.setattr(exec_approvals, "allowed_roots_for_env", lambda: (root,))
    return root


# --- system.run.prepare ---------------------------------------------------


def test_prepare_returns_canonical_plan(roots: Path) -> None:
    """A valid request yields argv, rawCommand, timeout, and a plan hash."""
    result = exec_approvals.handle_system_run_prepare({"cmd": ["ls", "-la"]})
    assert result["ok"] is True
    plan = result["systemRunPlan"]
    assert plan["argv"] == ["ls", "-la"]
    assert plan["rawCommand"] == "ls -la"
    assert plan["cwd"] is None
    assert plan["planHash"].startswith("sha256:")


def test_prepare_never_executes(roots: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Preparing a command must not start a process."""
    import subprocess

    def explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("system.run.prepare must not execute anything")

    monkeypatch.setattr(subprocess, "run", explode)
    result = exec_approvals.handle_system_run_prepare({"cmd": ["rm", "-rf", "/"]})
    assert result["ok"] is True


def test_prepare_rejects_shell_string(roots: Path) -> None:
    """A shell string is rejected rather than split."""
    result = exec_approvals.handle_system_run_prepare({"cmd": "ls -la; rm -rf /"})
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_PARAM"


def test_prepare_requires_cmd(roots: Path) -> None:
    """A missing command is a MISSING_PARAM error."""
    result = exec_approvals.handle_system_run_prepare({})
    assert result["ok"] is False
    assert result["error"]["code"] == "MISSING_PARAM"


def test_prepare_rejects_nul_byte(roots: Path) -> None:
    """NUL bytes in arguments are rejected."""
    result = exec_approvals.handle_system_run_prepare({"cmd": ["ls", "a\x00b"]})
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_PARAM"


def test_prepare_rejects_cwd_outside_roots(roots: Path, tmp_path: Path) -> None:
    """A working directory outside the allowed roots is refused.

    ``system.run`` documented this restriction without enforcing it; the
    plan path enforces it so an approval cannot carry an escaping cwd.
    """
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = exec_approvals.handle_system_run_prepare({"cmd": ["ls"], "cwd": str(outside)})
    assert result["ok"] is False
    assert result["error"]["code"] == "PATH_NOT_ALLOWED"


def test_prepare_accepts_cwd_inside_roots(roots: Path) -> None:
    """A working directory beneath an allowed root resolves into the plan."""
    inner = roots / "scripts"
    inner.mkdir()
    result = exec_approvals.handle_system_run_prepare({"cmd": ["ls"], "cwd": str(inner)})
    assert result["ok"] is True
    assert result["systemRunPlan"]["cwd"] == str(inner)


def test_prepare_clamps_timeout(roots: Path) -> None:
    """An oversized timeout is clamped to the node maximum."""
    from openclaw_node.commands.system_run import max_timeout_s

    result = exec_approvals.handle_system_run_prepare({"cmd": ["ls"], "timeout": 999_999})
    assert result["systemRunPlan"]["timeoutSeconds"] == max_timeout_s()


def test_prepare_rejects_nonpositive_timeout(roots: Path) -> None:
    """A zero or negative timeout is refused."""
    result = exec_approvals.handle_system_run_prepare({"cmd": ["ls"], "timeout": 0})
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_PARAM"


def test_plan_hash_binds_argv(roots: Path) -> None:
    """Changing any canonical field changes the hash."""
    base = exec_approvals.handle_system_run_prepare({"cmd": ["ls", "-la"]})
    changed_argv = exec_approvals.handle_system_run_prepare({"cmd": ["ls", "-l"]})
    changed_timeout = exec_approvals.handle_system_run_prepare({"cmd": ["ls", "-la"], "timeout": 5})
    base_hash = base["systemRunPlan"]["planHash"]
    assert changed_argv["systemRunPlan"]["planHash"] != base_hash
    assert changed_timeout["systemRunPlan"]["planHash"] != base_hash


def test_plan_hash_is_stable(roots: Path) -> None:
    """The same request produces the same hash."""
    first = exec_approvals.handle_system_run_prepare({"cmd": ["ls", "-la"]})
    second = exec_approvals.handle_system_run_prepare({"cmd": ["ls", "-la"]})
    assert first["systemRunPlan"]["planHash"] == second["systemRunPlan"]["planHash"]


# --- system.execApprovals.get / set ---------------------------------------


def test_get_missing_document_is_fail_closed(data_dir: Path) -> None:
    """An absent document reports the deny-by-default policy."""
    result = exec_approvals.handle_system_exec_approvals_get({})
    assert result["ok"] is True
    assert result["exists"] is False
    assert result["approvals"]["defaults"]["security"] == "deny"
    assert result["approvals"]["defaults"]["askFallback"] == "deny"


def test_get_corrupt_document_is_fail_closed(data_dir: Path) -> None:
    """A malformed document reports deny-by-default and flags itself."""
    (data_dir / "exec-approvals.json").write_text("{not json", encoding="utf-8")
    result = exec_approvals.handle_system_exec_approvals_get({})
    assert result["ok"] is True
    assert result["unreadable"] is True
    assert result["approvals"]["defaults"]["security"] == "deny"


def test_set_then_get_roundtrip(data_dir: Path) -> None:
    """A written document is returned verbatim on the next read."""
    document = {
        "defaults": {"security": "allowlist", "ask": "on-miss", "askFallback": "deny"},
        "agents": {"clawd": {"security": "allowlist", "ask": "always", "askFallback": "deny"}},
    }
    written = exec_approvals.handle_system_exec_approvals_set({"approvals": document})
    assert written["ok"] is True
    read_back = exec_approvals.handle_system_exec_approvals_get({})
    assert read_back["approvals"]["defaults"]["security"] == "allowlist"
    assert read_back["approvals"]["agents"]["clawd"]["ask"] == "always"
    assert read_back["approvals"]["version"] == exec_approvals.APPROVALS_DOC_VERSION


def test_set_rejects_invalid_security(data_dir: Path) -> None:
    """An unknown security value is refused."""
    result = exec_approvals.handle_system_exec_approvals_set(
        {"approvals": {"defaults": {"security": "yolo"}}}
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_PARAM"


def test_set_rejects_invalid_ask_fallback(data_dir: Path) -> None:
    """An unknown askFallback value is refused."""
    result = exec_approvals.handle_system_exec_approvals_set(
        {"approvals": {"defaults": {"security": "deny", "askFallback": "maybe"}}}
    )
    assert result["ok"] is False


def test_set_requires_defaults(data_dir: Path) -> None:
    """A document without a defaults block is refused."""
    result = exec_approvals.handle_system_exec_approvals_set({"approvals": {"agents": {}}})
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_PARAM"


def test_set_rejects_allowlist_without_pattern(data_dir: Path) -> None:
    """Allowlist entries require a string pattern."""
    result = exec_approvals.handle_system_exec_approvals_set(
        {"approvals": {"defaults": {"security": "allowlist", "allowlist": [{"id": "x"}]}}}
    )
    assert result["ok"] is False


def test_invalid_set_does_not_overwrite_existing(data_dir: Path) -> None:
    """A rejected write leaves the previous policy intact."""
    good = {"defaults": {"security": "allowlist", "ask": "on-miss", "askFallback": "deny"}}
    exec_approvals.handle_system_exec_approvals_set({"approvals": good})
    exec_approvals.handle_system_exec_approvals_set({"approvals": {"defaults": {"security": "no"}}})
    current = exec_approvals.handle_system_exec_approvals_get({})
    assert current["approvals"]["defaults"]["security"] == "allowlist"


def test_document_written_owner_only(data_dir: Path) -> None:
    """The approvals document is not world or group readable."""
    exec_approvals.handle_system_exec_approvals_set(
        {"approvals": {"defaults": {"security": "deny"}}}
    )
    mode = (data_dir / "exec-approvals.json").stat().st_mode & 0o777
    assert mode == 0o600


def test_no_temp_file_left_behind(data_dir: Path) -> None:
    """The atomic write leaves no stray temporary file."""
    exec_approvals.handle_system_exec_approvals_set(
        {"approvals": {"defaults": {"security": "deny"}}}
    )
    assert not list(data_dir.glob("*.tmp"))


def test_written_document_is_valid_json(data_dir: Path) -> None:
    """The persisted document parses as JSON."""
    exec_approvals.handle_system_exec_approvals_set(
        {"approvals": {"defaults": {"security": "deny"}}}
    )
    parsed = json.loads((data_dir / "exec-approvals.json").read_text(encoding="utf-8"))
    assert parsed["defaults"]["security"] == "deny"


# --- dispatcher wiring ----------------------------------------------------


@pytest.mark.parametrize(
    "command",
    ["system.run.prepare", "system.execApprovals.get", "system.execApprovals.set"],
)
def test_commands_are_dispatchable(command: str, data_dir: Path, roots: Path) -> None:
    """Each new command is reachable through the dispatcher."""
    result = dispatch(command, {})
    assert "ok" in result
    if not result["ok"]:
        assert result["error"]["code"] != "UNKNOWN_COMMAND"


def test_commands_are_advertised() -> None:
    """Each new command appears in the node's advertised command list."""
    from openclaw_node.gateway_ws import _NODE_COMMANDS as NODE_COMMANDS

    for command in (
        "system.run.prepare",
        "system.execApprovals.get",
        "system.execApprovals.set",
    ):
        assert command in NODE_COMMANDS


# --- validation edge cases ------------------------------------------------


def test_prepare_rejects_non_string_argv(roots: Path) -> None:
    """A list containing non-strings is refused."""
    result = exec_approvals.handle_system_run_prepare({"cmd": ["ls", 7]})
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_PARAM"


def test_prepare_rejects_empty_argv0(roots: Path) -> None:
    """An empty executable name is refused."""
    result = exec_approvals.handle_system_run_prepare({"cmd": ["", "-la"]})
    assert result["ok"] is False
    assert "non-empty" in result["error"]["message"]


def test_prepare_rejects_too_many_args(roots: Path) -> None:
    """An argument vector beyond the cap is refused."""
    result = exec_approvals.handle_system_run_prepare({"cmd": ["ls"] + ["-x"] * 400})
    assert result["ok"] is False
    assert "more than" in result["error"]["message"]


def test_prepare_rejects_overlong_arg(roots: Path) -> None:
    """A single oversized argument is refused."""
    result = exec_approvals.handle_system_run_prepare({"cmd": ["ls", "x" * 9000]})
    assert result["ok"] is False
    assert "longer than" in result["error"]["message"]


def test_prepare_rejects_non_integer_timeout(roots: Path) -> None:
    """A non-integer timeout is refused rather than coerced."""
    result = exec_approvals.handle_system_run_prepare({"cmd": ["ls"], "timeout": "soon"})
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_PARAM"


def test_prepare_rejects_cwd_that_is_a_file(roots: Path) -> None:
    """A cwd pointing at a file rather than a directory is refused."""
    target = roots / "notadir"
    target.write_text("x", encoding="utf-8")
    result = exec_approvals.handle_system_run_prepare({"cmd": ["ls"], "cwd": str(target)})
    assert result["ok"] is False
    assert "not a directory" in result["error"]["message"]


def test_prepare_reports_missing_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    """No configured roots is reported rather than silently allowing cwd."""
    monkeypatch.setattr(exec_approvals, "allowed_roots_for_env", tuple)
    result = exec_approvals.handle_system_run_prepare({"cmd": ["ls"], "cwd": "/tmp"})
    assert result["ok"] is False
    assert result["error"]["code"] == "NO_ALLOWED_ROOTS"


def test_get_non_object_document_is_fail_closed(data_dir: Path) -> None:
    """A JSON document that is not an object reports deny-by-default."""
    (data_dir / "exec-approvals.json").write_text("[1, 2, 3]", encoding="utf-8")
    result = exec_approvals.handle_system_exec_approvals_get({})
    assert result["unreadable"] is True
    assert result["approvals"]["defaults"]["security"] == "deny"


def test_set_rejects_invalid_ask(data_dir: Path) -> None:
    """An unknown ask value is refused."""
    result = exec_approvals.handle_system_exec_approvals_set(
        {"approvals": {"defaults": {"security": "deny", "ask": "sometimes"}}}
    )
    assert result["ok"] is False
    assert "ask must be one of" in result["error"]["message"]


def test_set_rejects_non_object_approvals(data_dir: Path) -> None:
    """A non-object payload is refused."""
    result = exec_approvals.handle_system_exec_approvals_set({"approvals": "deny everything"})
    assert result["ok"] is False


def test_set_rejects_non_list_allowlist(data_dir: Path) -> None:
    """An allowlist that is not a list is refused."""
    result = exec_approvals.handle_system_exec_approvals_set(
        {"approvals": {"defaults": {"security": "allowlist", "allowlist": "everything"}}}
    )
    assert result["ok"] is False
    assert "allowlist must be a list" in result["error"]["message"]


def test_set_rejects_non_object_agents(data_dir: Path) -> None:
    """An agents block that is not an object is refused."""
    result = exec_approvals.handle_system_exec_approvals_set(
        {"approvals": {"defaults": {"security": "deny"}, "agents": []}}
    )
    assert result["ok"] is False
    assert "agents must be an object" in result["error"]["message"]


def test_set_rejects_non_object_agent_policy(data_dir: Path) -> None:
    """An individual agent policy that is not an object is refused."""
    result = exec_approvals.handle_system_exec_approvals_set(
        {"approvals": {"defaults": {"security": "deny"}, "agents": {"clawd": "full"}}}
    )
    assert result["ok"] is False


def test_set_rejects_invalid_agent_policy(data_dir: Path) -> None:
    """A malformed per-agent policy is refused."""
    result = exec_approvals.handle_system_exec_approvals_set(
        {"approvals": {"defaults": {"security": "deny"}, "agents": {"clawd": {"ask": "nope"}}}}
    )
    assert result["ok"] is False
    assert "agents.clawd.ask" in result["error"]["message"]


def test_set_reports_write_failure(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unwritable destination is reported as IO_ERROR, not a crash."""
    import os as _os

    def deny(*args: Any, **kwargs: Any) -> int:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(_os, "open", deny)
    result = exec_approvals.handle_system_exec_approvals_set(
        {"approvals": {"defaults": {"security": "deny"}}}
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "IO_ERROR"


def test_approvals_path_uses_node_data_dir() -> None:
    """The document lives beside other node state, not in a stray location."""
    path = exec_approvals._approvals_path()
    assert path.name == "exec-approvals.json"
