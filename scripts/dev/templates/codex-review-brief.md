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
- **Do not post anything to GitHub.** Return your complete findings as your final reply. The parent posts the review comment and appends the attribution line, because only the parent can read which model actually ran. Your body must therefore contain no attribution, no sign-off, and no `Reviewer model:` line.
- Never quote any denylist term or matched content in the comment or in any intermediate output.
- Scan the PR title, PR body, complete binary diff, every added or modified head blob, every deleted base blob, and every commit message with `scripts/dev/confidentiality-check`. Any match requires REQUEST CHANGES without reproducing the matched content.
- Scan your complete findings with `scripts/dev/confidentiality-check` before returning them. Never return unscanned content.
- Before returning, re-read the live PR head and report drift instead of findings if it differs from `<HEAD_SHA>`.

Begin your findings with exactly `APPROVE` or `REQUEST CHANGES` on its own first line.

Immediately after that line, include the head you reviewed exactly once, in this
form and nowhere else in the body:

```
Reviewed head: `<HEAD_SHA>` (base `<BASE_SHA>`)
```

Use the full forty-character SHAs. Do **not** repeat the head SHA anywhere else
— not in the verdict sentence, not in a finding, not in a checks summary. One
mention is what makes the comment readable; it is also what the freshness check
parses, so a second mention is ambiguous rather than merely noisy.

## Review Scope

Examine the diff for:

1. Correctness — logic errors, off-by-one, missing edge cases, unhandled error paths.
2. Security — injection risks, credential exposure, insecure defaults.
3. Style / quality — non-obvious complexity, missing type annotations, test gaps.
4. Over-engineering and misapplied simplicity — read
   `docs/design/CODING-PRINCIPLES.md` first. It organizes rules by domain class,
   because "simple and correct" differs between a script editing tracked files
   and a command mutating live Home Assistant state. Flag both directions:
   complexity with no stated domain justification, and a class A rule such as
   "trust git for atomicity" applied to a class B, C, or D operation where no
   rollback layer exists. Deletion is a valid finding; so is objecting to a
   deletion that removes a real safety property.
