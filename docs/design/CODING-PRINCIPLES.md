# Coding principles

Simple, current, accurate code is preferred over defensive, robust, complex
code. This page is context for contributors and for reviewers: a reviewer should
flag over-engineering as readily as a missing edge case.

## Prefer simple over defensive

Defensive layers are not free. They are code that must be read, maintained,
tested, and reasoned about, and they carry their own bugs. A recovery path that
is never exercised in practice is a liability, not a safety net.

Before adding machinery that guards against a failure, ask whether the failure is
real in this system, and whether something below already handles it.

## Deletion is a valid response to a review finding

When a review finds a bug in a defensive layer, the first question is **"does
this layer need to exist?"** — not "how do I fix it?"

Patching the layer keeps its cost and adds more. Deleting it removes the bug and
the maintenance burden together. Reach for the fix only once deletion is ruled
out.

Worked example from this repository: the release stamping script grew a custom
snapshot, metadata-restore and transactional-replace path so a partial failure
could be rolled back. A review found a real bug in it, an access-time side
effect on a failed multi-file acquisition. The script edits tracked files, so
`git checkout -- .` already recovers a partial run completely. The machinery was
deleted rather than repaired: 541 lines to 348, and its test file from 734 lines
to 85. The bug went with it.

## Trust the layer that already guarantees it

Do not reimplement atomicity, transactions, locking, or rollback that git, the
operating system, the database, or the framework already provides.

- A script mutating tracked files does not need rollback. Fail cleanly, let the
  operator run `git checkout -- .` and retry.
- A single `write_text` does not need a temporary sibling and a rename unless a
  concurrent reader genuinely exists.
- Prefer a loud, early failure over a silent partial success followed by
  cleanup logic nobody has run in anger.

## Complexity requires justification tied to a real need

Complexity earns its place by serving a real domain requirement, not an imagined
failure mode. When it is genuinely needed, say why in a comment at the point of
difficulty, so the next reader can re-evaluate the tradeoff rather than
preserving it out of caution.

Complexity that does earn its keep is kept. Simplification is not an excuse to
drop a working gate or a real safety property. In the same change that deleted
the stamping machinery, the `--check-release-bump` gate was left untouched: it
is wired into CI and enforces an invariant nothing else checks.

## Fix a class of bug once, in a small place

When the same mistake can occur in several spots, fix it at the root so it stays
fixed. The `re.match` versus `re.fullmatch` defect was corrected in the shared
version parser rather than at each call site, so a trailing newline can no
longer be accepted in one place and rejected in another.

## What this is not

This is not an argument for sloppiness or for dropping error handling. Handle
errors that actually occur, validate input that actually arrives from outside,
and keep the checks that protect a real invariant. The target is invented
machinery, not care.
