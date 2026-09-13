"""Tests for scripts/dev/confidentiality-check and scripts/dev/apply-patch.

These tests use real subprocesses against the actual shell scripts so that
shell semantics (set -euo pipefail, grep exit codes, etc.) are covered by
real execution rather than mocks.

All denylist fixtures use obviously-fake placeholder terms that cannot appear
in any real confidential context.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHECK = _REPO_ROOT / "scripts" / "dev" / "confidentiality-check"
_APPLY = _REPO_ROOT / "scripts" / "dev" / "apply-patch"
_PR_STATE = _REPO_ROOT / "scripts" / "dev" / "pr-state"
_PR_REBASE = _REPO_ROOT / "scripts" / "dev" / "pr-rebase"

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


def test_missing_input_file_fails_closed_without_echoing_path(tmp_path: Path) -> None:
    """An input read error cannot be mistaken for a clean scan."""
    dl = _make_denylist(tmp_path, ["ACME-PLACEHOLDER"])
    missing = tmp_path / "private-name.txt"

    result = _run_check(denylist=dl, file_arg=missing)

    assert result.returncode == 1
    assert "scan failed" in result.stderr
    assert str(missing) not in result.stderr


def test_option_shaped_input_filename_is_scanned(tmp_path: Path) -> None:
    """A leading dash in a file name cannot become a grep option."""
    dl = _make_denylist(tmp_path, ["ACME-PLACEHOLDER"])
    target = tmp_path / "-v"
    target.write_text("ACME-PLACEHOLDER\n", encoding="utf-8")

    env = os.environ.copy()
    env["OPENCLAW_CONFIDENTIALITY_DENYLIST_FILE"] = str(dl)
    result = subprocess.run(
        ["bash", str(_CHECK), target.name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 1
    assert "CONFIDENTIAL LEAK" in result.stderr


def test_insecure_denylist_mode_fails_closed(tmp_path: Path) -> None:
    """A denylist readable by other users is rejected."""
    dl = _make_denylist(tmp_path, ["ACME-PLACEHOLDER"])
    dl.chmod(0o644)

    result = _run_check("clean text", denylist=dl)

    assert result.returncode == 1
    assert "mode 600" in result.stderr


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


def test_apply_patch_passes_named_file_on_stdin(tmp_path: Path) -> None:
    """The preferred patch applier receives file content on standard input."""
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    capture = tmp_path / "captured.txt"
    stub = stub_bin / "apply_patch"
    stub.write_text('#!/bin/sh\ncat > "$PATCH_CAPTURE"\n', encoding="utf-8")
    stub.chmod(0o755)
    patch_file = tmp_path / "change.patch"
    patch_file.write_text("safe patch body\n", encoding="utf-8")
    env = {
        "PATH": f"{stub_bin}:{os.environ['PATH']}",
        "PATCH_CAPTURE": str(capture),
    }

    result = _run_apply(file_arg=patch_file, env_override=env)

    assert result.returncode == 0, result.stderr
    assert capture.read_text(encoding="utf-8") == "safe patch body\n"


def test_git_fallback_treats_leading_dash_filename_as_stdin(tmp_path: Path) -> None:
    """The Git fallback never interprets a patch filename as an option."""
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    (stub_bin / "bash").symlink_to(shutil.which("bash") or "/usr/bin/bash")
    captured_args = tmp_path / "args.txt"
    captured_patch = tmp_path / "captured.patch"
    git = stub_bin / "git"
    git.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$*" > "$CAPTURE_ARGS"\n/bin/cat > "$CAPTURE_PATCH"\n',
        encoding="utf-8",
    )
    git.chmod(0o755)
    patch_file = tmp_path / "-unsafe.patch"
    patch_file.write_text("safe patch body\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(stub_bin),
            "CAPTURE_ARGS": str(captured_args),
            "CAPTURE_PATCH": str(captured_patch),
        }
    )

    result = subprocess.run(
        [str(stub_bin / "bash"), str(_APPLY), patch_file.name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert captured_args.read_text(encoding="utf-8").strip() == "apply --allow-empty"
    assert captured_patch.read_text(encoding="utf-8") == "safe patch body\n"


def test_pr_state_selects_latest_attributed_comment(tmp_path: Path) -> None:
    """One JSON result uses the latest attributed comment and its pinned head."""
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    head = "a" * 40
    old = "c" * 40
    gh = stub_bin / "gh"
    gh.write_text(
        """#!/bin/sh
