"""Tests for system command handlers."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from openclaw_node.commands.system import handle_system_which

_MOD = "openclaw_node.commands.system"


def test_system_which_missing_name() -> None:
    result = handle_system_which({})
    assert result["error"] == "NAME_REQUIRED"


def test_system_which_empty_name() -> None:
    result = handle_system_which({"name": ""})
    assert result["error"] == "NAME_REQUIRED"


def test_system_which_non_string_name() -> None:
    result = handle_system_which({"name": 42})
    assert result["error"] == "NAME_REQUIRED"


def test_system_which_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _name: None)
    result = handle_system_which({"name": "definitely-not-a-thing-xyz"})
    assert result["found"] is False
    assert result["ok"] is True


class _CP:
    """Stand-in for ``subprocess.CompletedProcess`` in tests."""

    def __init__(self, stdout: str = "", stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr


def test_system_which_found_with_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _name: "/usr/bin/fake")
    monkeypatch.setattr(f"{_MOD}.subprocess.run", lambda *a, **k: _CP(stdout="fake 1.2.3\nmore\n"))
    result = handle_system_which({"name": "fake"})
    assert result["found"] is True
    assert result["path"] == "/usr/bin/fake"
    assert result["version"] == "fake 1.2.3"


def test_system_which_version_from_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _name: "/bin/foo")
    monkeypatch.setattr(f"{_MOD}.subprocess.run", lambda *a, **k: _CP(stderr="foo 9.9\n"))
    result = handle_system_which({"name": "foo"})
    assert result["version"] == "foo 9.9"


def test_system_which_version_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _name: "/bin/foo")
    monkeypatch.setattr(f"{_MOD}.subprocess.run", lambda *a, **k: _CP())
    result = handle_system_which({"name": "foo"})
    assert result["version"] is None


def test_system_which_version_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _name: "/bin/foo")

    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="foo", timeout=2.0)

    monkeypatch.setattr(f"{_MOD}.subprocess.run", _raise)
    result = handle_system_which({"name": "foo"})
    assert result["version"] is None


def test_system_which_version_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _name: "/bin/foo")

    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("boom")

    monkeypatch.setattr(f"{_MOD}.subprocess.run", _raise)
    result = handle_system_which({"name": "foo"})
    assert result["version"] is None
