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
- `pinned_sha_matches_head` — boolean

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
call, parses an accepted receipt, and verifies the returned child session exists
and received the complete assembled brief before reporting success.
The generated brief binds all local Git and GitHub commands to that repository
instead of relying on the reviewer's starting directory.

The template encodes hard constraints (no commits, no pushes, no merges, no
file edits, no sub-agents, exactly one `gh pr comment`), the correct
`uv run` tooling rule, and the deterministic attribution line format.

```
scripts/dev/spawn-codex-review 123
scripts/dev/spawn-codex-review 123 reviews/pr-123-narrowing.md
REVIEW_MODEL_SLUG=openai/gpt-5.6-sol scripts/dev/spawn-codex-review 123
```

### Reviewer attribution

One value drives both the requested spawn route and the initial brief. Set
`REVIEW_MODEL_SLUG` to target a variant; the wrapper substitutes it into
`<MODEL_SLUG>` and rejects anything that is not a full `provider/model` slug.
The reviewer must replace that routing value if `session_status` reports a
different resolved model. The template never hardcodes one, or every reviewer
would claim the same identity regardless of what ran.

A valid sign-off looks like:

```
Reviewer model: openai/gpt-5.6-sol — reviewed at abc12345 (base def67890).
```

Bare `Codex` is **not** valid attribution. It is indistinguishable across every
variant, which defeats the purpose of pinning a verdict to a reviewer.

There is no fallback sign-off. A reviewer calls `session_status` first; if it
cannot resolve its own model it posts nothing and returns an error to the
caller. An unattributable review looks like independent verification while
proving nothing about who verified it, so no review is the safer outcome.

The wrapper enforces this before spawning with a same-model launcher turn. The
launcher must successfully call `session_status` before it may call
`sessions_spawn`. A parent-agent tool catalog is not accepted as proof because
it can differ from the spawned runtime's toolset. If the preflight cannot
self-identify, the wrapper refuses to spawn and reports that no review was
posted and the PR is not review-ready. The reviewer repeats the check because
its child runtime can still expose a different toolset. There is no bypass.

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
