# Coding principles

## The principle

Prefer simple, correct code. Complexity is warranted only when a domain-specific
requirement justifies it. Deleting code is a valid response to a review finding.
When a defensive layer causes bugs the reviewer keeps finding, ask first whether
the layer needs to exist.

## Why this doc is organized by domain

"Simple and correct" does not mean the same thing everywhere in this repository.
A script that rewrites a tracked JSON file and a command that turns on a light
have different recovery stories, and a rule learned from one is actively
dangerous applied to the other.

The classes below say what simple-and-correct means in each. **Before applying a
rule, identify which class the code belongs to.** Applying a class A rule to a
class C operation is a defect, and reviewers should flag it as one.

### A. Scripts editing files in this repository

`scripts/`, and anything else operating on tracked files in a developer or CI
checkout.

Git provides recovery. A partial run is repaired by `git checkout -- .` followed
by a retry, and the operator is present to do it. Do not build snapshot,
rollback, or transactional-replace machinery that only approximates what git
already guarantees. Fail loudly and early; a clean crash is better than a silent
partial success plus cleanup code nobody has exercised.

### B. `fs.*` node commands writing outside the repository

Eleven commands writing under the allowed roots enforced by `safe_path.py` and
`safe_fd.py`.

Git does not help here and the caller may have no retry story. Defensive
semantics are warranted: atomic writes, path containment enforced at the
boundary, and explicit structured error codes on refusal. The containment check
is not optional caution, it is the security property.

### C. `ha.*` commands mutating live Home Assistant state

Forty commands. **There is no rollback layer.** A `call_service` that flips a
light is permanent until another call reverses it, and nothing in this repo can
undo it.

Defensive coding is required, specifically:

- Fail closed on malformed input rather than guessing intent.
- **Refuse before the side effect**, not after. Ordering is the correctness
  property: validation and policy checks must complete before the request body
  is built and sent.
- Explicit denylist gates for privileged services.
- Structured, specific errors when a call is refused, so a caller can tell a
  refusal from a failure.

Authorization envelopes and tier policy earn their complexity here.

### D. `system.run` and commands with external-system side effects

Same posture as C, and for the same reason: the effects are irreversible and
land on the host rather than in this repo. Approval envelopes, argv binding to
an approved plan, and plan-mismatch detection are warranted complexity, not
defensive clutter.

### E. Network I/O

`ha_client.py` and anything else talking to an external system.

Timeouts, bounded retries with backoff, and response size caps are warranted.
Do not paper over transient failures silently: a swallowed error here becomes a
wrong answer upstream.

### F. Generic in-repo Python

Handlers, utilities, tests.

Prefer clarity over cleverness. Type hints and docstrings on public interfaces.
Skip defensive checks for invariants the type system already guarantees; a
runtime assertion that mypy has already proved is noise.

## Worked examples

### Simplification was correct

**The release stamping script** (`scripts/mark-commands-shipped.py`, PR #305).
A review found a real bug in its custom snapshot, metadata-restore and
transactional-replace path. That code was class A: it edits tracked files, so
git already provided the recovery it was approximating. The machinery was
deleted rather than repaired. The file went from 541 to 348 lines and its test
file from 734 to 85; the stamping path itself is now roughly 40 lines, with the
remaining bulk being the `--check-release-bump` CI gate, which was kept.

**Second example: none yet.** The other recent simplification-shaped findings
were genuine defects in load-bearing code, not excess machinery. This section
should gain an entry only when deletion is actually the right call again, not to
fill the slot.

### Defensive code was warranted

**The exec-approval envelope binding** (`system.run`, PR #277, merge
`6680114e3be208ecc2cc8516ee7f80d74fdff254`, building on PR #274, merge
`222ce4451738fb8b6882fc935eac4cc5044bfda6`). Class D. `system.run` executes on
the host with effects this repo cannot undo, so the approval envelope is the
security contract rather than a safety net around it. Binding the executed argv
to the approved plan, and refusing on mismatch, is the feature. A review of this
code found a partial-plan authorization bypass, and the correct response was to
tighten the binding, not to question whether binding should exist.

**The `ha.call_service` denylist gate** (PR #295, merge
`bd66d0cc957f1ab4bfc34e581476277f0af8ff67`). Class C. Generic service calls can
reach privileged effects that have dedicated, policy-gated commands. The gate
refuses **before** the request body is built, which is why the ordering is a
correctness requirement and not a stylistic preference: a check that ran after
the body was assembled could still dispatch on a bug in the matcher.

## The test before adding a principle here

> Would this principle apply verbatim in a completely different domain class
> from the one that motivated it? If yes, generalize it or move it into a domain
> section. If no, qualify it with the domain it applies to.

The motivating case for this document failed that test. "Trust git for
atomicity" is true for class A and wrong for class C, where no rollback layer
exists at all.

## What this document is not

This is **not** a record of every lesson learned. It is the smallest set of
durable principles that generalizes safely across the domain classes above.

A new entry requires a real cross-domain principle. A project-specific incident
belongs in the PR that fixed it, or in a domain section if it changes what
simple-and-correct means for that class. Growing this file with incident notes
would make it the thing it warns against.

It is also not an argument against error handling. Handle errors that occur,
validate input arriving from outside the process, and keep every check that
protects a real invariant. The target is invented machinery, not care.
