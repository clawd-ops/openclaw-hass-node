"""Tests for openclaw_node.commands.fs_patch (pure-Python applier)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest

from openclaw_node.commands.fs_patch import (
    PatchApplyError,
    _apply_unified_diff,
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
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)


def _allowed_file(tmp_path: Path, name: str = "test.yaml", content: str = "") -> Path:
    """Create a file under the allowed root and return its path."""
    allowed = tmp_path / "allowed"
    p = allowed / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _apply_unified_diff — pure-Python applier
# ---------------------------------------------------------------------------


def test_apply_unified_diff_single_line_replace() -> None:
    original = b"hello world\n"
    diff = "--- a/test\n+++ b/test\n@@ -1 +1 @@\n-hello world\n+goodbye world\n"
    patched, hunks = _apply_unified_diff(original, diff)
    assert patched == b"goodbye world\n"
    assert hunks == 1


def test_apply_unified_diff_multi_hunk() -> None:
    original = b"a\nb\nc\nd\ne\nf\ng\nh\ni\nj\n"
    # Replace 'b' at line 2 and 'i' at line 9 in two separate hunks.
    diff = (
        "--- a/test\n+++ b/test\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n@@ -8,3 +8,3 @@\n h\n-i\n+I\n j\n"
    )
    patched, hunks = _apply_unified_diff(original, diff)
    assert patched == b"a\nB\nc\nd\ne\nf\ng\nh\nI\nj\n"
    assert hunks == 2


def test_apply_unified_diff_pure_add() -> None:
    original = b"line one\nline two\n"
    diff = "--- a/test\n+++ b/test\n@@ -2,1 +2,2 @@\n line two\n+line three\n"
    patched, _ = _apply_unified_diff(original, diff)
    assert patched == b"line one\nline two\nline three\n"


def test_apply_unified_diff_pure_delete() -> None:
    original = b"keep\nremove\nkeep2\n"
    diff = "--- a/test\n+++ b/test\n@@ -1,3 +1,2 @@\n keep\n-remove\n keep2\n"
    patched, _ = _apply_unified_diff(original, diff)
    assert patched == b"keep\nkeep2\n"


def test_apply_unified_diff_context_mismatch_raises() -> None:
    original = b"actual line\n"
    diff = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-wrong line\n+replaced\n"
    with pytest.raises(PatchApplyError, match="mismatch"):
        _apply_unified_diff(original, diff)


def test_apply_unified_diff_no_newline_marker() -> None:
    """Handle the trailing `\\ No newline at end of file` marker."""
    original = b"only line without newline"
    diff = (
        "--- a/x\n"
        "+++ b/x\n"
        "@@ -1 +1 @@\n"
        "-only line without newline\n"
        "\\ No newline at end of file\n"
        "+replaced line without newline\n"
        "\\ No newline at end of file\n"
    )
    patched, _ = _apply_unified_diff(original, diff)
    assert patched == b"replaced line without newline"


def test_apply_unified_diff_ignores_file_headers() -> None:
    """`---`/`+++` file headers before the first `@@` are ignored."""
    original = b"x\n"
    diff = "--- a/whatever\n+++ b/whatever\nindex abc..def 100644\n@@ -1 +1 @@\n-x\n+y\n"
    patched, hunks = _apply_unified_diff(original, diff)
    assert patched == b"y\n"
    assert hunks == 1


def test_apply_unified_diff_malformed_hunk_header_raises() -> None:
    with pytest.raises(PatchApplyError, match="malformed hunk header"):
        _apply_unified_diff(b"x\n", "@@ garbage @@\n-x\n+y\n")


def test_apply_unified_diff_context_free_pure_insertion() -> None:
    """`old_count=0` insertions place the new lines *after* `old_start`.

    Regenerates the exact diff shape emitted by `difflib.unified_diff(..., n=0)`
    for a single-line insertion, which uses `@@ -1,0 +2 @@` — Codex-review
    #242 pass-3 medium finding.
    """
    original = b"a\nb\n"
    diff = "--- a/x\n+++ b/x\n@@ -1,0 +2 @@\n+x\n"
    patched, hunks = _apply_unified_diff(original, diff)
    assert patched == b"a\nx\nb\n"
    assert hunks == 1


def test_apply_unified_diff_pure_add_at_end_of_file() -> None:
    """Pure-add hunk with `old_start` == source length appends cleanly."""
    original = b"a\nb\n"
    diff = "--- a/x\n+++ b/x\n@@ -2,0 +3 @@\n+c\n"
    patched, _ = _apply_unified_diff(original, diff)
    assert patched == b"a\nb\nc\n"


def test_apply_unified_diff_pure_add_beyond_eof_raises() -> None:
    """A pure-add hunk targeting past the source end must be rejected."""
    original = b"a\nb\n"
    diff = "--- a/x\n+++ b/x\n@@ -99,0 +100 @@\n+x\n"
    with pytest.raises(PatchApplyError, match="beyond end of source"):
        _apply_unified_diff(original, diff)


def test_apply_unified_diff_pure_add_to_empty_file() -> None:
    """`@@ -0,0 +1 @@` — canonical add-to-empty-file hunk."""
    diff = "--- a/x\n+++ b/x\n@@ -0,0 +1 @@\n+first\n"
    patched, _ = _apply_unified_diff(b"", diff)
    assert patched == b"first\n"


def test_apply_unified_diff_overlapping_hunks_raises() -> None:
    original = b"a\nb\nc\n"
    diff = (
        "@@ -1,2 +1,2 @@\n a\n-b\n+B\n"
        "@@ -1,2 +1,2 @@\n a\n-B\n+X\n"  # second hunk targets same region
    )
    with pytest.raises(PatchApplyError):
        _apply_unified_diff(original, diff)


# ---------------------------------------------------------------------------
# _run_patch
# ---------------------------------------------------------------------------


def test_run_patch_applies_simple_diff() -> None:
    original = b"hello world\n"
    diff = "--- a/test\n+++ b/test\n@@ -1 +1 @@\n-hello world\n+goodbye world\n"
    patched, hunks = _run_patch(original, diff)
    assert patched == b"goodbye world\n"
    assert hunks == 1


def test_run_patch_dry_run_returns_empty_bytes_but_validates() -> None:
    original = b"hello\n"
    diff = "--- a/test\n+++ b/test\n@@ -1 +1 @@\n-hello\n+goodbye\n"
    patched, hunks = _run_patch(original, diff, dry_run=True)
    assert patched == b""
    assert hunks == 1


def test_run_patch_dry_run_still_raises_on_bad_diff() -> None:
    with pytest.raises(PatchApplyError):
        _run_patch(b"actual\n", "@@ -1 +1 @@\n-wrong\n+other\n", dry_run=True)


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
# handle_fs_patch — bad-diff error surface
# ---------------------------------------------------------------------------


def test_fs_patch_bad_diff_returns_patch_failed(tmp_path: Path) -> None:
    p = _allowed_file(tmp_path, "config.yaml", "hello\n")
    diff = (
        "--- a/config.yaml\n+++ b/config.yaml\n@@ -1 +1 @@\n-this line does not exist\n+replaced\n"
    )
    result = handle_fs_patch({"path": str(p), "patch": diff})
    assert result["error"] == "PATCH_FAILED"


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
    with mock_patch(
        "openclaw_node.commands.fs_patch._get_store",
    ) as mock_store_fn:
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
    with mock_patch(
        "openclaw_node.commands.fs_patch.atomic_write_safe",
        side_effect=OSError("no space left"),
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
    result = handle_fs_patch({"path": str(p), "patch": diff, "dry_run": True})

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["hunks_applicable"] == 1
    # File must be untouched.
    assert p.read_text() == original_content


def test_fs_patch_dry_run_no_backup_captured(tmp_path: Path) -> None:
    """dry_run must not write to the backup store."""
    p = _allowed_file(tmp_path, "a.yaml", "hello\n")
    diff = "--- a/a.yaml\n+++ b/a.yaml\n@@ -1 +1 @@\n-hello\n+goodbye\n"
    with mock_patch("openclaw_node.commands.fs_patch._get_store") as mock_store_fn:
        handle_fs_patch({"path": str(p), "patch": diff, "dry_run": True})
    mock_store_fn.assert_not_called()


# ---------------------------------------------------------------------------
# handle_fs_patch — happy path (integration-level, pure Python)
# ---------------------------------------------------------------------------


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
    assert patched_content == "# version: 2\nname: test\n"


def test_fs_patch_sha256_matches_written_bytes(tmp_path: Path) -> None:
    p = _allowed_file(tmp_path, "config.yaml", "hello world\n")
    diff = "--- a/config.yaml\n+++ b/config.yaml\n@@ -1 +1 @@\n-hello world\n+goodbye world\n"
    result = handle_fs_patch({"path": str(p), "patch": diff})
    assert result["ok"] is True
    on_disk = p.read_bytes()
    assert result["sha256"] == hashlib.sha256(on_disk).hexdigest()
    assert result["size"] == len(on_disk)


def test_fs_patch_real_dry_run_leaves_file_unchanged(tmp_path: Path) -> None:
    p = _allowed_file(tmp_path, "config.yaml", "hello\n")
    diff = "--- a/config.yaml\n+++ b/config.yaml\n@@ -1 +1 @@\n-hello\n+goodbye\n"
    result = handle_fs_patch({"path": str(p), "patch": diff, "dry_run": True})
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert p.read_text() == "hello\n"


# ---------------------------------------------------------------------------
# handle_fs_patch — actor and proposal_id plumbed through to backup
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Codex review #242 — regression: hunk-count validation and CRLF support
# ---------------------------------------------------------------------------


def test_apply_unified_diff_rejects_truncated_hunk() -> None:
    """Header declares 3 old lines but body walks only 1 — must reject."""
    original = b"a\nb\nc\n"
    diff = "@@ -1,3 +1,3 @@\n-a\n+A\n"  # truncated: only one line, header says 3
    with pytest.raises(PatchApplyError, match="hunk count mismatch"):
        _apply_unified_diff(original, diff)


def test_apply_unified_diff_rejects_declared_new_count_mismatch() -> None:
    """Header declares 2 new lines but body only adds 1."""
    original = b"a\n"
    diff = "@@ -1,1 +1,2 @@\n-a\n+A\n"
    with pytest.raises(PatchApplyError, match="hunk count mismatch"):
        _apply_unified_diff(original, diff)


def test_apply_unified_diff_rejects_empty_patch() -> None:
    """A patch with no hunk headers must not silently no-op."""
    with pytest.raises(PatchApplyError, match="no hunks"):
        _apply_unified_diff(b"a\n", "")


def test_apply_unified_diff_rejects_headers_only_patch() -> None:
    """File headers without a hunk body must not count as a valid patch."""
    with pytest.raises(PatchApplyError, match="no hunks"):
        _apply_unified_diff(b"a\n", "--- a/x\n+++ b/x\n")


def test_apply_unified_diff_preserves_crlf_line_endings() -> None:
    """A patch generated from a CRLF file must round-trip CRLF endings."""
    original = b"line one\r\nline two\r\nline three\r\n"
    # The patch body carries the source's own \r on each context/delete line.
    diff = (
        "--- a/x\r\n"
        "+++ b/x\r\n"
        "@@ -1,3 +1,3 @@\r\n"
        " line one\r\n"
        "-line two\r\n"
        "+LINE TWO\r\n"
        " line three\r\n"
    )
    patched, hunks = _apply_unified_diff(original, diff)
    assert patched == b"line one\r\nLINE TWO\r\nline three\r\n"
    assert hunks == 1


def test_fs_patch_rejects_truncated_hunk_via_handler(tmp_path: Path) -> None:
    """The handler surfaces truncated-hunk failures as PATCH_FAILED."""
    p = _allowed_file(tmp_path, "a.yaml", "a\nb\nc\n")
    diff = "--- a/a.yaml\n+++ b/a.yaml\n@@ -1,3 +1,3 @@\n-a\n+A\n"
    result = handle_fs_patch({"path": str(p), "patch": diff})
    assert result["error"] == "PATCH_FAILED"
    assert "hunk count mismatch" in result["message"]
    # File must be untouched after a rejected patch.
    assert p.read_text() == "a\nb\nc\n"


def test_fs_patch_custom_actor_and_proposal_id(tmp_path: Path) -> None:
    p = _allowed_file(tmp_path, "a.yaml", "v1\n")
    diff = "--- a/a.yaml\n+++ b/a.yaml\n@@ -1 +1 @@\n-v1\n+v2\n"
    with mock_patch("openclaw_node.commands.fs_patch._get_store") as mock_store_fn:
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
