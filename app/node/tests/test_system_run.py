"""Tests for openclaw_node.commands.system_run.

``system.run`` executes a Gateway-forwarded approved plan. Every valid test
input carries the native approval envelope (``systemRunPlan``, ``runId``,
``approvalDecision``/``approvalSource``/``approved``); refusing the envelope
is exercised explicitly. Timeout is expressed in ``timeoutMs`` (native wire
contract) and the successful payload uses ``success``/``exitCode``/
``timedOut``.
"""

from __future__ import annotations

import contextlib
import os
import signal
import time
from pathlib import Path
from typing import Any

import pytest

from openclaw_node.commands.system_run import (
    _is_blocked_key,
    _merge_env,
    handle_system_run,
)


def _plan(**overrides: Any) -> dict[str, Any]:
    """Return an approval-envelope-shaped systemRunPlan for the tests.

    Includes every approval-bound field the node handler now requires to be
    present. Optional fields default to ``None`` (the shape the Gateway
    forwards when the operator did not bind them).
    """
    base: dict[str, Any] = {
        "argv": ["true"],
        "commandText": "true",
        "cwd": None,
        "agentId": None,
        "sessionKey": None,
    }
    base.update(overrides)
    return base


def _params(**overrides: Any) -> dict[str, Any]:
    """Return a minimal, authorized Gateway-forwarded plan.

    ``systemRunPlan.commandText`` is the canonical ``_format_exec_command``
    rendering of the argv, matching what ``system.run.prepare`` would have
    stored. Callers can override the plan with ``systemRunPlan={...}``.
    """
    from openclaw_node.commands.exec_approvals import _format_exec_command

    argv = overrides.get("command", ["true"])
    plan_overrides = overrides.pop("systemRunPlan", None)
    plan_kwargs: dict[str, Any] = {
        "argv": list(argv),
        "commandText": _format_exec_command(list(argv)),
    }
    base_plan = _plan(**plan_kwargs)
    if plan_overrides is not None:
        base_plan.update(plan_overrides)
    base: dict[str, Any] = {
        "command": list(argv),
        "systemRunPlan": base_plan,
        "runId": "run-uuid",
        "approvalDecision": "allow-once",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _allow_tmp_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Standalone mode looks up allowed roots from an env var."""
    monkeypatch.setenv("OPENCLAW_ALLOWED_ROOTS", str(tmp_path))


# ---------------------------------------------------------------------------
# _is_blocked_key / _merge_env
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "OPENCLAW_ADMIN_TOKEN",
        "SECRET_KEY",
        "DB_PASSWORD",
        "AWS_CREDENTIAL",
        "MY_AUTH_HEADER",
        "API_KEY",
        "GH_TOKEN",
        "PWD",
    ],
)
def test_is_blocked_key_blocks_sensitive(key: str) -> None:
    assert _is_blocked_key(key)


@pytest.mark.parametrize("key", ["PATH", "HOME", "LANG", "TZ", "DISPLAY", "TERM"])
def test_is_blocked_key_allows_safe(key: str) -> None:
    assert not _is_blocked_key(key)


def test_merge_env_safe_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/root")
    result = _merge_env({"DISPLAY": ":0"})
    assert result is not None
    assert result["DISPLAY"] == ":0"
    assert "PATH" in result


def test_merge_env_blocked_key_returns_none() -> None:
    assert _merge_env({"MY_SECRET": "sssh"}) is None


def test_merge_env_caller_key_overrides_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    result = _merge_env({"PATH": "/custom/bin"})
    assert result is not None
    assert result["PATH"] == "/custom/bin"


# ---------------------------------------------------------------------------
# handle_system_run — authorization envelope (fail closed)
# ---------------------------------------------------------------------------


def test_missing_system_run_plan_is_refused() -> None:
    """A direct Gateway-shaped dictionary without a stored plan is refused."""
    result = handle_system_run(
        {
            "command": ["true"],
            "runId": "run-uuid",
            "approvalDecision": "allow-once",
        }
    )
    assert result["ok"] is False
    assert result["error"] == "UNAUTHORIZED"


def test_missing_run_id_is_refused() -> None:
    result = handle_system_run(
        {
            "command": ["true"],
            "systemRunPlan": _plan(),
            "approvalDecision": "allow-once",
        }
    )
    assert result["error"] == "UNAUTHORIZED"


def test_missing_approval_signal_is_refused() -> None:
    result = handle_system_run(
        {
            "command": ["true"],
            "systemRunPlan": _plan(),
            "runId": "run-uuid",
        }
    )
    assert result["error"] == "UNAUTHORIZED"


def test_approved_true_alone_is_accepted() -> None:
    result = handle_system_run(
        {
            "command": ["true"],
            "systemRunPlan": _plan(),
            "runId": "run-uuid",
            "approved": True,
        }
    )
    assert result["ok"] is True
    assert result["success"] is True


def test_approval_source_non_empty_is_accepted() -> None:
    result = handle_system_run(
        {
            "command": ["true"],
            "systemRunPlan": _plan(),
            "runId": "run-uuid",
            "approvalSource": "ask-fallback",
        }
    )
    assert result["ok"] is True


def test_unknown_approval_decision_is_refused() -> None:
    result = handle_system_run(
        {
            "command": ["true"],
            "systemRunPlan": _plan(),
            "runId": "run-uuid",
            "approvalDecision": "bogus",
        }
    )
    assert result["error"] == "UNAUTHORIZED"


def test_system_run_plan_argv_mismatch_is_refused() -> None:
    result = handle_system_run(
        {
            "command": ["echo", "sneak"],
            "systemRunPlan": _plan(argv=["echo", "approved"], commandText="echo approved"),
            "runId": "run-uuid",
            "approvalDecision": "allow-once",
        }
    )
    assert result["error"] == "PLAN_MISMATCH"


def test_system_run_plan_cwd_mismatch_is_refused(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    result = handle_system_run(
        {
            "command": ["true"],
            "cwd": str(tmp_path),
            "systemRunPlan": _plan(argv=["true"], commandText="true", cwd=str(other.resolve())),
            "runId": "run-uuid",
            "approvalDecision": "allow-once",
        }
    )
    assert result["error"] == "PLAN_MISMATCH"


def test_system_run_plan_agent_id_mismatch_is_refused() -> None:
    result = handle_system_run(
        {
            "command": ["true"],
            "systemRunPlan": _plan(agentId="approved-agent"),
            "runId": "run-uuid",
            "approvalDecision": "allow-once",
            "agentId": "other-agent",
        }
    )
    assert result["error"] == "PLAN_MISMATCH"


# ---------------------------------------------------------------------------
# Regression: every approval-bound field must be PRESENT in the stored plan.
# A missing/null field in the stored plan is a refusal, not a skipped check;
# otherwise a partial plan lets the forward smuggle unchecked values.
# ---------------------------------------------------------------------------


def test_plan_missing_command_text_is_refused() -> None:
    plan = {"argv": ["true"]}  # commandText absent
    result = handle_system_run(
        {
            "command": ["true"],
            "rawCommand": "true",
            "systemRunPlan": plan,
            "runId": "run-uuid",
            "approvalDecision": "allow-once",
        }
    )
    assert result["error"] == "PLAN_MISMATCH"


def test_plan_null_command_text_is_refused() -> None:
    plan = {"argv": ["true"], "commandText": None}
    result = handle_system_run(
        {
            "command": ["true"],
            "rawCommand": "true",
            "systemRunPlan": plan,
            "runId": "run-uuid",
            "approvalDecision": "allow-once",
        }
    )
    assert result["error"] == "PLAN_MISMATCH"


def test_plan_missing_cwd_with_forwarded_cwd_is_refused(tmp_path: Path) -> None:
    plan = {"argv": ["true"], "commandText": "true"}  # cwd key absent
    result = handle_system_run(
        {
            "command": ["true"],
            "cwd": str(tmp_path),
            "systemRunPlan": plan,
            "runId": "run-uuid",
            "approvalDecision": "allow-once",
        }
    )
    assert result["error"] == "PLAN_MISMATCH"


def test_plan_null_cwd_with_forwarded_cwd_is_refused(tmp_path: Path) -> None:
    plan = {"argv": ["true"], "commandText": "true", "cwd": None}
    result = handle_system_run(
        {
            "command": ["true"],
            "cwd": str(tmp_path),
            "systemRunPlan": plan,
            "runId": "run-uuid",
            "approvalDecision": "allow-once",
        }
    )
    assert result["error"] == "PLAN_MISMATCH"


def test_plan_missing_agent_id_with_forwarded_agent_id_is_refused() -> None:
    plan = {"argv": ["true"], "commandText": "true"}  # agentId key absent
    result = handle_system_run(
        {
            "command": ["true"],
            "systemRunPlan": plan,
            "runId": "run-uuid",
            "approvalDecision": "allow-once",
            "agentId": "drifted-agent",
        }
    )
    assert result["error"] == "PLAN_MISMATCH"


def test_plan_null_agent_id_with_forwarded_agent_id_is_refused() -> None:
    plan = {"argv": ["true"], "commandText": "true", "agentId": None}
    result = handle_system_run(
        {
            "command": ["true"],
            "systemRunPlan": plan,
            "runId": "run-uuid",
            "approvalDecision": "allow-once",
            "agentId": "drifted-agent",
        }
    )
    assert result["error"] == "PLAN_MISMATCH"


def test_plan_missing_session_key_with_forwarded_session_key_is_refused() -> None:
    plan = {"argv": ["true"], "commandText": "true"}  # sessionKey key absent
    result = handle_system_run(
        {
            "command": ["true"],
            "systemRunPlan": plan,
            "runId": "run-uuid",
            "approvalDecision": "allow-once",
            "sessionKey": "drifted-session",
        }
    )
    assert result["error"] == "PLAN_MISMATCH"


def test_plan_null_session_key_with_forwarded_session_key_is_refused() -> None:
    plan = {"argv": ["true"], "commandText": "true", "sessionKey": None}
    result = handle_system_run(
        {
            "command": ["true"],
            "systemRunPlan": plan,
            "runId": "run-uuid",
            "approvalDecision": "allow-once",
            "sessionKey": "drifted-session",
        }
    )
    assert result["error"] == "PLAN_MISMATCH"


def test_reviewer_reproduction_partial_plan_with_drifted_cwd_is_refused(
    tmp_path: Path,
) -> None:
    """Reviewer's P0 reproduction from PR #277 comment 5641299694.

    ``systemRunPlan={"argv":["true"]}`` plus forwarded ``rawCommand``,
    ``cwd``, ``agentId``, ``sessionKey`` must NOT execute; the handler
    must return PLAN_MISMATCH because the plan omits every approval-bound
    field beyond argv.
    """
    result = handle_system_run(
        {
            "command": ["true"],
            "rawCommand": "true",
            "cwd": str(tmp_path),
            "agentId": "drifted-agent",
            "sessionKey": "drifted-session",
            "systemRunPlan": {"argv": ["true"]},
            "runId": "run-uuid",
            "approvalDecision": "allow-once",
        }
    )
    assert result.get("ok") is False
    assert result["error"] == "PLAN_MISMATCH"


# ---------------------------------------------------------------------------
# handle_system_run — plan-shape validation (fail closed)
# ---------------------------------------------------------------------------


def test_missing_command_fails_closed() -> None:
    result = handle_system_run({})
    assert result["error"] == "MISSING_PARAM"


def test_command_as_string_rejected() -> None:
    result = handle_system_run({"command": "echo hi"})
    assert result["error"] == "INVALID_PARAM"


def test_command_non_string_elements_rejected() -> None:
    result = handle_system_run({"command": ["echo", 42]})
    assert result["error"] == "INVALID_PARAM"


def test_command_empty_list_rejected() -> None:
    result = handle_system_run({"command": []})
    assert result["error"] == "INVALID_PARAM"


def test_command_first_element_empty_rejected() -> None:
    result = handle_system_run({"command": ["", "arg"]})
    assert result["error"] == "INVALID_PARAM"


def test_command_with_nul_rejected() -> None:
    result = handle_system_run({"command": ["true\x00"]})
    assert result["error"] == "INVALID_PARAM"


def test_raw_command_mismatch_rejected() -> None:
    """A forwarded plan whose rawCommand disagrees with its argv is refused."""
    result = handle_system_run(_params(rawCommand="something else"))
    assert result["error"] == "RAW_COMMAND_MISMATCH"


def test_raw_command_matching_canonical_accepted() -> None:
    result = handle_system_run(_params(rawCommand="true"))
    assert result["ok"] is True


def test_raw_command_inline_shell_payload_accepted() -> None:
    """/bin/sh -c wrappers may present the inline payload as rawCommand."""
    result = handle_system_run(_params(command=["/bin/sh", "-c", "echo hi"], rawCommand="echo hi"))
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# handle_system_run — cwd validation binds to allowed roots
# ---------------------------------------------------------------------------


def test_cwd_outside_allowed_roots_rejected(tmp_path: Path) -> None:
    result = handle_system_run(_params(cwd="/etc"))
    assert result["error"] == "PATH_NOT_ALLOWED"


def test_cwd_blank_rejected() -> None:
    result = handle_system_run(_params(cwd="   "))
    assert result["error"] == "INVALID_PARAM"


def test_cwd_not_a_directory_rejected(tmp_path: Path) -> None:
    file_path = tmp_path / "regular"
    file_path.write_text("x")
    result = handle_system_run(_params(cwd=str(file_path)))
    assert result["error"] == "INVALID_PARAM"


def test_cwd_no_allowed_roots_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_ALLOWED_ROOTS", raising=False)
    result = handle_system_run(_params(cwd="/tmp"))
    assert result["error"] == "NO_ALLOWED_ROOTS"


def test_cwd_within_allowed_root_bound_to_subprocess(tmp_path: Path) -> None:
    result = handle_system_run(
        _params(
            command=["pwd"],
            cwd=str(tmp_path),
            systemRunPlan={
                "argv": ["pwd"],
                "commandText": "pwd",
                "cwd": str(tmp_path.resolve()),
            },
        )
    )
    assert result["ok"] is True
    assert result["stdout"].strip() == str(tmp_path.resolve())


# ---------------------------------------------------------------------------
# handle_system_run — env, timeout, identifiers
# ---------------------------------------------------------------------------


def test_env_blocked_key(tmp_path: Path) -> None:
    result = handle_system_run(_params(env={"MY_SECRET": "sssh"}))
    assert result["error"] == "INVALID_PARAM"


def test_env_non_dict_rejected() -> None:
    result = handle_system_run(_params(env="PATH=/usr/bin"))
    assert result["error"] == "INVALID_PARAM"


def test_env_empty_key_rejected() -> None:
    result = handle_system_run(_params(env={"": "x"}))
    assert result["error"] == "INVALID_PARAM"


def test_env_non_string_value_rejected() -> None:
    result = handle_system_run(_params(env={"X": 42}))
    assert result["error"] == "INVALID_PARAM"


def test_timeout_ms_capped_at_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_RUN_TIMEOUT_MAX_MS", "300")
    result = handle_system_run(_params(command=["sleep", "30"], timeoutMs=9_999_000))
    assert result["timedOut"] is True
    assert result["elapsed_ms"] < 5_000


def test_timeout_ms_honored() -> None:
    """A short timeoutMs ends a long command well before the default timeout."""
    result = handle_system_run(_params(command=["sleep", "30"], timeoutMs=300))
    assert result["timedOut"] is True
    assert result["elapsed_ms"] < 5_000


def test_legacy_seconds_timeout_rejected() -> None:
    """The retired ``timeout`` (seconds) key is refused; only ``timeoutMs`` works."""
    result = handle_system_run(_params(timeout=30))
    assert result["error"] == "INVALID_PARAM"


def test_timeout_ms_negative_rejected() -> None:
    result = handle_system_run(_params(timeoutMs=-5))
    assert result["error"] == "INVALID_PARAM"


def test_timeout_ms_non_int_rejected() -> None:
    result = handle_system_run(_params(timeoutMs="forever"))
    assert result["error"] == "INVALID_PARAM"


def test_agent_id_blank_rejected() -> None:
    result = handle_system_run(_params(agentId="  "))
    assert result["error"] == "INVALID_PARAM"


def test_session_key_blank_rejected() -> None:
    result = handle_system_run(_params(sessionKey=""))
    assert result["error"] == "INVALID_PARAM"


def test_proposal_id_is_audit_metadata_only() -> None:
    """A proposalId travels with the invoke but never authorizes it."""
    result = handle_system_run(_params(proposalId="prop-123"))
    assert result["ok"] is True


def test_proposal_id_blank_rejected() -> None:
    result = handle_system_run(_params(proposalId=""))
    assert result["error"] == "INVALID_PARAM"


# ---------------------------------------------------------------------------
# handle_system_run — subprocess error handling
# ---------------------------------------------------------------------------


def test_timeout_returns_timed_out_payload() -> None:
    result = handle_system_run(_params(command=["sleep", "30"], timeoutMs=200))
    assert result["ok"] is True
    assert result["timedOut"] is True
    assert result["success"] is False
    assert result["exitCode"] is None


def test_binary_not_found() -> None:
    result = handle_system_run(_params(command=["no_such_binary"]))
    assert result["error"] == "NOT_FOUND"


def test_oserror_returns_exec_error(tmp_path: Path) -> None:
    not_executable = tmp_path / "plain.txt"
    not_executable.write_text("x")
    result = handle_system_run(_params(command=[str(not_executable)]))
    assert result["error"] == "EXEC_ERROR"


# ---------------------------------------------------------------------------
# handle_system_run — happy path and native result wire contract
# ---------------------------------------------------------------------------


def test_success_returns_native_payload_shape() -> None:
    result = handle_system_run(_params(command=["echo", "hi"], rawCommand="echo hi"))
    assert result["ok"] is True
    assert result["success"] is True
    assert result["exitCode"] == 0
    assert result["timedOut"] is False
    assert result["stdout"] == "hi\n"
    assert result["stderr"] == ""
    assert "outputTruncated" not in result
    assert "elapsed_ms" in result


def test_nonzero_exit_reports_success_false() -> None:
    result = handle_system_run(_params(command=["false"]))
    assert result["ok"] is True
    assert result["success"] is False
    assert result["exitCode"] == 1


def test_stderr_is_captured() -> None:
    result = handle_system_run(_params(command=["sh", "-c", "echo error >&2"]))
    assert result["stderr"] == "error\n"


def test_subprocess_never_uses_shell() -> None:
    """Shell metacharacters in argv are passed literally, never interpreted."""
    result = handle_system_run(_params(command=["echo", "a; echo b"]))
    assert result["stdout"] == "a; echo b\n"


def test_subprocess_receives_sanitised_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DANGEROUS_VAR", "should_not_appear")
    result = handle_system_run(_params(command=["env"]))
    assert "DANGEROUS_VAR" not in result["stdout"]


def test_subprocess_argv_matches_plan() -> None:
    result = handle_system_run(_params(command=["echo", "hi", "there"], rawCommand="echo hi there"))
    assert result["stdout"] == "hi there\n"


# ---------------------------------------------------------------------------
# handle_system_run — argv/env/output bounds and process-group teardown
# ---------------------------------------------------------------------------

_ARGV_CAP = 128 * 1024
_OUTPUT_CAP = 256 * 1024


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state != "Z"


def _wait_dead(pid: int) -> bool:
    for _ in range(100):
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_output_truncated_per_stream_after_capture() -> None:
    result = handle_system_run(
        _params(command=["sh", "-c", "head -c 400000 /dev/zero; head -c 400000 /dev/zero >&2"])
    )
    assert result["success"] is True
    assert len(result["stdout"]) == _OUTPUT_CAP
    assert len(result["stderr"]) == _OUTPUT_CAP


def test_timeout_kills_grandchildren(tmp_path: Path) -> None:
    pidfile = tmp_path / "gc.pid"
    script = f"sleep 60 & echo $! > {pidfile}; sleep 60"
    result = handle_system_run(_params(command=["sh", "-c", script], timeoutMs=500))
    assert result["timedOut"] is True
    assert result["elapsed_ms"] < 10_000
    assert _wait_dead(int(pidfile.read_text()))


def test_timeout_abandons_detached_pipe_holder(tmp_path: Path) -> None:
    pidfile = tmp_path / "detached.pid"
    script = f"setsid sh -c 'echo $$ > {pidfile}; exec sleep 5' & sleep 30"
    t0 = time.monotonic()
    try:
        result = handle_system_run(_params(command=["sh", "-c", script], timeoutMs=200))
        elapsed = time.monotonic() - t0
        assert result["timedOut"] is True
        assert result["outputIncomplete"] is True
        assert elapsed < 2.5
    finally:
        deadline = time.monotonic() + 2
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        if pidfile.exists():
            with contextlib.suppress(ProcessLookupError, ValueError):
                os.kill(int(pidfile.read_text()), signal.SIGKILL)


def test_timeout_without_detached_holder_is_output_complete() -> None:
    result = handle_system_run(_params(command=["sleep", "30"], timeoutMs=200))
    assert result["timedOut"] is True
    assert result["outputIncomplete"] is False


def test_argv_over_cap_refused_before_spawn(tmp_path: Path) -> None:
    sentinel = tmp_path / "ran"
    arg = "a" * 8000
    padding = [arg] * (_ARGV_CAP // 8001 + 1)
    argv = ["sh", "-c", f"touch {sentinel}", *padding]
    assert sum(len(a) + 1 for a in argv) > _ARGV_CAP
    result = handle_system_run(_params(command=argv))
    assert result["ok"] is False
    assert result["error"] == "ARGV_TOO_LARGE"
    assert not sentinel.exists()


def test_argv_at_cap_succeeds() -> None:
    argv = ["echo", *["a" * 8000 for _ in range(16)]]
    argv.append("b" * (_ARGV_CAP - sum(len(a) + 1 for a in argv) - 1))
    assert sum(len(a) + 1 for a in argv) == _ARGV_CAP
    result = handle_system_run(_params(command=argv))
    assert result["ok"] is True
    assert result["success"] is True


def test_env_over_cap_refused_before_spawn(tmp_path: Path) -> None:
    sentinel = tmp_path / "ran"
    env = {f"E{i}": "v" * 4000 for i in range(40)}
    result = handle_system_run(_params(command=["touch", str(sentinel)], env=env))
    assert result["error"] == "ENV_TOO_LARGE"
    assert not sentinel.exists()


def test_env_under_cap_succeeds() -> None:
    env = {f"E{i}": "v" * 3000 for i in range(20)}
    result = handle_system_run(_params(command=["true"], env=env))
    assert result["success"] is True


def test_normal_exit_leaves_background_grandchild_alive(tmp_path: Path) -> None:
    pidfile = tmp_path / "bg.pid"
    script = f"sleep 30 >/dev/null 2>&1 & echo $! > {pidfile}; echo fg"
    result = handle_system_run(_params(command=["sh", "-c", script], timeoutMs=20_000))
    pid = int(pidfile.read_text())
    try:
        assert result["success"] is True
        assert result["stdout"] == "fg\n"
        assert result["elapsed_ms"] < 5_000
        assert _alive(pid)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def test_exited_child_with_pipe_holding_background_not_timed_out() -> None:
    result = handle_system_run(
        _params(command=["sh", "-c", "sleep 5 & echo $! >&2; echo fg"], timeoutMs=300)
    )
    pid = int(result["stderr"].strip())
    try:
        assert result["timedOut"] is False
        assert result["success"] is True
        assert result["exitCode"] == 0
        assert result["stdout"] == "fg\n"
        assert result["outputIncomplete"] is True
        assert _alive(pid)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def test_exited_child_nonzero_with_pipe_holding_background_reports_exit_code() -> None:
    result = handle_system_run(
        _params(command=["sh", "-c", "sleep 5 & echo $! >&2; exit 3"], timeoutMs=300)
    )
    pid = int(result["stderr"].strip())
    try:
        assert result["timedOut"] is False
        assert result["success"] is False
        assert result["exitCode"] == 3
        assert result["outputIncomplete"] is True
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def test_clean_exit_output_is_complete() -> None:
    result = handle_system_run(_params(command=["echo", "hi"]))
    assert result["outputIncomplete"] is False


def test_argv_unencodable_value_refused_before_spawn(tmp_path: Path) -> None:
    sentinel = tmp_path / "ran"
    argv = ["sh", "-c", f"touch {sentinel}", "\ud800"]
    result = handle_system_run(_params(command=argv))
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"
    assert not sentinel.exists()


def test_env_unencodable_value_refused_before_spawn(tmp_path: Path) -> None:
    sentinel = tmp_path / "ran"
    result = handle_system_run(_params(command=["touch", str(sentinel)], env={"V": "\ud800"}))
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"
    assert not sentinel.exists()