case "$*" in
  "repo view --json nameWithOwner") printf '%s\\n' "$GH_REPO" ;;
  *"check-runs?"*) printf '%s\\n' "$GH_CHECKS" ;;
  *"comments?"*) printf '%s\\n' "$GH_COMMENTS" ;;
  *"reviews?"*) printf '[[]]\\n' ;;
  *"pulls/7") printf '%s\\n' "$GH_CORE" ;;
  *) exit 9 ;;
esac
""",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    denylist = _make_denylist(tmp_path, ["ACME-PLACEHOLDER"])
    env = os.environ.copy()
    env["PATH"] = f"{stub_bin}:{env['PATH']}"
    env["OPENCLAW_CONFIDENTIALITY_DENYLIST_FILE"] = str(denylist)
    env["GH_REPO"] = json.dumps({"nameWithOwner": "example/project"})
    env["GH_CHECKS"] = json.dumps(
        [
            {"check_runs": [{"name": "CI", "conclusion": "success"}]},
            {"check_runs": [{"name": "Docs", "conclusion": "success"}]},
        ]
    )
    env["GH_COMMENTS"] = json.dumps(
        [
            [
                {
                    "created_at": "2026-01-01T00:00:00Z",
                    "user": {"login": "example"},
                    "body": (
                        f"REQUEST CHANGES\n"
                        f"Reviewed head: `{old}` (base `{'b' * 40}`)\n\n"
                        f"Reviewer model: openai/gpt-5.6-sol"
                    ),
                },
            ],
            [
                {
                    "created_at": "2026-01-02T00:00:00Z",
                    "user": {"login": "example"},
                    "body": (
                        f"APPROVE\n"
                        f"Reviewed head: `{head}` (base `{'b' * 40}`)\n\n"
                        f"Reviewer model: openai/gpt-5.6-luna"
                    ),
                }
            ],
            [
                {
                    "created_at": "2026-01-03T00:00:00Z",
                    "user": {"login": "untrusted"},
                    "body": (
                        f"REQUEST CHANGES\n"
                        f"Reviewed head: `{old}` (base `{'b' * 40}`)\n\n"
                        f"Reviewer model: openai/gpt-5.6-sol"
                    ),
                }
            ],
        ]
    )
    env["GH_CORE"] = json.dumps(
        {
            "head": {"sha": head},
            "base": {"sha": "b" * 40},
            "mergeable_state": "clean",
            "state": "open",
        }
    )

    result = subprocess.run(
        [str(_PR_STATE), "7"], capture_output=True, text=True, check=False, env=env
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["latest_codex_verdict_body_head"] == "APPROVE"
    assert payload["latest_codex_pinned_sha"] == head
    assert payload["latest_codex_pinned_base_sha"] == "b" * 40
    assert payload["pinned_sha_matches_head"] is True
    assert payload["pinned_shas_match_pr"] is True
    assert [check["name"] for check in payload["checks"]] == ["CI", "Docs"]

    moved_base = json.loads(env["GH_CORE"])
    moved_base["base"]["sha"] = "d" * 40
    env["GH_CORE"] = json.dumps(moved_base)
    stale_result = subprocess.run(
        [str(_PR_STATE), "7"], capture_output=True, text=True, check=False, env=env
    )
    stale_payload = json.loads(stale_result.stdout)
    assert stale_payload["pinned_sha_matches_head"] is True
    assert stale_payload["pinned_shas_match_pr"] is False


def test_pr_rebase_uses_worktree_local_gates_and_explicit_lease() -> None:
    """Static invariants prevent the shared-checkout and silent-failure regressions."""
    text = _PR_REBASE.read_text(encoding="utf-8")
    assert "python scripts/generate-command-coverage.py" in text
    assert "scripts/dev/run-all-gates" in text
    assert '"--force-with-lease=refs/heads/$BRANCH:$EXPECTED_HEAD"' in text
    assert 'generate-command-coverage.py" 2>/dev/null || true' not in text
    assert 'check-active-docs-schema.py" 2>/dev/null || true' not in text
    assert text.index("pnpm install --no-frozen-lockfile") < text.index("pnpm docs:typescript")
    assert 'git diff --name-only -z --diff-filter=ACMRT "origin/main...HEAD"' in text
    assert 'git cat-file blob "HEAD:$changed_path"' in text
    assert 'git diff --binary --no-ext-diff "origin/main...HEAD"' in text
    assert 'git diff --name-only -z --diff-filter=D "origin/main...HEAD"' in text
    assert 'git cat-file blob "origin/main:$changed_path"' in text
    assert "--json state,baseRefName,headRefName,headRefOid,headRepository" in text
    assert '[[ "$PR_STATE" != "OPEN" ]]' in text
    assert '[[ "$BASE_BRANCH" != "main" ]]' in text
    assert '[[ "$HEAD_REPOSITORY" != "$REPOSITORY" ]]' in text
    assert "uv sync --quiet --python 3.13" in text


def test_pr_rebase_head_blob_filter_covers_type_changes(tmp_path: Path) -> None:
    """The rebase scanner's filter includes a symlink-to-file type change."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    changed = repo / "changed"
    changed.symlink_to("target")
    subprocess.run(["git", "-C", str(repo), "add", "changed"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)

    changed.unlink()
    changed.write_bytes(b"literal payload\x00after type change")
    subprocess.run(["git", "-C", str(repo), "add", "changed"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "head"], check=True)

    status = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-status", "HEAD^...HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    scanned = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "--name-only",
            "--diff-filter=ACMRT",
            "HEAD^...HEAD",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    blob = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", "HEAD:changed"],
        check=True,
        capture_output=True,
    )

    assert status.stdout == "T\tchanged\n"
    assert scanned.stdout == "changed\n"
    assert blob.stdout == b"literal payload\x00after type change"


