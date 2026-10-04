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
14. plugin `vitest run`
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

### Exact-head review (`templates/codex-review-brief.md`)

Reviews are driven by the parent agent, not by a script in this directory.
`templates/codex-review-brief.md` is the canonical brief: substitute the
repository, PR number, head SHA, base SHA, and an optional per-PR narrowing
section, then pass the assembled text verbatim as the `task` of a single
`sessions_spawn` call with `visible=true`.

There is deliberately no wrapper that does the spawning. An earlier version of
this directory shipped one, and it could not work: bash has no deterministic way
to call `sessions_spawn`, so the wrapper asked an intermediate model turn to
re-emit the brief as a tool argument. On a measured run that turn reproduced 58%
of a 3481-character brief, silently dropping the section defining the verdict
format, and rewrote a literal path. Validating the result afterwards only
converted the corruption into a hard failure *after* a full review had been paid
for. Passing the brief from the parent, which holds the exact text, removes the
lossy hop rather than checking it.

The template encodes the hard constraints (no commits, pushes, merges, file
edits, sub-agents, or GitHub posts) and the `uv run` tooling rule, and binds
every Git and GitHub command to the repository under review rather than the
reviewer's starting directory.

The child returns `APPROVE` or `REQUEST CHANGES` on its first line, with the
single `Reviewed head:` pin as its exact second line. `pr-state` parses that pin
to decide whether a review is fresh, so a verdict covers only the SHA it names.

### Publishing a review

The parent publishes, because only the parent can read which model actually ran.
Before posting: resolve the reviewer's real `provider/model` from the child's
session record, confirm the pin is on the second line and the head SHA appears
exactly once, scan the full comment with `confidentiality-check`, check no
existing comment already carries that exact pin line, re-read the live head and
base immediately before the POST, and read the stored comment back to confirm it
matches byte for byte.

Attribution names the actual model:

```
Reviewer model: openai/gpt-5.6-sol
```

Bare `Codex` is **not** valid attribution. It is indistinguishable across every
variant, which defeats the purpose of pinning a verdict to a reviewer. There is
no fallback sign-off: if the model cannot be resolved, the review is not
published.

The reviewer never discovers the model. `session_status` is not in the subagent
tool surface, so a reviewer asked to self-identify cannot comply: it will either
guess a family name or abort without posting, and both have happened. The
spawning agent chose the model, so it already holds the answer. Substitute it
into `<REVIEWER_MODEL>` in the brief at spawn time and the reviewer stamps that
value verbatim. `sessions_spawn` also returns the resolved id as `resolvedModel`
in its receipt before the review runs, which is the value to use when the
requested and resolved models could differ.

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

## `gh` in this environment

Two things cost an hour each if you meet them cold.

### `gh pr edit` fails; use the REST API

The token here is not granted `read:org`, and `gh pr edit` issues a GraphQL
query whose `login` field requires it:

```
GraphQL: Your token has not been granted the required scopes to execute this
query. The 'login' field requires one of the following scopes: ['read:org']
```

The failure is about the query `gh` builds, not about your permission to edit,
so the REST endpoint works unchanged. Edit a PR or issue body with:

```
python3 -c "import json; print(json.dumps({'body': open('body.md').read()}))" > payload.json
gh api -X PATCH repos/clawd-ops/openclaw-hass-node/pulls/<num> --input payload.json
gh api -X PATCH repos/clawd-ops/openclaw-hass-node/issues/<num> --input payload.json
```

Going through a JSON file rather than a shell argument is also what keeps
backticks, `$(...)` and newlines intact, per the GitHub comment formatting rule.
Read the body back afterwards and confirm real paragraph breaks survived.

### Review verdicts are reviews, not issue comments

A cross-provider verdict posted with `gh pr review` lands in

```
gh api repos/clawd-ops/openclaw-hass-node/pulls/<num>/reviews
```

and **not** in `.../issues/<num>/comments`. Polling only the comments endpoint
returns `0` while a `REQUEST CHANGES` is sitting on the PR. Check both, or a
verdict gets reported as "no verdict yet".

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
