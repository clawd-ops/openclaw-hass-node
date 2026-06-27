# Docs index

> Navigation + maintenance guide for `docs/`. Read this **first** if
> you're about to update a doc and aren't sure which file owns the
> fact you're changing. Read this **also** if you're a future
> maintainer (human or agent) trying to orient.
>
> Rule of thumb: every fact lives in exactly one canonical doc. Other
> docs link to it. If two docs disagree, fix the non-canonical one to
> match the canonical one — don't update both copies.

## What each doc is for

### Orientation (read these first)

| File | Purpose |
|---|---|
| [`OVERVIEW.md`](OVERVIEW.md) | High-level user-facing intro: the three pieces (addon, HACS shim, gateway) and how they fit. |
| [`PLAN.md`](PLAN.md) | Full design doc: goals, architecture, surfaces in detail, decision log. The "why" reference. |
| [`STATUS.md`](STATUS.md) | **Canonical "where we are right now."** Current release, what works end-to-end, what's next. Update on every meaningful state change. |

### Roadmap + history

| File | Purpose |
|---|---|
| [`TODO.md`](TODO.md) | **Canonical roadmap + PR ledger.** Open items (numbered) + "Recently done" PR list in ascending order. Update when a PR merges or an item closes. |
| [`QUESTIONS-FOR-ROB.md`](QUESTIONS-FOR-ROB.md) | Append-only Q&A for things Clawd can't resolve overnight. Open → Resolved. |
| [`LESSONS.md`](LESSONS.md) | Postmortems / "I burned a day on this" recipes. Append-only. |
| [`MEMORY.md`](MEMORY.md) | Agent-side recall: snapshots of context an agent rebooting mid-project needs. |

### Identity, scopes, command surface

| File | Purpose |
|---|---|
| [`IDENTITY-AND-SCOPES.md`](IDENTITY-AND-SCOPES.md) | **Canonical identity + role model.** Actor signing (HMAC subkey derived from `local_api_token`), role resolution (`user`/`admin`/`super_admin`), per-role forbidden-command tables, per-user agent routing. |
| [`COMMAND-TIERS.md`](COMMAND-TIERS.md) | **Canonical Tier A/B/C blast-radius policy.** What each tier means, what's in scope per tier, audit/guard requirements. |
| [`COMMAND-SURFACE.md`](COMMAND-SURFACE.md) | **Canonical live command registry.** Every command the node exposes, per-tier constraints. If counts change, this file owns the change. |

### Operations + quality

| File | Purpose |
|---|---|
| [`INSTALL.md`](INSTALL.md) | **User-facing.** Step-by-step install (gateway allowlist, addon config, HACS install, verification). Self-contained on purpose — users shouldn't have to chase links. |
| [`UAT-PLAN.md`](UAT-PLAN.md) | User acceptance testing checklist. Update when phases change. |
| [`HA-CONFIG-EDITING.md`](HA-CONFIG-EDITING.md) | Domain API map: HA-native API first, `fs.patch` fallback, `.storage` read-only hard rule. |
| [`BACKUPS.md`](BACKUPS.md) | Per-file versioning design: content-addressed store, index format, restore, retention. |
| [`PACKAGING.md`](PACKAGING.md) | Build + packaging design: addon config keys, image arch matrix, version policy. |
| [`RELEASE.md`](RELEASE.md) | **Canonical release procedure.** Auto-cut via `.github/workflows/release-on-version-bump.yml` on version bump. Manual recipe preserved as emergency fallback. |
| [`PROCESS.md`](PROCESS.md) | Cross-provider code review workflow (Clawd/Opus writes → Codex/GPT-5.5 reviews). |
| [`QUALITY.md`](QUALITY.md) | CI gates (ruff, ruff format, mypy strict, pytest coverage ≥95%, bandit, pip-audit). |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Conventional commit policy, version policy, where to put what. |

### Research / context (historical)

These captured the thinking that led to the current architecture. Read for "why is it this way?" — not for current state.

| File | Purpose |
|---|---|
| [`RESEARCH-AGENT-BRIDGE-CONNECTIVITY.md`](RESEARCH-AGENT-BRIDGE-CONNECTIVITY.md) | Why the node goes through the gateway, not direct to agent-bridge. |
| [`RESEARCH-CONVERSATION-AGENT.md`](RESEARCH-CONVERSATION-AGENT.md) | Why the HACS shim is required (HA core constraint on conversation-agent registration). |
| [`RESEARCH-OPENCLAW-INTEGRATION.md`](RESEARCH-OPENCLAW-INTEGRATION.md) | P5.11 postmortem that killed the parallel-brain direction. |
| [`RESEARCH-MIGRATION.md`](RESEARCH-MIGRATION.md) | MCP server retirement readiness tracking. |