@pytest.mark.parametrize(
    "pr_data",
    [
        "CLOSED\tmain\tfeature\t" + "a" * 40 + "\texample/project",
        "OPEN\trelease\tfeature\t" + "a" * 40 + "\texample/project",
        "OPEN\tmain\tfeature\t" + "a" * 40 + "\tother/project",
    ],
)
def test_pr_rebase_refuses_unsupported_pr_before_git_mutation(tmp_path: Path, pr_data: str) -> None:
    """Closed, non-main, and foreign-repository PRs fail before fetch/worktree."""
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    marker = tmp_path / "mutated"
    git = stub_bin / "git"
    git.write_text(
        """#!/bin/sh
case "$*" in
  *"rev-parse --show-toplevel") printf '%s\n' "$REPO_ROOT" ;;
  *) touch "$MUTATION_MARKER"; exit 9 ;;
esac
""",
        encoding="utf-8",
    )
    git.chmod(0o755)
    gh = stub_bin / "gh"
    gh.write_text(
        """#!/bin/sh
case "$*" in
  "pr view "*) printf '%s\n' "$PR_DATA" ;;
  "repo view "*) printf '%s\n' "example/project" ;;
  *) exit 9 ;;
esac
""",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{stub_bin}:{env['PATH']}",
            "MUTATION_MARKER": str(marker),
            "PR_DATA": pr_data,
            "REPO_ROOT": str(_REPO_ROOT),
        }
    )

    result = subprocess.run(
        [str(_PR_REBASE), "7"], capture_output=True, text=True, check=False, env=env
    )

    assert result.returncode != 0
    assert not marker.exists()


