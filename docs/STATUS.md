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

P1.2 — agent-bridge connectivity model (node direct vs gateway broker).

## Next step

- P1.2 — agent-bridge connectivity model (node direct vs gateway
  broker).
- P1.3 — MCP server retirement criteria.
- P1.4 — Versioning scheme for the add-on image (semver vs date).

## Completed P1 research

- **P1.1 (2026-06-05) — Conversation agent registration.** Verdict:
  **Plan A not viable, Plan B required.** HA's conversation registration
  (`async_set_agent` / `ConversationEntity`) is in-process Python only;
  there is no WS/REST/Supervisor path that lets an external process
  register an agent. All precedent ships as `custom_components/`.
  Decision: ship a thin ~150 LOC `custom_components/openclaw_gateway/`
  HACS shim alongside the add-on, whose sole job is to register a
  `ConversationEntity` that forwards turns to the add-on's local
  socket. Full citations in `docs/RESEARCH-CONVERSATION-AGENT.md`.

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
- 2026-06-05 — Assist conversation agent: ship as add-on **plus**
  thin `custom_components/openclaw_gateway/` HACS shim. Plan A
  (add-on alone) confirmed not viable; see
  `RESEARCH-CONVERSATION-AGENT.md`. (Clawd, P1.1)
