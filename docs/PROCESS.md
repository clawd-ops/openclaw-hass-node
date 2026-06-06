# Process — cross-validated code changes

> Every substantive code change goes through generate-then-cross-review
> before merge. Generator and reviewer must be different model
> providers, so blind spots don't compound.

## Pairing

- **Generator**: Claude (Claude Code subagent).
- **Reviewer**: OpenAI (Codex subagent on the pi runtime).

If the pairing is ever inverted for a specific task (e.g. Codex
generates), the reviewer must be a different provider — Claude in that
case. The rule is "two providers", not "Claude generates".

## Flow

1. **Branch + change.** Claude Code subagent is spawned with the issue
   or task brief. It creates a feature branch, writes the change,
   commits, opens a PR with a clear description of what + why.
2. **Cross-review.** A Codex subagent is spawned against the PR diff
   with a review-only prompt (no write access). It posts:
   - Inline `gh pr comment` lines for specific issues.
   - A final verdict comment: `LGTM` or `CHANGES REQUESTED` with a
     prioritized list.
3. **Iterate.** If `CHANGES REQUESTED`, Claude addresses each item in
   follow-up commits. Codex re-reviews until `LGTM` or human override.
4. **Merge.** Only on `LGTM` or explicit human override. Squash-merge
   keeps history clean.

## Spawning

Both subagents run via OpenClaw `sessions_spawn`. Concrete brief shape
to be filled in once Phase 2 starts; expectations:

- Generator brief: objective, paths in scope, write-scope, link to
  PLAN/STATUS, requirement to update STATUS.md on completion.
- Reviewer brief: PR number, read-only, must check against
  `docs/PLAN.md`, `docs/HA-CONFIG-EDITING.md`, and the change's own
  description. Output format must be the verdict structure above.

## Why this exists

Smart-home config and HA APIs are easy to get plausible-but-wrong on.
Two independent provider perspectives catch:

- Hallucinated HA service names or entity attributes.
- `.storage/` direct-edit slips.
- Missed reload calls after YAML edits.
- Version-specific behavior drift (paired with `docs.lookup` rule).

## Human escalation

The reviewer flagging something does not require Rob to resolve it
directly. Reviewer comments are addressed by the generator first; Rob
is only pulled in when the two agents loop without converging, or when
either agent flags a scope/safety question.
