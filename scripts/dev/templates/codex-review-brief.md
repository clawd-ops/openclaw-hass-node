# Codex Review Brief — PR <PR>

## Assignment

Review PR **<PR>** at head `<HEAD_SHA>` (base `<BASE_SHA>`).

<NARROWING>

## Tooling

Work in a fresh worktree:

```
git fetch origin
git worktree add /tmp/review-<PR> <HEAD_SHA>
cd /tmp/review-<PR>
uv sync --quiet
```

Always use plain `uv run` from the worktree root. Never pass `--project <main-clone>` or `--project <worktree>` — it imports from the wrong location. Remove the worktree when done.

Before surfacing any content from PR descriptions, comments, or review bodies, pipe it through `scripts/dev/confidentiality-check`. Never echo matched terms or matched lines.

## Hard Constraints

- **No commits, pushes, merges, file edits, or new PRs.**
- **No sub-agents and no `sessions_yield`.**
- **Exactly one action when done:** post findings as a single GitHub PR comment on PR <PR>, using a literal body file.
- Never quote any denylist term or matched content in the comment or in any intermediate output.
- Scan the entire PR diff, PR body, and commit messages with `scripts/dev/confidentiality-check`. Any match requires REQUEST CHANGES without reproducing the matched content.
- Before commenting, re-read the live PR head and stop without posting if it differs from `<HEAD_SHA>`.
- Read the posted comment back and verify real paragraph breaks and the attribution line.

## Review Scope

Examine the diff for:

1. Correctness — logic errors, off-by-one, missing edge cases, unhandled error paths.
2. Security — injection risks, credential exposure, insecure defaults.
3. Style / quality — non-obvious complexity, missing type annotations, test gaps.

## Attribution Line (required, verbatim, final line of comment)

```
Reviewer model: openai/gpt-5.6-sol
```