## Source-of-truth table — when a fact changes, update *one* file

| Fact that changes | Canonical doc | Other docs that mention it |
|---|---|---|
| Current shipped version | `addon/config.yaml` + `addon/build.yaml` (code is the source); `STATUS.md` references it. | `INSTALL.md`, `UAT-PLAN.md`, `PACKAGING.md`, `MEMORY.md`, `RELEASE.md` should `→` link or quote `STATUS.md`. |
| Current state (what works end-to-end) | `STATUS.md` | `OVERVIEW.md`, `MEMORY.md`, `UAT-PLAN.md` may summarize → link to `STATUS.md`. |
| Command list / command counts | `COMMAND-SURFACE.md` | `STATUS.md`, `INSTALL.md`, `UAT-PLAN.md`, `MEMORY.md` should link, not duplicate counts. |
| Tier A/B/C policy | `COMMAND-TIERS.md` | `IDENTITY-AND-SCOPES.md`, `TODO.md` item #11 link to it. |
| Identity / actor signing model | `IDENTITY-AND-SCOPES.md` | `OVERVIEW.md`, `INSTALL.md`, `authz.py` docstring link to it. |
| Open work / what's next | `TODO.md` | `STATUS.md` "next steps" links to specific TODO items. |
| Recently merged PRs | `TODO.md` "Recently done (ascending PR order)" | Don't duplicate elsewhere. |
| Release procedure | `RELEASE.md` | `CONTRIBUTING.md`, `LESSONS.md` link, don't restate. |
| Resolved Rob questions | `QUESTIONS-FOR-ROB.md` Resolved section | Cross-reference from the relevant `TODO.md` item. |

## Cheat sheet — "I just did X, which docs do I touch?"

| You just did… | Update |
|---|---|
| Merged a non-release PR | Add a one-liner to `TODO.md` "Recently done"; if it closes a numbered TODO item, mark that item closed. |
| Merged a release PR (version bump) | Auto-cut workflow handles tag + GitHub release. Then update `STATUS.md` "Where we are" if behavior changed; bump version references in `INSTALL.md`/`UAT-PLAN.md`/`PACKAGING.md`/`MEMORY.md`. (Phase 2 reshape will collapse these into a single link.) |
| Registered a new command | `COMMAND-SURFACE.md` (count + tier); add to `IDENTITY-AND-SCOPES.md` forbidden-command tables if user/admin shouldn't reach it. |
| Changed a Tier boundary | `COMMAND-TIERS.md`. Then audit `IDENTITY-AND-SCOPES.md` rules. |
| Shipped a feature that closes a TODO item | Mark the item in `TODO.md`; if it changes user-visible behavior, also `STATUS.md` and `INSTALL.md`. |
| Wrote a useful postmortem | `LESSONS.md` (append). |
| Hit a question that needs Rob | `QUESTIONS-FOR-ROB.md` Open section. Move to Resolved when answered. |
| Changed CI / quality gates | `QUALITY.md` (the gates) + `CONTRIBUTING.md` (if it changes contributor workflow). |
| Changed the release pipeline | `RELEASE.md`. |
| Changed the addon `map:` or volume layout | `PLAN.md` "Surfaces in detail" + `PACKAGING.md`. |

## Decision tree — when in doubt

1. **Is this a fact about the live code right now?** → it belongs in the canonical doc for that fact (see source-of-truth table above), not in a research doc or postmortem.
2. **Is this a fact about why we did something historically?** → `LESSONS.md` (specific incident) or a `RESEARCH-*.md` (broader design rationale).
3. **Is this a user-facing instruction?** → `INSTALL.md`, `OVERVIEW.md`, or `UAT-PLAN.md`. These are intentionally self-contained — duplicate facts here are accepted.
4. **Is this state that will change next week?** → `STATUS.md` (current) or `TODO.md` (roadmap). Not `PLAN.md`.
5. **Is this design-stable and won't change for a while?** → `PLAN.md` or the relevant canonical doc.
6. **Still unsure?** → drop it in `STATUS.md` "Open / under discussion" and ask Rob via `QUESTIONS-FOR-ROB.md`.

## Maintenance

- If you update a fact and notice another doc still has the old version, **don't** also update the copy — fix the copy by linking to the canonical doc instead. (Phase 2 reshape will systematize this.)
- A CI guard against hardcoded command counts outside `COMMAND-SURFACE.md` is on the roadmap (post-reshape).
- This file itself is the canonical "what does each doc do" — if a new doc is added, add a row here in the same commit.
