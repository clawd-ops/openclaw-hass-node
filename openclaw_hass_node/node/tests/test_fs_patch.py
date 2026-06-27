"""Tests for openclaw_node.commands.fs_patch."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch as mock_patch

import pytest
from openclaw_node.commands.fs_patch import (
    _run_patch,
    handle_fs_patch,
    reset_store_for_testing,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate backup store and set allowed roots for every test."""
    reset_store_for_testing(tmp_path / "store")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("OPENCLAW_ALLOWED_ROOTS", str(allowed))
    monkeypatch.setenv("OPENCLAW_BACKUP_ROOT", str(tmp_path / "store"))


def _allowed_file(tmp_path: Path, name: str = "test.yaml", content: str = "") -> Path:
    """Create a file under the allowed root and return its path."""
    allowed = tmp_path / "allowed"
    p = allowed / name
    p.write_text(content, encoding="utf-8")
    return p


def _simple_patch(path: str, old_line: str, new_line: str) -> str:
    """Build a minimal unified diff replacing one line."""
    return f"--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,1 @@\n-{old_line}\n+{new_line}\n"


# ---------------------------------------------------------------------------
# _run_patch unit tests
# ---------------------------------------------------------------------------


_PATCH_ON_PATH = any(
    (Path(d) / "patch").exists() for d in os.environ.get("PATH", "").split(os.pathsep)
)

_skip_no_patch = pytest.mark.skipif(not _PATCH_ON_PATH, reason="'patch' binary not on PATH")


@_skip_no_patch
def test_run_patch_applies_simple_diff() -> None:
    original = b"hello world\n"
    diff = "--- a/test\n+++ b/test\n@@ -1 +1 @@\n-hello world\n+goodbye world\n"
    patched, _hunks = _run_patch(original, diff)
    assert patched == b"goodbye world\n"


@_skip_no_patch
def test_run_patch_dry_run_returns_empty_bytes() -> None:
    original = b"hello\n"
    diff = "--- a/test\n+++ b/test\n@@ -1 +1 @@\n-hello\n+goodbye\n"
    patched, _hunks = _run_patch(original, diff, dry_run=True)
    assert patched == b""


def test_run_patch_raises_file_not_found_when_binary_missing() -> None:
    with (
        mock_patch("subprocess.run", side_effect=FileNotFoundError("patch: not found")),
        pytest.raises(FileNotFoundError),
    ):
        _run_patch(b"x\n", "--- a\n+++ b\n")


def test_run_patch_raises_timeout_expired() -> None:
    with (
        mock_patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(["patch"], 30),
        ),
        pytest.raises(subprocess.TimeoutExpired),
    ):
        _run_patch(b"x\n", "--- a\n+++ b\n")


def test_run_patch_raises_runtime_on_nonzero_exit() -> None:
    proc = subprocess.CompletedProcess(
        args=["patch"],
        returncode=1,
        stdout=b"",
        stderr=b"Hunk #1 FAILED at 1.",
    )
    with (
        mock_patch("subprocess.run", return_value=proc),
        pytest.raises(RuntimeError, match="Hunk #1 FAILED"),
    ):
        _run_patch(b"x\n", "bad diff")


def test_run_patch_raises_runtime_on_nonzero_no_stderr() -> None:
    proc = subprocess.CompletedProcess(
        args=["patch"],
        returncode=1,
        stdout=b"",
        stderr=b"",
    )
    with (
        mock_patch("subprocess.run", return_value=proc),
        pytest.raises(RuntimeError, match="patch exited non-zero"),
    ):
        _run_patch(b"x\n", "bad diff")


