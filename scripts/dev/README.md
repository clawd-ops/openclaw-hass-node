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

1. `ruff format --check`
2. `ruff check`
3. `mypy --strict`
4. `pytest` (95% coverage)
5. `generate-command-coverage.py --check`
6. `check-active-docs-schema.py`
7. synchronized release-version check
8. Bandit and dependency audit
9. strict MkDocs build
10. TypeScript dependency install matching CI
11. `pnpm docs:typescript:check`
12. plugin `tsc --noEmit` (typecheck)
13. plugin `vitest run` when paths covered by the CI TypeScript workflow changed
14. `git diff --check` for the branch, index, and worktree

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
6. Runs `confidentiality-check` on every changed blob, including binary content, and every commit message
7. Pushes with an explicit expected-head `--force-with-lease`
8. Cleans up the worktree on exit (success or failure)

```
scripts/dev/pr-rebase 123
```

Stops loudly at the first failure. Never pushes through a gate or leak failure.

---

### `spawn-codex-review <num> [narrowing-file]`

Assembles a Codex review brief by substituting PR number, head SHA, base SHA,
and an optional per-PR narrowing section into
`scripts/dev/templates/codex-review-brief.md`. It checks the assembled brief,
then uses a dedicated launcher session to invoke `sessions_spawn` for one
visible exact-head review run. It requires exactly one successful spawn tool
call, parses an accepted receipt, and verifies the returned child session exists
and received the complete assembled brief before reporting success.

The template encodes hard constraints (no commits, no pushes, no merges, no
file edits, no sub-agents, exactly one `gh pr comment`), the correct
`uv run` tooling rule, and the deterministic attribution line format.

```
scripts/dev/spawn-codex-review 123
scripts/dev/spawn-codex-review 123 reviews/pr-123-narrowing.md
REVIEW_MODEL_SLUG=openai/gpt-5.6-sol scripts/dev/spawn-codex-review 123
```

### Reviewer attribution

One value drives both the spawn and the brief, so a verdict names the model that
actually produced it. Set `REVIEW_MODEL_SLUG` to target a variant; the wrapper
substitutes it into `<MODEL_SLUG>` and rejects anything that is not a full
`provider/model` slug. The template never hardcodes one, or every reviewer would
claim the same identity regardless of what ran.

A valid sign-off looks like:

```
Reviewer model: openai/gpt-5.6-sol — reviewed at abc12345 (base def67890).
```

Bare `Codex` is **not** valid attribution. It is indistinguishable across every
variant, which defeats the purpose of pinning a verdict to a reviewer. A
reviewer that genuinely cannot determine its own model must say so explicitly
rather than fall back to a family name:

```
Reviewer model: unconfirmed (session_status not available in toolset) — reviewed at abc12345 (base def67890).
```

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
