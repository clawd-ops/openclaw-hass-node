# Status

> **Update this file at every meaningful state change.** It is the
> single thing that tells future-Clawd "where am I". If `PLAN.md` and
> `STATUS.md` disagree, fix whichever is wrong before continuing.

## Current phase

**P1 — Research** (active)

## Last completed

- 2026-06-05 — Project bootstrapped at `~/.openclaw/projects/openclaw-hass-node/`.
- 2026-06-05 — `PLAN.md`, `STATUS.md`, `COMMAND-SURFACE.md`, `PACKAGING.md` drafted.
- 2026-06-05 — Repo pushed: https://github.com/clawd-ops/openclaw-hass-node
- 2026-06-05 — Issue #1 first round folded in.
- 2026-06-05 — New docs: `HA-CONFIG-EDITING.md`, `PROCESS.md`.
- 2026-06-05 — Issue #1 second round (Rob): backup model rewritten,
  HA-native edits hardened, breaking-change verification made
  mandatory. Resulting changes:
  - `PLAN.md` §1b rewritten: per-file content-addressed backup store
    in `/share/openclaw-backups/`. No git, no per-change Supervisor
    snapshots, no `.bak` sidecars.
  - `PLAN.md` §2b hardened: HA-native APIs are the default; `fs.patch`
    is the exception (yaml-only / custom things / blueprints).
    `.storage/` is read-only at the command dispatcher; writes require
    explicit `unsafe_storage=true` plus user-accepted proposal.
  - `PLAN.md` §2c expanded: `docs.breaking_changes` command,
    mandatory pre-change verification, cross-validated by Codex.
  - `HA-CONFIG-EDITING.md` rewritten around the HA-native-first rule
    and the per-domain API map.
  - New `BACKUPS.md` covers the per-file store format, retention,
    restore flow, and DR.

## Current task

P1.1: **Conversation agent registration from outside `custom_components/`.**
Goal: determine whether a HA Assist conversation agent can be
registered by an add-on (or via the Supervisor / WS API) without a
companion `custom_components/` Python integration. Output: a written
decision in `docs/RESEARCH-CONVERSATION-AGENT.md` with cited HA source
references, and a Plan A vs Plan B recommendation.

## Next step

After P1.1 lands, the next P1 items are:

- P1.2 — agent-bridge connectivity model (node direct vs gateway
  broker).
- P1.3 — MCP server retirement criteria.
- P1.4 — Versioning scheme for the add-on image (semver vs date).

## Open blockers

None.

## Decision log

- 2026-06-05 — Single node per HA. (Rob)
- 2026-06-05 — All `/config` mutations go through agent-bridge. (Rob)
- 2026-06-05 — Add-on first. HACS only as last resort. (Rob)
- 2026-06-05 — Code lives under `~/.openclaw/projects/openclaw-hass-node/`. (Rob)
- 2026-06-05 — Docs in `docs/` are source of truth across compactions. (Rob)
- 2026-06-05 — Deletes use `trash-cli`, recoverable via `fs.restore`. (Rob, issue #1)
- 2026-06-05 — Node must be HA-version-aware via `docs.lookup` against installed version. (Rob, issue #1)
- 2026-06-05 — Build process: Claude generates, OpenAI (Codex) reviews; cross-provider required. (Rob, issue #1)
- 2026-06-05 — Backups: purpose-built per-file content-addressed
  store under `/share/openclaw-backups/`. No git in `/config`. No
  per-change Supervisor snapshots. (Rob, issue #1 round 2)
- 2026-06-05 — HA-native APIs are the default for HA-managed config;
  `fs.patch` is reserved for yaml-only / custom files / blueprints.
  (Rob, issue #1 round 2)
- 2026-06-05 — `.storage/` is read-only to the node. Writes refused
  at the dispatcher unless `unsafe_storage=true` + accepted proposal.
  HARD rule. (Rob, issue #1 round 2)
- 2026-06-05 — Every HA config proposal must verify against the
  running version's breaking changes and include a functional fix
  when impacted. Cross-validated by Codex reviewer. (Rob, issue #1
  round 2)
