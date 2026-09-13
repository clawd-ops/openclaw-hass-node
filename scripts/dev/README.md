# scripts/dev — Developer Tooling

Local automation for the openclaw-hass-node development workflow.

---

## Tools

### `confidentiality-check`

Scans content (stdin or a named file) against an operator-local denylist of
sensitive terms. Fails closed if the denylist is missing or unreadable. On a
match, prints a single redacted warning and exits 1. Never echoes matched
terms, matched lines, or the denylist itself.

```
# Scan a file
scripts/dev/confidentiality-check path/to/file.txt

# Scan stdout of another command
git diff origin/main..HEAD | scripts/dev/confidentiality-check
```

The denylist path defaults to `$HOME/.openclaw/state/confidentiality-denylist.txt`
and can be overridden with `OPENCLAW_CONFIDENTIALITY_DENYLIST_FILE`.

**Denylist format:** one literal term per line (no regular expressions, no
comments). The file must be mode 600, outside the repository, and never
committed. Populate it from your operator's secure store; do not place example
entries in the repo.

---

### `run-all-gates`

Runs the full local gate suite in order, exiting on the first failure with a
clear statement of which gate failed and the exact command to re-run it alone.

```
scripts/dev/run-all-gates
```

Gates in order:

1. Python 3.13 environment sync matching CI
2. `ruff format --check`
3. `ruff check`
4. `mypy --strict`
5. `pytest` (95% coverage)
6. `generate-command-coverage.py --check`
7. `check-active-docs-schema.py`
8. synchronized release-version check
9. Bandit and dependency audit
10. strict MkDocs build
11. TypeScript dependency install matching CI
12. `pnpm docs:typescript:check`
13. plugin `tsc --noEmit` (typecheck)
14. plugin `vitest run` when paths covered by the CI TypeScript workflow changed
15. Docker application build smoke
16. `git diff --check` for the branch, index, and worktree

> **Note:** TypeScript contract tests require the Python venv to include the
> node package. The gate runner prepares that environment automatically before
> running the cross-language suite.

---

### `pr-state <num>`

Emits one JSON object with:

- `head_sha`, `base_sha`
- `mergeable_state`, `state`
- `checks` — every paginated CI check name and its conclusion
- `latest_codex_verdict_body_head` — first non-empty line of the latest trusted review body
- `latest_codex_pinned_sha` — the SHA named by that review
- `latest_codex_pinned_base_sha` — the base SHA named by that review
- `pinned_sha_matches_head` — whether the attributed head matches
- `pinned_shas_match_pr` — authoritative freshness check requiring both the
  attributed head and base to match the live PR

Only repository-owner comments with the exact verdict envelope and terminal
attribution line are eligible. Review body text is piped through
`confidentiality-check` before surfacing.

```
scripts/dev/pr-state 123
```

---

### `pr-rebase <num>`

Automates the safe rebase workflow:

1. Creates a dedicated worktree (never touching the shared clone checkout)
2. Fetches `origin`
3. Rebases the PR branch onto `origin/main`
4. Regenerates the coverage ledger and TypeScript API docs, committing derived changes when needed
5. Runs `run-all-gates`
6. Runs `confidentiality-check` on the complete binary diff, every current changed blob, every deleted base blob, and every commit message
7. Pushes with an explicit expected-head `--force-with-lease`
8. Cleans up the worktree on exit (success or failure)

```
scripts/dev/pr-rebase 123
```

Stops loudly at the first failure. Never pushes through a gate or leak failure.

---

### `spawn-codex-review <num> [narrowing-file]`

Assembles a Codex review brief by substituting repository identity, PR number, head SHA, base SHA,
and an optional per-PR narrowing section into
`scripts/dev/templates/codex-review-brief.md`. It checks the assembled brief,
then uses a dedicated launcher session to invoke `sessions_spawn` for one
visible exact-head review run. It requires exactly one successful spawn tool
call and an accepted child receipt, waits for that exact child to finish, and
reads the child's final review from its exported trajectory.
The generated brief binds all local Git and GitHub commands to that repository
instead of relying on the reviewer's starting directory.

The template encodes hard constraints (no commits, no pushes, no merges, no
file edits, no sub-agents, and no GitHub posts), plus the correct `uv run`
tooling rule. The child returns only an `APPROVE` or `REQUEST CHANGES`
review body. The wrapper owns attribution and publication.

```
scripts/dev/spawn-codex-review 123
scripts/dev/spawn-codex-review 123 reviews/pr-123-narrowing.md
REVIEW_MODEL_SLUG=openai/gpt-5.6-sol scripts/dev/spawn-codex-review 123
```

### Reviewer attribution

Set `REVIEW_MODEL_SLUG` to request a model variant. The value is a routing
hint, not attribution evidence. After the child finishes, the wrapper obtains a
fresh session list, selects the exact accepted child session key, and runs
`resolve-reviewer-model.py`. That resolver emits only the child record's
`provider/model` slug and refuses missing, empty, malformed, or unknown
records.

A valid sign-off looks like:

```
Reviewer model: openai/gpt-5.6-sol
```

Bare `Codex` is **not** valid attribution. It is indistinguishable across every
variant, which defeats the purpose of pinning a verdict to a reviewer.

There is no fallback sign-off. Resolver failure suppresses the entire review.
On success, the wrapper appends the resolved slug and exact head/base SHAs to
the child's body, scans the complete comment with `confidentiality-check`,
rechecks that the PR head has not moved, posts through the GitHub API, and reads
the stored comment back exactly. Any failure before posting exits loudly and
leaves the PR without an unattributable or stale review.

---

### `apply-patch [patch-file]`

Applies a patch (from a file or stdin), trying three tools in order:

1. `apply_patch` (if on PATH)
2. `git apply --allow-empty`
3. `patch -p1`

Fails clearly if none are available.

```
cat my.patch | scripts/dev/apply-patch
scripts/dev/apply-patch my.patch
```

---

## Denylist location and format

The operator-local denylist lives at:

```
$HOME/.openclaw/state/confidentiality-denylist.txt
```

Format: one literal term per line. No regular expressions. No comments. No
header. The file must be mode 600 and must never be committed to the
repository. Populate it from your secure store. The path can be overridden
with the `OPENCLAW_CONFIDENTIALITY_DENYLIST_FILE` environment variable.
