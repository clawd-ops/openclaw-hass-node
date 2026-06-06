# Status

> **Update this file at every meaningful state change.** It is the
> single thing that tells future-Clawd "where am I". If `PLAN.md` and
> `STATUS.md` disagree, fix whichever is wrong before continuing.

## Current phase

**P0 — Planning** (active)

## Last completed

- 2026-06-05 — Project bootstrapped at `~/.openclaw/projects/openclaw-hass-node/`.
- 2026-06-05 — `PLAN.md`, `STATUS.md`, `COMMAND-SURFACE.md`, `PACKAGING.md` drafted.
- 2026-06-05 — Repo pushed: https://github.com/clawd-ops/openclaw-hass-node
- 2026-06-05 — Issue #1 "Direction" responded to; folded all 5 rules into docs:
  - `fs.delete` via `trash-cli`, added `fs.restore`.
  - Snapshot model: git-per-proposal + Supervisor snapshot pre-batch.
  - `ha.config.*` surface for automations/scripts/scenes/lovelace/blueprints.
  - `docs.lookup` versioned against running HA core.
  - Cross-validated build process (Claude generates, Codex reviews).
- 2026-06-05 — New docs: `HA-CONFIG-EDITING.md`, `PROCESS.md`.

## Next step

Rob to review `PLAN.md`. Decide:
- Approve scope as written, or trim.
- Approve phase order, or reorder.

After Rob's review, advance to **P1 — Research**. P1's first task is:
**confirm whether HA's conversation agent interface can be driven from an
add-on without a companion `custom_components/` integration.** That
answer gates Plan A vs Plan B in P5.

## Open blockers

None.

## Decision log

- 2026-06-05 — Single node per HA. (Rob)
- 2026-06-05 — All `/config` mutations go through agent-bridge. (Rob)
- 2026-06-05 — Add-on first. HACS only as last resort. (Rob)
- 2026-06-05 — Code lives under `~/.openclaw/projects/openclaw-hass-node/`. (Rob)
- 2026-06-05 — Docs in `docs/` are source of truth across compactions. (Rob)
- 2026-06-05 — Deletes use `trash-cli`, recoverable via `fs.restore`. (Rob, issue #1)
- 2026-06-05 — Snapshots: git per proposal + Supervisor snapshot pre-batch. No sidecar `.bak`. (Rob, issue #1)
- 2026-06-05 — `.storage/` files are never touched directly; UI-mode config goes through HA WS/REST. (Rob, issue #1)
- 2026-06-05 — Node must be HA-version-aware via `docs.lookup` against installed version. (Rob, issue #1)
- 2026-06-05 — Build process: Claude generates, OpenAI (Codex) reviews; cross-provider required. (Rob, issue #1)