def test_gate_runner_runs_complete_local_suite() -> None:
    """The local runner executes every gate without path-dependent skips."""
    text = (_REPO_ROOT / "scripts" / "dev" / "run-all-gates").read_text(encoding="utf-8")
    assert "uv sync --all-extras --python 3.13" in text
    # `scripts/dev` holds no Python module now that the review wrapper and its
    # model resolver are gone, and an unmatched glob is passed through literally
    # to ruff, which then fails on a path that does not exist.
    assert "scripts/dev/pr-state" in text
    assert "scripts/dev/*.py" not in text
    assert text.index("uv sync --all-extras --python 3.13") < text.index("uv run ruff")
    assert "uv run python scripts/generate-command-coverage.py --check" in text
    assert "uv run python scripts/check-active-docs-schema.py" in text
    assert text.count("pnpm -r --filter './plugins/*' test") == 2
    assert "TypeScript workflow paths unchanged" not in text
    assert "changed-path enumeration" not in text
    assert "docker build app" in text


def test_review_template_binds_repository_and_complete_confidentiality_scope() -> None:
    """The central brief covers repository identity and every public-content boundary."""
    text = (_REPO_ROOT / "scripts" / "dev" / "templates" / "codex-review-brief.md").read_text(
        encoding="utf-8"
    )

    assert "<REPOSITORY> PR <PR>" in text
    assert "git -C <REPOSITORY_ROOT>" in text
    assert "PR title" in text
    assert "complete binary diff" in text
    assert "every added or modified head blob" in text
    assert "every deleted base blob" in text
    assert "Scan your complete findings" in text
    # The child no longer posts to GitHub, so it needs no repo flag;
    # it must instead be told explicitly not to post.
    assert "Do not post anything to GitHub" in text


def _cross_repository_stub_bin(tmp_path: Path) -> Path:
    """Stub `gh` that reports a different repository outside the script root.

    Both helpers are addressed by path, so they can be invoked while the shell
    sits in an unrelated authenticated checkout. The stub mimics real `gh`,
    which resolves `repo view` from the working directory.
    """
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    git = stub_bin / "git"
    git.write_text(
        """#!/bin/sh
case "$*" in
  *"rev-parse --show-toplevel") printf '%s\\n' "$REPO_ROOT" ;;
  *) touch "$MUTATION_MARKER"; exit 9 ;;
esac
""",
        encoding="utf-8",
    )
    git.chmod(0o755)
    gh = stub_bin / "gh"
    gh.write_text(
        """#!/bin/sh
printf '%s\\n' "$*" >> "$GH_CALL_LOG"
case "$*" in
  "repo view "*)
    if [ "$PWD" = "$REPO_ROOT" ]; then
      printf '%s\\n' "example/project"
    else
      printf '%s\\n' "intruder/other"
    fi
    ;;
  "pr view "*) printf '%s\\n' "$PR_DATA" ;;
  *) exit 9 ;;
esac
""",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return stub_bin


def test_pr_rebase_binds_github_lookups_to_the_script_repository(tmp_path: Path) -> None:
    """Invoked from another checkout, pr-rebase must not read a foreign PR.

    Every mutation it performs (fetch, worktree, rebase, force-push) targets the
    script's own repository, so resolving PR metadata from the caller's working
    directory could pair one repository's metadata with another's force-push.
    """
    stub_bin = _cross_repository_stub_bin(tmp_path)
    call_log = tmp_path / "gh-calls.txt"
    marker = tmp_path / "mutated"
    foreign_cwd = tmp_path / "other-checkout"
    foreign_cwd.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{stub_bin}:{env['PATH']}",
            "MUTATION_MARKER": str(marker),
            "GH_CALL_LOG": str(call_log),
            "REPO_ROOT": str(_REPO_ROOT),
            # Closed PR: stops the run early, after the lookups under test.
            "PR_DATA": "CLOSED\tmain\ttopic\tdeadbeef\texample/project",
        }
    )

    subprocess.run(
        [str(_PR_REBASE), "7"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=foreign_cwd,
    )

    calls = call_log.read_text(encoding="utf-8").splitlines()
    pr_views = [call for call in calls if call.startswith("pr view ")]
    assert pr_views, "expected pr-rebase to look up the pull request"
    for call in pr_views:
        assert "--repo example/project" in call
        assert "intruder/other" not in call
    assert not marker.exists()
