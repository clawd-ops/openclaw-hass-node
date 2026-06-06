"""Tests for system command handlers."""

from __future__ import annotations

import importlib
from unittest.mock import patch

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


def test_system_which_rejects_path_name() -> None:
    result = handle_system_which({"name": "bin/sh"})
    assert result["ok"] is False
    assert result["error"] == "NAME_REQUIRED"


def test_system_which_found_without_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _name: "/usr/bin/fake")
    result = handle_system_which({"name": "fake"})
    assert result["found"] is True
    assert result["path"] == "/usr/bin/fake"
    assert "version" not in result


def test_system_which_never_executes_resolved_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _name: "/tmp/payload")
    with patch("subprocess.run") as run:
        result = handle_system_which({"name": "payload"})
    run.assert_not_called()
    assert result == {"ok": True, "name": "payload", "found": True, "path": "/tmp/payload"}


def test_subprocess_not_imported_for_system_which() -> None:
    module = importlib.import_module(_MOD)
    assert not hasattr(module, "subprocess")
