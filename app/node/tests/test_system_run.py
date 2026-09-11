"""Tests for openclaw_node.commands.system_run.

``system.run`` executes a Gateway-forwarded approved plan. Every test drives
the handler with the canonical plan shape (``command`` argv, optional
``cwd``/``env``/``timeout``/``rawCommand``/``agentId``/``sessionKey``/
``proposalId``); there is no add-on admin token to configure or bypass.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch as mock_patch

import pytest

from openclaw_node.commands.system_run import (
    _is_blocked_key,
    _merge_env,
    handle_system_run,
)


def _params(**overrides: Any) -> dict[str, Any]:
    """Return a minimal valid Gateway-forwarded plan."""
    base: dict[str, Any] = {"command": ["true"]}
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


def test_raw_command_matching_canonical_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = subprocess.CompletedProcess(args=["true"], returncode=0, stdout=b"", stderr=b"")
    with mock_patch("subprocess.run", return_value=proc):
        result = handle_system_run(_params(rawCommand="true"))
    assert result["ok"] is True


def test_raw_command_inline_shell_payload_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """/bin/sh -c wrappers may present the inline payload as rawCommand."""
    proc = subprocess.CompletedProcess(args=["/bin/sh"], returncode=0, stdout=b"", stderr=b"")
    with mock_patch("subprocess.run", return_value=proc):
        result = handle_system_run(
            {"command": ["/bin/sh", "-c", "echo hi"], "rawCommand": "echo hi"}
        )
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# handle_system_run — cwd validation binds to allowed roots
# ---------------------------------------------------------------------------


def test_cwd_outside_allowed_roots_rejected(tmp_path: Path) -> None:
    outside = "/etc"
    result = handle_system_run(_params(cwd=outside))
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
    captured: list[dict[str, Any]] = []
    proc = subprocess.CompletedProcess(args=["pwd"], returncode=0, stdout=b"", stderr=b"")

    def _fake(_argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured.append(kwargs)
        return proc

    with mock_patch("subprocess.run", side_effect=_fake):
        result = handle_system_run(_params(command=["pwd"], cwd=str(tmp_path)))
    assert result["ok"] is True
    assert captured[0]["cwd"] == str(tmp_path.resolve())


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


def test_timeout_capped_at_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_RUN_TIMEOUT_MAX", "10")
    captured: list[dict[str, Any]] = []
    proc = subprocess.CompletedProcess(args=["true"], returncode=0, stdout=b"", stderr=b"")

    def _fake(_argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured.append(kwargs)
        return proc

    with mock_patch("subprocess.run", side_effect=_fake):
        handle_system_run(_params(timeout=9999))

    assert captured[0]["timeout"] == 10


def test_timeout_negative_rejected() -> None:
    result = handle_system_run(_params(timeout=-5))
    assert result["error"] == "INVALID_PARAM"


def test_timeout_non_int_rejected() -> None:
    result = handle_system_run(_params(timeout="forever"))
    assert result["error"] == "INVALID_PARAM"


def test_agent_id_blank_rejected() -> None:
    result = handle_system_run(_params(agentId="  "))
    assert result["error"] == "INVALID_PARAM"


def test_session_key_blank_rejected() -> None:
    result = handle_system_run(_params(sessionKey=""))
    assert result["error"] == "INVALID_PARAM"


def test_proposal_id_is_audit_metadata_only() -> None:
    """A proposalId travels with the invoke but never authorizes it."""
    proc = subprocess.CompletedProcess(args=["true"], returncode=0, stdout=b"", stderr=b"")
    with mock_patch("subprocess.run", return_value=proc):
        result = handle_system_run(_params(proposalId="prop-123"))
    assert result["ok"] is True


def test_proposal_id_blank_rejected() -> None:
    result = handle_system_run(_params(proposalId=""))
    assert result["error"] == "INVALID_PARAM"


# ---------------------------------------------------------------------------
# handle_system_run — subprocess error handling
# ---------------------------------------------------------------------------


def test_timeout_returns_timeout_error() -> None:
    with mock_patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(["true"], 30),
    ):
        result = handle_system_run(_params())
    assert result["error"] == "TIMEOUT"


def test_binary_not_found() -> None:
    with mock_patch("subprocess.run", side_effect=FileNotFoundError("no such binary")):
        result = handle_system_run(_params(command=["no_such_binary"]))
    assert result["error"] == "NOT_FOUND"


def test_oserror_returns_exec_error() -> None:
    with mock_patch("subprocess.run", side_effect=OSError("permission denied")):
        result = handle_system_run(_params())
    assert result["error"] == "EXEC_ERROR"


# ---------------------------------------------------------------------------
# handle_system_run — happy path and subprocess shape
# ---------------------------------------------------------------------------


def test_success_returns_ok() -> None:
    proc = subprocess.CompletedProcess(args=["echo"], returncode=0, stdout=b"hi\n", stderr=b"")
    with mock_patch("subprocess.run", return_value=proc):
        result = handle_system_run(_params(command=["echo", "hi"]))
    assert result["ok"] is True
    assert result["stdout"] == "hi\n"
    assert result["stderr"] == ""
    assert result["returncode"] == 0
    assert "elapsed_ms" in result


def test_nonzero_exit_still_ok() -> None:
    proc = subprocess.CompletedProcess(args=["false"], returncode=1, stdout=b"", stderr=b"error\n")
    with mock_patch("subprocess.run", return_value=proc):
        result = handle_system_run(_params())
    assert result["ok"] is True
    assert result["returncode"] == 1


def test_stdout_truncated_at_limit() -> None:
    big = b"x" * (300 * 1024)
    proc = subprocess.CompletedProcess(args=["cat"], returncode=0, stdout=big, stderr=b"")
    with mock_patch("subprocess.run", return_value=proc):
        result = handle_system_run(_params())
    assert result["ok"] is True
    assert len(result["stdout"]) == 256 * 1024


def test_subprocess_never_uses_shell() -> None:
    captured: list[dict[str, Any]] = []
    proc = subprocess.CompletedProcess(args=["true"], returncode=0, stdout=b"", stderr=b"")

    def _fake(_argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured.append(kwargs)
        return proc

    with mock_patch("subprocess.run", side_effect=_fake):
        handle_system_run(_params())

    assert captured
    assert not captured[0].get("shell", False)


def test_subprocess_receives_sanitised_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DANGEROUS_VAR", "should_not_appear")
    captured: list[dict[str, Any]] = []
    proc = subprocess.CompletedProcess(args=["true"], returncode=0, stdout=b"", stderr=b"")

    def _fake(_argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured.append(kwargs)
        return proc

    with mock_patch("subprocess.run", side_effect=_fake):
        handle_system_run(_params())

    assert "DANGEROUS_VAR" not in captured[0]["env"]


def test_subprocess_argv_matches_plan() -> None:
    captured_argv: list[list[str]] = []
    proc = subprocess.CompletedProcess(args=["echo"], returncode=0, stdout=b"", stderr=b"")

    def _fake(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured_argv.append(argv)
        return proc

    with mock_patch("subprocess.run", side_effect=_fake):
        handle_system_run(_params(command=["echo", "hi", "there"]))

    assert captured_argv == [["echo", "hi", "there"]]
