# Compatibility Matrix

**Purpose:** record the exact component versions and architecture that each live
verification actually ran against, so a completion claim can be tied to a
concrete environment instead of a branch state.

This file is the evidence artifact required by Phase 0 of
[`COMPLETION-ROADMAP.md`](COMPLETION-ROADMAP.md) ("Record the exact app, plugin,
HACS, Gateway, HA Core, Supervisor, and architecture versions used by each live
verification") and referenced by the Phase 5 and Phase 6 release gates.

It is maintained by hand from observed values. It is not generated, and it must
never be filled in from repository declarations when the question being asked is
what a running system had installed.

## Recording rules

- One row set per live verification. A verification that did not capture a field
  records `not recorded`, never an inferred or repository-declared value.
- Distinguish the **source** of every value:
  - `observed-live` — read from the running system during the verification.
  - `repo-declared` — read from a tracked file in the repository at the stated
    commit. Valid for what the source tree claims, not for what is installed.
  - `not recorded` — not captured, and not reconstructible after the fact.
- Add-on, integration, and plugin versions are separate fields even when the
  release process keeps them in step. A release that advances one and not the
  others is the exact failure this table exists to catch.
- Architecture means the architecture the verified artifact actually ran on, not
  the set of architectures the add-on advertises support for.
- Record the advertised command count observed from the running node alongside
  the count the source tree produces. A difference is expected whenever merged
  source has not yet shipped, and it is the evidence that a source-only fix must
  not be ticked as released.

## Required fields

| Field | Meaning |
|---|---|
| Verification | The verification document or gate this environment belongs to |
| Date | Date the values were observed |
| App (add-on) | Installed add-on version |
| Add-on state | Supervisor-reported run state at observation time |
| HACS integration | Installed custom integration version |
| Plugin | Gateway plugin package version |
| Gateway | OpenClaw Gateway version |
| HA Core | Home Assistant Core version |
| Supervisor | Home Assistant Supervisor version |
| Host OS | Home Assistant Operating System version, where applicable |
| HACS | HACS version |
| Architecture | Architecture the verified artifact ran on |
| Advertised commands (live) | Command count advertised by the running node |
| Advertised commands (source) | Command count the source tree advertises |

## Recorded environments

### 2026-09-12 — current installed environment

Observed through read-only node commands against the running production node. No
mutation was performed. This is the environment any further live verification
starts from until a component is updated.

| Field | Value | Source |
|---|---|---|
| Verification | current installed baseline (no new verification claimed) | — |
| Date | 2026-09-12 | — |
| App (add-on) | `2026.7.23b1` | observed-live |
| Add-on state | `started`, no update available | observed-live |
| HACS integration | not recorded | the tracked manifest declares `2026.7.23b1`, but the version HA actually loaded is not observable; see [gap 2](#known-recording-gaps) |
| Plugin | not recorded | the tracked package declares `0.1.1`, but the version the Gateway actually loaded is not observable; see [gap 2](#known-recording-gaps) |
| Gateway | `2026.9.3` | observed-live |
| HA Core | `2026.9.1` (`2026.9.2` available, not installed) | observed-live |
| Supervisor | `2026.09.0` | observed-live |
| Host OS | `18.2` | observed-live |
| HACS | `2.0.5` | observed-live |
| Architecture | not recorded | see [Known recording gaps](#known-recording-gaps) |
| Advertised commands (live) | 51 | observed-live |
| Advertised commands (source) | 56 | repo-declared (`de8404e`) |

**Advertisement delta, 51 live versus 56 in source.** The running artifact
`2026.7.23b1` does not advertise these five commands, which exist only in merged
source:

| Command | Merged under | Released in `2026.7.23b1` |
|---|---|---|
| `ha.addon_update` | #260 via PR #284 | no |
| `ha.update_install` | #260 via PR #284 | no |
| `system.run.prepare` | #258 via PRs #274 / #277 | no |
| `system.execApprovals.get` | #258 via PRs #274 / #277 | no |
| `system.execApprovals.set` | #258 via PRs #274 / #277 | no |

No command is advertised by the running node and missing from source. This
confirms the roadmap's released-artifact rule is doing real work: both of those
changes are correct at source and still unreleased, so neither may be ticked as
complete on release-tie grounds.

### 2026-09-11 — command surface verification

Environment for [`VERIFICATION-2026-09-11.md`](VERIFICATION-2026-09-11.md).
That document captured the add-on version and the Gateway allowlist size but did
not capture the rest of the environment, and the missing fields cannot be
reconstructed after the fact.

| Field | Value | Source |
|---|---|---|
| Verification | `VERIFICATION-2026-09-11.md` | — |
| Date | 2026-09-11 | — |
| App (add-on) | `2026.7.23b1` | observed-live |
| Add-on state | not recorded | — |
| HACS integration | not recorded | — |
| Plugin | not recorded | — |
| Gateway | not recorded | — |
| HA Core | not recorded | — |
| Supervisor | not recorded | — |
| Host OS | not recorded | — |
| HACS | not recorded | — |
| Architecture | not recorded | — |
| Advertised commands (live) | not recorded as a count; Gateway allowlist held 55 entries | observed-live |
| Advertised commands (source) | not recorded | — |

The add-on version is the one field that carries forward, and it is unchanged at
the 2026-09-12 observation, so the 2026-09-11 command-surface findings still
describe the currently installed artifact. Every other field in that
verification is unrecorded, which is why it cannot satisfy a release gate on its
own.

## Known recording gaps

These block a complete Phase 0 recording and need closing before any release
gate can claim a version-bound environment.

1. **Architecture is not observable through the node's read-only surface.**
   `ha.addon_info` does return an `arch` field, but it is the add-on's
   *supported*-architecture list taken from its own manifest, not the
   architecture the artifact is running on. Two checks establish that: the value
   is identical to the `arch:` list declared in `app/config.yaml`, and a
   different add-on queried the same way reports the same two values. Its
   `machine` field is empty, so it does not narrow the answer either. Nothing
   else in the read-only command set reports host architecture. Recording it
   today requires either an operator-approved `system.run` or a new read-only
   capability that reports it.
2. **Loaded plugin and integration versions are not separately observable.** The
   tracked manifest and package declare versions, but a verification needs the
   versions the Gateway and HA actually loaded, and the current surface does not
   report either. Both fields are therefore `not recorded` above rather than
   filled from the repository, because this file's own rule says a repository
   declaration cannot stand in for an installed version.
3. **No field binds an environment to an artifact digest.** Phase 5 and Phase 6
   both require digests so the tested image is provably the released image. Once
   published artifacts exist, add image and plugin digest fields here rather
   than in a separate document.

Until gaps 1 and 2 are closed, the Phase 0 recording item stays unchecked even
though most fields are now captured. A partially recorded environment is not a
recorded environment.
