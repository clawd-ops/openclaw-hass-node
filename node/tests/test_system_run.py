"""Tests for openclaw_node.commands.system_run."""

from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import patch as mock_patch

import pytest

from openclaw_node.commands.system_run import (
    _is_blocked_key,
    _merge_env,
    handle_system_run,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _params(**kwargs: Any) -> dict[str, Any]:
    """Return a minimal valid params dict with admin_token preset."""
    base: dict[str, Any] = {"cmd": ["true"], "admin_token": "test-token"}
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# _is_blocked_key
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


# ---------------------------------------------------------------------------
# _merge_env
# ---------------------------------------------------------------------------


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
# handle_system_run — admin token gate
# ---------------------------------------------------------------------------


def test_system_run_no_admin_token_env_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_ADMIN_TOKEN", raising=False)
    result = handle_system_run(_params(admin_token="anything"))
    assert result["error"] == "ADMIN_REQUIRED"
    assert "not configured" in result["message"]


def test_system_run_wrong_token_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "correct")
    result = handle_system_run(_params(admin_token="wrong"))
    assert result["error"] == "ADMIN_REQUIRED"


def test_system_run_empty_caller_token_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "correct")
    result = handle_system_run(_params(admin_token=""))
    assert result["error"] == "ADMIN_REQUIRED"


def test_system_run_missing_admin_token_param_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "correct")
    result = handle_system_run({"cmd": ["true"]})
    assert result["error"] == "ADMIN_REQUIRED"


# ---------------------------------------------------------------------------
# handle_system_run — cmd validation
# ---------------------------------------------------------------------------


def test_system_run_missing_cmd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    result = handle_system_run({"admin_token": "tok"})
    assert result["error"] == "MISSING_PARAM"


def test_system_run_shell_string_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    result = handle_system_run({"cmd": "echo hello", "admin_token": "tok"})
    assert result["error"] == "INVALID_PARAM"
    assert "list" in result["message"]


def test_system_run_cmd_non_string_elements_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    result = handle_system_run({"cmd": ["echo", 42], "admin_token": "tok"})
    assert result["error"] == "INVALID_PARAM"


# ---------------------------------------------------------------------------
# handle_system_run — env validation
# ---------------------------------------------------------------------------


def test_system_run_env_blocked_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    result = handle_system_run(_params(env={"MY_SECRET": "sssh"}, admin_token="tok"))
    assert result["error"] == "INVALID_PARAM"
    assert "blocked" in result["message"]


def test_system_run_env_non_dict_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    result = handle_system_run(_params(env="PATH=/usr/bin", admin_token="tok"))
    assert result["error"] == "INVALID_PARAM"


def test_system_run_env_non_string_value_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    result = handle_system_run(_params(env={"X": 42}, admin_token="tok"))  # type: ignore[arg-type]
    assert result["error"] == "INVALID_PARAM"


# ---------------------------------------------------------------------------
# handle_system_run — timeout validation
# ---------------------------------------------------------------------------


def test_system_run_timeout_capped_at_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("OPENCLAW_RUN_TIMEOUT_MAX", "10")
    captured: list[dict[str, Any]] = []

    proc = subprocess.CompletedProcess(args=["true"], returncode=0, stdout=b"", stderr=b"")

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured.append(kwargs)
        return proc

    with mock_patch("subprocess.run", side_effect=_fake_run):
        handle_system_run(_params(timeout=9999, admin_token="tok"))

    assert captured[0]["timeout"] == 10


def test_system_run_timeout_negative_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    result = handle_system_run(_params(timeout=-5, admin_token="tok"))
    assert result["error"] == "INVALID_PARAM"


def test_system_run_timeout_non_int_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    result = handle_system_run(_params(timeout="forever", admin_token="tok"))
    assert result["error"] == "INVALID_PARAM"


# ---------------------------------------------------------------------------
# handle_system_run — subprocess error handling
# ---------------------------------------------------------------------------


def test_system_run_timeout_returns_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    with mock_patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(["true"], 30),
    ):
        result = handle_system_run(_params(admin_token="tok"))
    assert result["error"] == "TIMEOUT"


def test_system_run_binary_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    with mock_patch("subprocess.run", side_effect=FileNotFoundError("no such binary")):
        result = handle_system_run(_params(cmd=["no_such_binary"], admin_token="tok"))
    assert result["error"] == "NOT_FOUND"


def test_system_run_oserror_returns_exec_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    with mock_patch("subprocess.run", side_effect=OSError("permission denied")):
        result = handle_system_run(_params(admin_token="tok"))
    assert result["error"] == "EXEC_ERROR"


# ---------------------------------------------------------------------------
# handle_system_run — happy path
# ---------------------------------------------------------------------------


def test_system_run_success_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    proc = subprocess.CompletedProcess(
        args=["echo"], returncode=0, stdout=b"hello\n", stderr=b""
    )
    with mock_patch("subprocess.run", return_value=proc):
        result = handle_system_run(_params(cmd=["echo", "hello"], admin_token="tok"))
    assert result["ok"] is True
    assert result["stdout"] == "hello\n"
    assert result["stderr"] == ""
    assert result["returncode"] == 0
    assert "elapsed_ms" in result


def test_system_run_nonzero_exit_still_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    proc = subprocess.CompletedProcess(
        args=["false"], returncode=1, stdout=b"", stderr=b"error\n"
    )
    with mock_patch("subprocess.run", return_value=proc):
        result = handle_system_run(_params(admin_token="tok"))
    assert result["ok"] is True
    assert result["returncode"] == 1


def test_system_run_stdout_truncated_at_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    big = b"x" * (300 * 1024)
    proc = subprocess.CompletedProcess(args=["cat"], returncode=0, stdout=big, stderr=b"")
    with mock_patch("subprocess.run", return_value=proc):
        result = handle_system_run(_params(admin_token="tok"))
    assert result["ok"] is True
    assert len(result["stdout"]) == 256 * 1024


# ---------------------------------------------------------------------------
# handle_system_run — subprocess call shape
# ---------------------------------------------------------------------------


def test_system_run_subprocess_no_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """subprocess.run must never be called with shell=True."""
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    captured: list[dict[str, Any]] = []
    proc = subprocess.CompletedProcess(args=["true"], returncode=0, stdout=b"", stderr=b"")

    def _fake(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured.append(kwargs)
        return proc

    with mock_patch("subprocess.run", side_effect=_fake):
        handle_system_run(_params(admin_token="tok"))

    assert captured
    assert not captured[0].get("shell", False)


def test_system_run_uses_sanitised_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """env passed to subprocess must not contain arbitrary OS env vars."""
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("DANGEROUS_VAR", "should_not_appear")
    captured: list[dict[str, Any]] = []
    proc = subprocess.CompletedProcess(args=["true"], returncode=0, stdout=b"", stderr=b"")

    def _fake(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured.append(kwargs)
        return proc

    with mock_patch("subprocess.run", side_effect=_fake):
        handle_system_run(_params(admin_token="tok"))

    assert "DANGEROUS_VAR" not in captured[0]["env"]


def test_system_run_cwd_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_ADMIN_TOKEN", "tok")
    captured: list[dict[str, Any]] = []
    proc = subprocess.CompletedProcess(args=["pwd"], returncode=0, stdout=b"", stderr=b"")

    def _fake(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured.append(kwargs)
        return proc

    with mock_patch("subprocess.run", side_effect=_fake):
        handle_system_run(_params(cmd=["pwd"], cwd="/tmp", admin_token="tok"))

    assert captured[0]["cwd"] == "/tmp"
