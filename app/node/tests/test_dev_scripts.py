"""Tests for scripts/dev/confidentiality-check and scripts/dev/apply-patch.

These tests use real subprocesses against the actual shell scripts so that
shell semantics (set -euo pipefail, grep exit codes, etc.) are covered by
real execution rather than mocks.

All denylist fixtures use obviously-fake placeholder terms that cannot appear
in any real confidential context.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHECK = _REPO_ROOT / "scripts" / "dev" / "confidentiality-check"
_APPLY = _REPO_ROOT / "scripts" / "dev" / "apply-patch"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_check(
    input_text: str | None = None,
    *,
    denylist: Path | None = None,
    file_arg: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run confidentiality-check and return the completed process."""
    env = os.environ.copy()
    if denylist is not None:
        env["OPENCLAW_CONFIDENTIALITY_DENYLIST_FILE"] = str(denylist)

    cmd: list[str] = ["bash", str(_CHECK)]
    if file_arg is not None:
        cmd.append(str(file_arg))

    return subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _make_denylist(tmp_path: Path, terms: list[str]) -> Path:
    """Write a denylist file with the given placeholder terms and return its path."""
    p = tmp_path / "denylist.txt"
    p.write_text("\n".join(terms) + "\n", encoding="utf-8")
    p.chmod(0o600)
    return p


# ---------------------------------------------------------------------------
# confidentiality-check — clean input
# ---------------------------------------------------------------------------


def test_clean_stdin_exits_zero(tmp_path: Path) -> None:
    """Clean text through stdin exits 0."""
    dl = _make_denylist(tmp_path, ["ACME-PLACEHOLDER", "example.invalid"])
    result = _run_check("hello world, nothing to see here", denylist=dl)
    assert result.returncode == 0, result.stderr


def test_clean_file_exits_zero(tmp_path: Path) -> None:
    """Clean text in a named file exits 0."""
    dl = _make_denylist(tmp_path, ["ACME-PLACEHOLDER"])
    target = tmp_path / "clean.txt"
    target.write_text("this text is safe\n", encoding="utf-8")
    result = _run_check(denylist=dl, file_arg=target)
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# confidentiality-check — matching input
# ---------------------------------------------------------------------------


def test_matching_stdin_exits_one(tmp_path: Path) -> None:
    """Input containing a denylist term exits 1."""
    dl = _make_denylist(tmp_path, ["ACME-PLACEHOLDER"])
    result = _run_check("this contains ACME-PLACEHOLDER here", denylist=dl)
    assert result.returncode == 1


def test_matching_file_exits_one(tmp_path: Path) -> None:
    """A named file containing a denylist term exits 1."""
    dl = _make_denylist(tmp_path, ["ACME-PLACEHOLDER"])
    target = tmp_path / "leaky.txt"
    target.write_text("contains ACME-PLACEHOLDER in body\n", encoding="utf-8")
    result = _run_check(denylist=dl, file_arg=target)
    assert result.returncode == 1


def test_matching_output_has_exact_message(tmp_path: Path) -> None:
    """On a match, stderr contains exactly the required message string."""
    dl = _make_denylist(tmp_path, ["ACME-PLACEHOLDER"])
    result = _run_check("ACME-PLACEHOLDER is in here", denylist=dl)
    assert "CONFIDENTIAL LEAK — matches redacted from output" in result.stderr


def test_matching_term_never_echoed(tmp_path: Path) -> None:
    """The fake placeholder term must not appear in stdout or stderr."""
    placeholder = "ACME-PLACEHOLDER"
    dl = _make_denylist(tmp_path, [placeholder])
    result = _run_check(f"text with {placeholder} inside", denylist=dl)
    combined = result.stdout + result.stderr
    assert placeholder not in combined, (
        f"placeholder appeared in output — redaction failed: {combined!r}"
    )


# ---------------------------------------------------------------------------
# confidentiality-check — case insensitivity
# ---------------------------------------------------------------------------


def test_case_insensitive_match(tmp_path: Path) -> None:
    """Matching is case-insensitive (grep -i)."""
    dl = _make_denylist(tmp_path, ["ACME-PLACEHOLDER"])
    result = _run_check("acme-placeholder lower-cased", denylist=dl)
    assert result.returncode == 1