def test_run_patch_subprocess_command_shape() -> None:
    """Verify subprocess.run is called with list-form args, --output, and input=; no shell."""
    proc = subprocess.CompletedProcess(
        args=["patch"],
        returncode=0,
        stdout=b"Hunk #1 succeeded",
        stderr=b"",
    )
    captured: list[Any] = []

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured.append({"cmd": cmd, "kwargs": kwargs})
        # Write a fake output file so _run_patch can read it.
        out_path = next(p for p in cmd if "patched" in p)
        Path(out_path).write_bytes(b"patched\n")
        return proc

    with mock_patch("subprocess.run", side_effect=_fake_run):
        _run_patch(b"original\n", "my unified diff")

    assert captured, "subprocess.run was not called"
    call = captured[0]
    cmd = call["cmd"]
    kwargs = call["kwargs"]

    assert cmd[0] == "patch", "binary must be 'patch'"
    assert "--unified" in cmd
    assert "--forward" in cmd
    assert "--output" in cmd
    # patch text must go through stdin, not shell-expanded args
    assert kwargs.get("input") == b"my unified diff"
    assert not kwargs.get("shell", False), "shell=True would be a command injection risk"


# ---------------------------------------------------------------------------
# handle_fs_patch — validation
# ---------------------------------------------------------------------------


def test_fs_patch_missing_path() -> None:
    result = handle_fs_patch({"patch": "--- a\n+++ b\n"})
    assert result["error"] == "MISSING_PARAM"
    assert "path" in result["message"]


def test_fs_patch_missing_patch() -> None:
    result = handle_fs_patch({"path": "/tmp/x.yaml"})
    assert result["error"] == "MISSING_PARAM"
    assert "patch" in result["message"]


def test_fs_patch_storage_readonly(tmp_path: Path) -> None:
    result = handle_fs_patch({"path": "/config/.storage/core.entity_registry", "patch": "x"})
    assert result["error"] == "STORAGE_READONLY"


def test_fs_patch_protected_root_proposal_required() -> None:
    result = handle_fs_patch({"path": "/config/automations.yaml", "patch": "x"})
    assert result["error"] == "PROPOSAL_REQUIRED"


def test_fs_patch_agent_bridge_forces_proposal(tmp_path: Path) -> None:
    p = _allowed_file(tmp_path, "notes.txt", "hello\n")
    result = handle_fs_patch({"path": str(p), "patch": "x", "agent_bridge": True})
    assert result["error"] == "PROPOSAL_REQUIRED"


