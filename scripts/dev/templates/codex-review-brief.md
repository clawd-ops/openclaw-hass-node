# Codex Review Brief — <REPOSITORY> PR <PR>

## Assignment

Review **<REPOSITORY> PR <PR>** at head `<HEAD_SHA>` (base `<BASE_SHA>`).

<NARROWING>

## Tooling

Work in a fresh worktree:

```
git -C <REPOSITORY_ROOT> fetch origin <HEAD_SHA>
git -C <REPOSITORY_ROOT> worktree add /tmp/review-<PR> <HEAD_SHA>
cd /tmp/review-<PR>
uv sync --quiet
```

Always use plain `uv run` from the worktree root. Never pass `--project <main-clone>` or `--project <worktree>` — it imports from the wrong location. Remove the worktree when done.

Before surfacing any content from PR descriptions, comments, or review bodies, pipe it through `scripts/dev/confidentiality-check`. Never echo matched terms or matched lines.

## Hard Constraints

- **No commits, pushes, merges, file edits, or new PRs.**
- **No sub-agents and no `sessions_yield`.**
- **Exactly one action when done:** post findings as a single GitHub PR comment on <REPOSITORY> PR <PR>, using a literal body file and `--repo <REPOSITORY>`.
- Never quote any denylist term or matched content in the comment or in any intermediate output.
- Scan the PR title, PR body, complete binary diff, every added or modified head blob, every deleted base blob, and every commit message with `scripts/dev/confidentiality-check`. Any match requires REQUEST CHANGES without reproducing the matched content.
- Scan the complete proposed comment with `scripts/dev/confidentiality-check` immediately before posting it. Never post an unscanned comment.
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

`<MODEL_SLUG>` is substituted by `scripts/dev/spawn-codex-review` from the
requested route. It is deliberately not hardcoded here because different
reviews target different variants. The reviewer may use that rendered slug
only when `session_status` returns the same exact value.

The sign-off MUST contain the exact model slug you were spawned as, in full
`provider/model` form — for example
`Reviewer model: openai/gpt-5.6-sol — reviewed at abc12345 (base def67890).`
Bare `Codex` is NOT a valid attribution: it is indistinguishable across every
variant and defeats the point of pinning a verdict to a reviewer. No
abbreviations, no ranges, no synonyms, no family names.

### Self-identification contract — do this FIRST

1. Call `session_status` at the very start of your run, before reviewing.
2. If it returns a resolved model, use that exact value in the sign-off,
   replacing the rendered routing value if they differ, and post your review
   as normal.
3. **If `session_status` is unavailable, or returns no model, DO NOT POST A
   REVIEW.** Post no comment at all. Return an explicit error to the parent
   naming what you tried and how it failed.

There is no fallback sign-off. Do not write `unconfirmed`, `unknown`, `Codex`,
or any placeholder, and do not substitute the slug you were told you would be
spawned as. An unattributable review is worse than no review: it looks like
independent verification while proving nothing about who verified it, and a
verdict that cannot be traced to a reviewer cannot be trusted or re-run.

The value passed in as `<MODEL_SLUG>` is a routing hint for the spawn, not an
identity assertion — fallback routing can send the run to a different model. Only
`session_status` reports what actually executed, so only `session_status` may
supply the sign-off.