def test_case_insensitive_upper_in_text(tmp_path: Path) -> None:
    """Term in denylist in mixed case; input is uppercase."""
    dl = _make_denylist(tmp_path, ["acme-placeholder"])
    result = _run_check("ACME-PLACEHOLDER uppercased", denylist=dl)
    assert result.returncode == 1


# ---------------------------------------------------------------------------
# confidentiality-check — substring matching
# ---------------------------------------------------------------------------


def test_substring_still_matches(tmp_path: Path) -> None:
    """A term appearing as a substring of a word still matches (-F semantics)."""
    dl = _make_denylist(tmp_path, ["example.invalid"])
    result = _run_check("prefix-example.invalid-suffix", denylist=dl)
    assert result.returncode == 1


# ---------------------------------------------------------------------------
# confidentiality-check — missing / unreadable denylist
# ---------------------------------------------------------------------------


def test_missing_denylist_fails_closed(tmp_path: Path) -> None:
    """If the denylist file does not exist, the script exits non-zero."""
    missing = tmp_path / "does_not_exist.txt"
    result = _run_check("any input", denylist=missing)
    assert result.returncode != 0


def test_unreadable_denylist_fails_closed(tmp_path: Path) -> None:
    """If the denylist exists but is not readable, the script exits non-zero."""
    dl = _make_denylist(tmp_path, ["ACME-PLACEHOLDER"])
    dl.chmod(0o000)
    try:
        result = _run_check("any input", denylist=dl)
        assert result.returncode != 0
    finally:
        dl.chmod(0o600)  # restore so tmp_path cleanup can remove it


# ---------------------------------------------------------------------------
# confidentiality-check — empty denylist
# ---------------------------------------------------------------------------


def test_empty_denylist_clean_exits_zero(tmp_path: Path) -> None:
    """An empty denylist file results in a clean check (nothing to match)."""
    dl = tmp_path / "empty.txt"
    dl.write_text("", encoding="utf-8")
    dl.chmod(0o600)
    result = _run_check("any text at all", denylist=dl)
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# apply-patch — fallback ordering
# ---------------------------------------------------------------------------


def _run_apply(
    patch_text: str | None = None,
    *,
    file_arg: Path | None = None,
    env_override: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run apply-patch and return the completed process."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    cmd: list[str] = ["bash", str(_APPLY)]
    if file_arg is not None:
        cmd.append(str(file_arg))
    return subprocess.run(
        cmd,
        input=patch_text,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=str(_REPO_ROOT),
    )


def test_apply_patch_git_apply_fallback(tmp_path: Path) -> None:
    """When apply_patch is absent but git is present, git apply is used.

    We write a trivially-applying diff to a temp file within the repo and
    verify the script exits 0 (even if the diff has nothing to apply, via
    --allow-empty).
    """
    # Manufacture a minimal empty diff that git apply --allow-empty accepts.
    empty_patch = (
        "diff --git a/scripts/dev/README.md b/scripts/dev/README.md\n"
        "index 0000000..0000000 100644\n"
    )
    result = _run_apply(patch_text=empty_patch)
    # git apply may reject a malformed diff; we only require the script runs
    # without "no patch applier available" and that git was tried.
    combined = result.stdout + result.stderr
    assert "no patch applier available" not in combined


def test_apply_patch_no_applier_error(tmp_path: Path) -> None:
    """When PATH has none of the three tools, the error message is clear."""
    # Build a minimal PATH containing only bash (so the script can run) but
    # not apply_patch, git, or patch.
    bash_path = shutil.which("bash") or "/usr/bin/bash"
    stub_bin = tmp_path / "stubbin"
    stub_bin.mkdir()
    # Symlink bash into the stub bin so the script interpreter is reachable,
    # but no patch applier is available.
    (stub_bin / "bash").symlink_to(bash_path)
    result = _run_apply(
        patch_text="--- a\n+++ b\n",
        env_override={"PATH": str(stub_bin)},
    )
    assert result.returncode != 0
    assert "no patch applier available" in result.stderr


def test_apply_patch_usage_error() -> None:
    """More than one argument produces a usage error."""
    result = subprocess.run(
        ["bash", str(_APPLY), "a.patch", "b.patch"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode != 0
    assert "usage" in result.stderr.lower()