def test_fs_patch_not_found(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    result = handle_fs_patch({"path": str(allowed / "nonexistent.yaml"), "patch": "x"})
    assert result["error"] == "NOT_FOUND"


def test_fs_patch_post_resolution_protected() -> None:
    """Symlink that resolves into a protected root is blocked post-resolution."""
    with mock_patch(
        "openclaw_node.commands.fs_write.resolve_safe",
        return_value=Path("/config/sneaky.yaml"),
    ):
        result = handle_fs_patch({"path": "/share/allowed/legit.yaml", "patch": "x"})
    assert result["error"] == "PROPOSAL_REQUIRED"


# ---------------------------------------------------------------------------
# handle_fs_patch — patch binary errors surfaced as correct error codes
# ---------------------------------------------------------------------------


def test_fs_patch_binary_not_found(tmp_path: Path) -> None:
    p = _allowed_file(tmp_path, "a.yaml", "hello\n")
    with mock_patch(
        "openclaw_node.commands.fs_patch._run_patch",
        side_effect=FileNotFoundError("patch not found"),
    ):
        result = handle_fs_patch({"path": str(p), "patch": "--- a\n+++ b\n"})
    assert result["error"] == "PATCH_BINARY_NOT_FOUND"


def test_fs_patch_timeout(tmp_path: Path) -> None:
    p = _allowed_file(tmp_path, "a.yaml", "hello\n")
    with mock_patch(
        "openclaw_node.commands.fs_patch._run_patch",
        side_effect=subprocess.TimeoutExpired(["patch"], 30),
    ):
        result = handle_fs_patch({"path": str(p), "patch": "--- a\n+++ b\n"})
    assert result["error"] == "PATCH_TIMEOUT"


def test_fs_patch_failed(tmp_path: Path) -> None:
    p = _allowed_file(tmp_path, "a.yaml", "hello\n")
    with mock_patch(
        "openclaw_node.commands.fs_patch._run_patch",
        side_effect=RuntimeError("Hunk #1 FAILED"),
    ):
        result = handle_fs_patch({"path": str(p), "patch": "--- a\n+++ b\n"})
    assert result["error"] == "PATCH_FAILED"
    # Error message must NOT leak raw patch stderr (keep it in server logs only).
    assert "Hunk #1 FAILED" not in result["message"]


# ---------------------------------------------------------------------------
# handle_fs_patch — read error
# ---------------------------------------------------------------------------


def test_fs_patch_read_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _allowed_file(tmp_path, "a.yaml", "hello\n")

    def boom(*a: object, **kw: object) -> bytes:
        raise OSError("permission denied")

    monkeypatch.setattr("openclaw_node.commands.fs_patch.read_bytes_safe", boom)
    result = handle_fs_patch({"path": str(p), "patch": "--- a\n+++ b\n"})
    assert result["error"] == "READ_ERROR"


# ---------------------------------------------------------------------------
# handle_fs_patch — backup failure aborts before write
# ---------------------------------------------------------------------------


def test_fs_patch_backup_error_aborts(tmp_path: Path) -> None:
    p = _allowed_file(tmp_path, "a.yaml", "hello\n")
    diff = "--- a/a.yaml\n+++ b/a.yaml\n@@ -1 +1 @@\n-hello\n+goodbye\n"
    original_content = p.read_text()
    with (
        mock_patch(
            "openclaw_node.commands.fs_patch._run_patch",
            return_value=(b"goodbye\n", 1),
        ),
        mock_patch(
            "openclaw_node.commands.fs_patch._get_store",
        ) as mock_store_fn,
    ):
        mock_store = mock_store_fn.return_value
        from openclaw_node.backup_store import BackupStoreError

        mock_store.capture.side_effect = BackupStoreError("disk full")
        result = handle_fs_patch({"path": str(p), "patch": diff})

    assert result["error"] == "BACKUP_ERROR"
    # File must be untouched.
    assert p.read_text() == original_content


# ---------------------------------------------------------------------------
# handle_fs_patch — write error
# ---------------------------------------------------------------------------


def test_fs_patch_write_error(tmp_path: Path) -> None:
    p = _allowed_file(tmp_path, "a.yaml", "hello\n")
    diff = "--- a/a.yaml\n+++ b/a.yaml\n@@ -1 +1 @@\n-hello\n+goodbye\n"
    with (
        mock_patch(
            "openclaw_node.commands.fs_patch._run_patch",
            return_value=(b"goodbye\n", 1),
        ),
        mock_patch(
            "openclaw_node.commands.fs_patch.atomic_write_safe",
            side_effect=OSError("no space left"),
        ),
    ):
        result = handle_fs_patch({"path": str(p), "patch": diff})
    assert result["error"] == "WRITE_ERROR"


# ---------------------------------------------------------------------------
# handle_fs_patch — dry_run
# ---------------------------------------------------------------------------


def test_fs_patch_dry_run_returns_applicable_count(tmp_path: Path) -> None:
    p = _allowed_file(tmp_path, "a.yaml", "hello\n")
    original_content = p.read_text()
    diff = "--- a/a.yaml\n+++ b/a.yaml\n@@ -1 +1 @@\n-hello\n+goodbye\n"
    with mock_patch(
        "openclaw_node.commands.fs_patch._run_patch",
        return_value=(b"", 1),
    ) as mock_run:
        result = handle_fs_patch({"path": str(p), "patch": diff, "dry_run": True})

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["hunks_applicable"] == 1
    # dry_run must pass dry_run=True to _run_patch
    mock_run.assert_called_once_with(b"hello\n", diff, dry_run=True)
    # File must be untouched.
    assert p.read_text() == original_content


def test_fs_patch_dry_run_no_backup_captured(tmp_path: Path) -> None:
    """dry_run must not write to the backup store."""
    p = _allowed_file(tmp_path, "a.yaml", "hello\n")
    diff = "--- a/a.yaml\n+++ b/a.yaml\n@@ -1 +1 @@\n-hello\n+goodbye\n"
    with (
        mock_patch(
            "openclaw_node.commands.fs_patch._run_patch",
            return_value=(b"", 1),
        ),
        mock_patch("openclaw_node.commands.fs_patch._get_store") as mock_store_fn,
    ):
        handle_fs_patch({"path": str(p), "patch": diff, "dry_run": True})
    mock_store_fn.assert_not_called()


# ---------------------------------------------------------------------------
# handle_fs_patch — happy path (integration-level with real patch binary)
# ---------------------------------------------------------------------------


@_skip_no_patch
def test_fs_patch_applies_real_diff(tmp_path: Path) -> None:
    p = _allowed_file(tmp_path, "config.yaml", "# version: 1\nname: test\n")
    diff = (
        "--- a/config.yaml\n"
        "+++ b/config.yaml\n"
        "@@ -1,2 +1,2 @@\n"
        "-# version: 1\n"
        "+# version: 2\n"
        " name: test\n"
    )
    result = handle_fs_patch({"path": str(p), "patch": diff})
    assert result["ok"] is True
    assert result["path"] == str(p)
    assert "sha256" in result
    assert result["size"] > 0
    patched_content = p.read_text()
    assert "# version: 2" in patched_content
    assert "name: test" in patched_content


@_skip_no_patch
def test_fs_patch_sha256_matches_written_bytes(tmp_path: Path) -> None:
    p = _allowed_file(tmp_path, "config.yaml", "hello world\n")
    diff = "--- a/config.yaml\n+++ b/config.yaml\n@@ -1 +1 @@\n-hello world\n+goodbye world\n"
    result = handle_fs_patch({"path": str(p), "patch": diff})
    assert result["ok"] is True
    on_disk = p.read_bytes()
    assert result["sha256"] == hashlib.sha256(on_disk).hexdigest()
    assert result["size"] == len(on_disk)


@_skip_no_patch
def test_fs_patch_real_dry_run_leaves_file_unchanged(tmp_path: Path) -> None:
    p = _allowed_file(tmp_path, "config.yaml", "hello\n")
    diff = "--- a/config.yaml\n+++ b/config.yaml\n@@ -1 +1 @@\n-hello\n+goodbye\n"
    result = handle_fs_patch({"path": str(p), "patch": diff, "dry_run": True})
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert p.read_text() == "hello\n"


@_skip_no_patch
def test_fs_patch_real_bad_diff_returns_patch_failed(tmp_path: Path) -> None:
    p = _allowed_file(tmp_path, "config.yaml", "hello\n")
    # This diff targets a line that doesn't exist in the file.
    diff = (
        "--- a/config.yaml\n+++ b/config.yaml\n@@ -1 +1 @@\n-this line does not exist\n+replaced\n"
    )
    result = handle_fs_patch({"path": str(p), "patch": diff})
    assert result["error"] == "PATCH_FAILED"


# ---------------------------------------------------------------------------
# handle_fs_patch — actor and proposal_id plumbed through to backup
# ---------------------------------------------------------------------------


def test_fs_patch_custom_actor_and_proposal_id(tmp_path: Path) -> None:
    p = _allowed_file(tmp_path, "a.yaml", "v1\n")
    diff = "--- a/a.yaml\n+++ b/a.yaml\n@@ -1 +1 @@\n-v1\n+v2\n"
    with (
        mock_patch(
            "openclaw_node.commands.fs_patch._run_patch",
            return_value=(b"v2\n", 1),
        ),
        mock_patch("openclaw_node.commands.fs_patch._get_store") as mock_store_fn,
        mock_patch(
            "openclaw_node.commands.fs_patch.atomic_write_safe",
        ),
    ):
        mock_store = mock_store_fn.return_value
        mock_store.capture.return_value = type("V", (), {"sha256": "abc"})()
        handle_fs_patch(
            {
                "path": str(p),
                "patch": diff,
                "actor": "rob",
                "proposal_id": "prop-99",
            }
        )
    call_kwargs = mock_store.capture.call_args
    assert call_kwargs.kwargs["actor"] == "rob"
    assert call_kwargs.kwargs["proposal_id"] == "prop-99"
    assert call_kwargs.kwargs["op"] == "write"
