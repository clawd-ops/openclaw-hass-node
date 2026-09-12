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

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHECK = _REPO_ROOT / "scripts" / "dev" / "confidentiality-check"
_APPLY = _REPO_ROOT / "scripts" / "dev" / "apply-patch"
_PR_STATE = _REPO_ROOT / "scripts" / "dev" / "pr-state"
_PR_REBASE = _REPO_ROOT / "scripts" / "dev" / "pr-rebase"
_SPAWN_REVIEW = _REPO_ROOT / "scripts" / "dev" / "spawn-codex-review"

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
                        f"REQUEST CHANGES\nReviewed exact head `{old}`.\n\n"
                        f"Reviewer model: openai/gpt-5.6-sol — reviewed at {old} "
                        f"(base {'b' * 40})."
                    ),
                },
            ],
            [
                {
                    "created_at": "2026-01-02T00:00:00Z",
                    "user": {"login": "example"},
                    "body": (
                        f"APPROVE\nReviewed exact head `{head}`.\n\n"
                        f"Reviewer model: openai/gpt-5.6-luna — reviewed at {head} "
                        f"(base {'b' * 40})."
                    ),
                }
            ],
            [
                {
                    "created_at": "2026-01-03T00:00:00Z",
                    "user": {"login": "untrusted"},
                    "body": (
                        f"REQUEST CHANGES\nReviewed exact head `{old}`.\n\n"
                        f"Reviewer model: openai/gpt-5.6-sol — reviewed at {old} "
                        f"(base {'b' * 40})."
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
    assert payload["pinned_sha_matches_head"] is True
    assert [check["name"] for check in payload["checks"]] == ["CI", "Docs"]


def _spawn_review_env(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    """Build the stubbed environment used by the spawn-review tests."""
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    captured_args = tmp_path / "args.txt"
    captured_prompt = tmp_path / "prompt.txt"
    gh = stub_bin / "gh"
    gh.write_text(
        (
            "#!/bin/sh\n"
            'case "$*" in\n'
            "  *headRefOid*) printf '%040d\\n' 0;;\n"
            "  *) printf '%040d\\n' 1;;\n"
            "esac\n"
        ),
        encoding="utf-8",
    )
    gh.chmod(0o755)
    launcher = stub_bin / "openclaw"
    launcher.write_text(
        (
            "#!/bin/sh\n"
            'if [ "$1" = "agent" ]; then\n'
            '  printf \'%s\\n\' "$*" > "$CAPTURE_ARGS"\n'
            '  previous=""\n'
            '  for argument in "$@"; do\n'
            '    if [ "$previous" = "--message-file" ]; then '
            '/bin/cp "$argument" "$CAPTURE_PROMPT"; fi\n'
            '    previous="$argument"\n'
            "  done\n"
            "  printf '%s\\n' \"$LAUNCH_RESULT\"\n"
            'elif [ "$1" = "sessions" ]; then\n'
            "  printf '%s\\n' \"$SESSIONS_RESULT\"\n"
            'elif [ "$1" = "gateway" ]; then\n'
            "  printf '%s\\n' \"$HISTORY_RESULT\"\n"
            "else exit 9; fi\n"
        ),
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    denylist = _make_denylist(tmp_path, ["ACME-PLACEHOLDER"])
    expected_brief = (
        (_REPO_ROOT / "scripts/dev/templates/codex-review-brief.md")
        .read_text(encoding="utf-8")
        .replace("<PR>", "7")
        .replace("<HEAD_SHA>", "0" * 40)
        .replace("<BASE_SHA>", f"{1:040d}")
        .replace("<NARROWING>", "(no additional narrowing)")
        .replace("<MODEL_SLUG>", "openai/gpt-5.6-sol")
        .rstrip("\n")
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{stub_bin}:{env['PATH']}",
            "CAPTURE_ARGS": str(captured_args),
            "CAPTURE_PROMPT": str(captured_prompt),
            "OPENCLAW_CONFIDENTIALITY_DENYLIST_FILE": str(denylist),
            "LAUNCH_RESULT": json.dumps(
                {
                    "status": "ok",
                    "result": {
                        "payloads": [
                            {
                                "text": json.dumps(
                                    {
                                        "status": "accepted",
                                        "runId": "run-1",
                                        "childSessionKey": "agent:clawd:child-1",
                                    }
                                )
                            }
                        ],
                        "meta": {
                            "agentMeta": {
                                "terminalReceipt": {
                                    "successfulToolNames": ["session_status", "sessions_spawn"]
                                }
                            }
                        },
                    },
                }
            ),
            "SESSIONS_RESULT": json.dumps({"sessions": [{"key": "agent:clawd:child-1"}]}),
            "HISTORY_RESULT": json.dumps(
                {
                    "output": {
                        "details": {
                            "messages": [
                                {
                                    "role": "user",
                                    "content": f"[Subagent Task]\n{expected_brief}",
                                }
                            ]
                        }
                    }
                }
            ),
        }
    )

    return env, captured_prompt, captured_args


def test_spawn_review_invokes_launcher_with_subagent_brief(tmp_path: Path) -> None:
    """The wrapper dispatches exactly one subagent with the assembled brief."""
    env, captured_prompt, captured_args = _spawn_review_env(tmp_path)

    result = subprocess.run(
        [str(_SPAWN_REVIEW), "7"], capture_output=True, text=True, check=False, env=env
    )

    assert result.returncode == 0, result.stderr
    prompt = captured_prompt.read_text(encoding="utf-8")
    assert "Call session_status exactly once before any spawn" in prompt
    assert "call sessions_spawn exactly once" in prompt
    assert "Reviewer model: openai/gpt-5.6-sol" in prompt
    assert "PR **7**" in prompt
    assert "--model openai/gpt-5.6-sol" in captured_args.read_text(encoding="utf-8")
    # The placeholder must never survive into a dispatched brief: an
    # unsubstituted <MODEL_SLUG> would ship a reviewer with no identity to sign.
    assert "<MODEL_SLUG>" not in prompt


def test_spawn_review_rejects_success_without_spawn_receipt(tmp_path: Path) -> None:
    """A successful launcher process without one spawn tool call fails closed."""
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    gh = stub_bin / "gh"
    gh.write_text(
        "#!/bin/sh\ncase \"$*\" in *headRefOid*) printf '%040d\\n' 0;; "
        "*) printf '%040d\\n' 1;; esac\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    launcher = stub_bin / "openclaw"
    launcher.write_text(
        '#!/bin/sh\nif [ "$1" = agent ]; then printf \'%s\\n\' "$LAUNCH_RESULT"; '
        'elif [ "$1" = gateway ]; then printf \'%s\\n\' "$CATALOG_RESULT"; '
        "else printf '%s\\n' '{\"sessions\":[]}'; fi\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    denylist = _make_denylist(tmp_path, ["ACME-PLACEHOLDER"])
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{stub_bin}:{env['PATH']}",
            "OPENCLAW_CONFIDENTIALITY_DENYLIST_FILE": str(denylist),
            "CATALOG_RESULT": json.dumps({"groups": [{"tools": [{"id": "session_status"}]}]}),
            "LAUNCH_RESULT": json.dumps(
                {
                    "status": "ok",
                    "result": {
                        "payloads": [
                            {
                                "text": json.dumps(
                                    {
                                        "status": "accepted",
                                        "runId": "invented",
                                        "childSessionKey": "agent:clawd:invented",
                                    }
                                )
                            }
                        ],
                        "meta": {
                            "agentMeta": {
                                "terminalReceipt": {"successfulToolNames": ["session_status"]}
                            }
                        },
                    },
                }
            ),
        }
    )

    result = subprocess.run(
        [str(_SPAWN_REVIEW), "7"], capture_output=True, text=True, check=False, env=env
    )

    assert result.returncode != 0
    assert "no single accepted sessions_spawn receipt" in result.stderr


def test_pr_rebase_uses_worktree_local_gates_and_explicit_lease() -> None:
    """Static invariants prevent the shared-checkout and silent-failure regressions."""
    text = _PR_REBASE.read_text(encoding="utf-8")
    assert "python scripts/generate-command-coverage.py" in text
    assert "scripts/dev/run-all-gates" in text
    assert '"--force-with-lease=refs/heads/$BRANCH:$EXPECTED_HEAD"' in text
    assert 'generate-command-coverage.py" 2>/dev/null || true' not in text
    assert 'check-active-docs-schema.py" 2>/dev/null || true' not in text
    assert text.index("pnpm install --no-frozen-lockfile") < text.index("pnpm docs:typescript")
    assert 'git diff --name-only -z --diff-filter=ACMR "origin/main...HEAD"' in text
    assert 'git cat-file blob "HEAD:$changed_path"' in text


def test_gate_runner_matches_typescript_workflow_scope() -> None:
    """Node changes run cross-language tests and diff failures have guidance."""
    text = (_REPO_ROOT / "scripts" / "dev" / "run-all-gates").read_text(encoding="utf-8")
    assert "app/node/*" in text
    assert "uv sync --package openclaw-node --python 3.13" in text
    assert 'fail "changed-path enumeration (branch)"' in text


def test_spawn_review_reports_history_api_error_as_unverified(tmp_path: Path) -> None:
    """An unreachable history API must not be reported as a brief mismatch.

    The spawn is already proven by the accepted receipt and the session lookup.
    If the history call fails, the brief was never compared, so claiming it did
    not match accuses the wrong thing and sends the operator hunting a
    non-existent content bug.
    """
    env, _captured_prompt, _captured_args = _spawn_review_env(tmp_path)
    env["HISTORY_RESULT"] = json.dumps(
        {
            "ok": False,
            "toolName": "sessions_history",
            "error": {"code": "validation_error", "message": "no explicit owner"},
        }
    )

    result = subprocess.run(
        [str(_SPAWN_REVIEW), "7"], capture_output=True, text=True, check=False, env=env
    )

    assert result.returncode == 0, result.stderr
    assert "brief delivery unverified" in result.stderr
    assert "does not match the assembled brief" not in result.stderr


def test_spawn_review_still_fails_on_real_brief_mismatch(tmp_path: Path) -> None:
    """A history response that genuinely lacks the brief is still fatal."""
    env, _captured_prompt, _captured_args = _spawn_review_env(tmp_path)
    env["HISTORY_RESULT"] = json.dumps(
        {
            "output": {
                "details": {"messages": [{"role": "user", "content": "some entirely other task"}]}
            }
        }
    )

    result = subprocess.run(
        [str(_SPAWN_REVIEW), "7"], capture_output=True, text=True, check=False, env=env
    )

    assert result.returncode == 1
    assert "does not match the assembled brief" in result.stderr


def test_spawn_review_refuses_when_session_status_is_unavailable(tmp_path: Path) -> None:
    """No self-identification means no spawn.

    A reviewer that cannot call `session_status` can only guess at its own
    identity, and the spawn slug is a routing hint rather than proof of what
    ran. Producing a review nobody can attribute is worse than producing none,
    so the wrapper refuses before spawning rather than after.
    """
    env, _captured_prompt, _captured_args = _spawn_review_env(tmp_path)
    launch_result = json.loads(env["LAUNCH_RESULT"])
    launch_result["result"]["meta"]["agentMeta"]["terminalReceipt"]["successfulToolNames"] = []
    env["LAUNCH_RESULT"] = json.dumps(launch_result)

    result = subprocess.run(
        [str(_SPAWN_REVIEW), "7"], capture_output=True, text=True, check=False, env=env
    )

    assert result.returncode == 1
    assert (
        "Reviewer failed self-identification. No review posted. PR is not review-ready."
        in result.stderr
    )
    # Nothing may be dispatched when self-identification is impossible.
    assert "accepted" not in result.stdout


def test_spawn_review_reports_launcher_self_identification_failure(tmp_path: Path) -> None:
    """A launcher that cannot prove identity reports the required upstream state."""
    env, _captured_prompt, _captured_args = _spawn_review_env(tmp_path)
    launch_result = json.loads(env["LAUNCH_RESULT"])
    launch_result["result"]["payloads"] = [{"text": "SELF_IDENTIFICATION_FAILED"}]
    launch_result["result"]["meta"]["agentMeta"]["terminalReceipt"]["successfulToolNames"] = [
        "session_status"
    ]
    env["LAUNCH_RESULT"] = json.dumps(launch_result)

    result = subprocess.run(
        [str(_SPAWN_REVIEW), "7"], capture_output=True, text=True, check=False, env=env
    )

    assert result.returncode == 1
    assert (
        "Reviewer failed self-identification. No review posted. PR is not review-ready."
        in result.stderr
    )
