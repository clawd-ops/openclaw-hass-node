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
Reviewer model: <MODEL_SLUG> — reviewed at <HEAD_SHA> (base <BASE_SHA>).
```

`<MODEL_SLUG>` is substituted by `scripts/dev/spawn-codex-review` from the model
the reviewer is actually spawned as. It is deliberately not hardcoded here:
different reviews target different variants, and a fixed slug in the template
would make every reviewer claim the same identity regardless of what ran.

The sign-off MUST contain the exact model slug you were spawned as, in full
`provider/model` form — for example
`Reviewer model: openai/gpt-5.6-sol — reviewed at abc12345 (base def67890).`
Bare `Codex` is NOT a valid attribution: it is indistinguishable across every
variant and defeats the point of pinning a verdict to a reviewer. No
abbreviations, no ranges, no synonyms, no family names.

If you genuinely cannot determine your own model, do not guess and do not fall
back to a generic name. Sign off exactly:

```
Reviewer model: unconfirmed (session_status not available in toolset) — reviewed at <HEAD_SHA> (base <BASE_SHA>).
```
