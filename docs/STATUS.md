# Status

> **Update this file at every meaningful state change.** It is the
> single thing that tells future-Clawd "where am I". If `PLAN.md` and
> `STATUS.md` disagree, fix whichever is wrong before continuing.

## Current phase

**P3 — Filesystem + shell surface** (P3.2.1 merged; P3.2.2 next)

P2 merged on 2026-06-06 (`2c83bfd`, PR #2) via human override.
P3.1 merged on 2026-06-06 (`3542bdd`, PR #3) after Codex cross-review
returned APPROVE-WITH-NITS on re-review #3 via the CLI workaround (see
`docs/PROCESS.md` "Codex CLI fallback"). v1 was BLOCK (10 findings),
v2 REQUEST-CHANGES (NUL-bypass HIGH), v3 APPROVE-WITH-NITS.
P3.2.1 merged on 2026-06-06 (`13687a5`, PR #4) after Codex cross-review:
v1 REQUEST-CHANGES (7 findings), v2 APPROVE (all 8 items resolved). Fixes:
parent-dir fsync for crash-durability, datetime-based `at=` comparison,
ValueError/TypeError catch in `from_json`, cap raised 200→250, docs for
concurrency model, orphan behavior, and case-sensitive FS assumption.

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
- 2026-06-06 — P3.1 read-only fs/system PR opened:
  clawd-ops/openclaw-hass-node#3.
- 2026-06-06 — P3.1 MERGED (`3542bdd`). Codex cross-review iterated v1→v3
  via the CLI workaround; landed APPROVE-WITH-NITS. 135 tests, 96.26%
  branch coverage. One non-blocking nit: trailing-slash on regular file
  opens it (not an access bypass; tighten when convenient).

## Current task

P3.2.2 — dispatcher wiring: `fs.write`, `fs.restore`, `fs.history`, `fs.diff`
backed by `backup_store.BackupStore`; agent-bridge proposal flow integration.

## Codex review status

PR #3 cross-review returned BLOCK with 10 findings. Fix mapping:

- BLOCKER `system.which` executed caller-resolved binaries: fixed by
  `4bd79f3` (`system.which` is lookup-only, basename-only, no version
  probe).
- HIGH safe path TOCTOU in downstream fs ops: fixed by `576226e` and
  `05f76a2` (fd-rooted `safe_fd.open_safe_fd`, fd-based read/stat/list/glob).
- HIGH `fs.read` size race: fixed by `576226e` (bounded `os.read` of
  `max_bytes + 1` from the opened fd).
- MED `fs.list` unbounded sort: fixed by `05f76a2` (streaming
  `scandir` with bounded collection before sort).
- MED `fs.glob` unbounded traversal and bad pattern handling: fixed by
  `05f76a2` (`BAD_PATTERN`, fd-rooted bounded walker, hidden filter during walk).
- MED gateway connect advertised wrong commands: fixed by `add3150`
  (advertises exactly `ping`, `fs.*`, `system.which`).
- MED gateway generic command error leaked exception text: fixed by
  `add3150` (generic wire error, full exception only in logs).
- LOW `OUT_OF_BOUNDS` leaked resolved paths: fixed by `add3150`
  (generic exception string and fs wire messages).
- LOW bind mount policy ambiguity: fixed by docs commit for this status
  update (operator-configured bind mounts under allowed roots are trusted).
- LOW test gaps: fixed across `4bd79f3`, `576226e`, `05f76a2`, and
  `add3150`.

## Last P2 completed milestones

- P2.1 — Repo scaffolding: `pyproject.toml` (uv workspace),
  `addon/Dockerfile` + `config.yaml`, `custom_components/openclaw_gateway/`
  stub, GitHub Actions workflow.
- P2.2 — Node entrypoint that detects add-on vs standalone mode and
  opens the gateway WS connection.
- P2.3 — Pairing handshake against the gateway, Ed25519 device identity,
  key persistence under `/data/openclaw/node-key.json`.
- P2.4 — `ping` command end-to-end, command dispatcher, gateway
  invoke/result loop.

## P2 additional scope delivered

- `http_api.py` — local aiohttp HTTP server (port 8099) with `/health`,
  `/commands/ping`, `/v1/commands/{cmd}`, `/ha/snapshot` (read-only HA
  REST proxy), and `/v1/conversation` (Assist placeholder).
- 57 tests, 99.76% branch coverage.

## Next step

Begin P3.2 — proposal-gated writes (`fs.write`, `fs.patch`, `fs.append`)
backed by per-file content-addressed backup store, plus `system.run`
behind the `operator.admin` scope. Cross-review continues to run via the
Codex CLI fallback until OpenClaw's openai/* routing regression is
resolved (see [memory: project_codex_oauth_regression_2026_06_06]).

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
- **P1.2 (2026-06-05) — agent-bridge connectivity.** Verdict:
  **Gateway brokers.** Node speaks only the gateway WS protocol; emits
  `node.propose` over its existing WS connection and the gateway
  translates to agent-bridge MCP calls. Keeps the node dumb, single
  auth path, one audit trail. See
  `docs/RESEARCH-AGENT-BRIDGE-CONNECTIVITY.md`.

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
- 2026-06-05 — Proposals are gateway-brokered. Node speaks only the
  gateway WS protocol; does not connect to agent-bridge directly. See
  `RESEARCH-AGENT-BRIDGE-CONNECTIVITY.md`. (Clawd, P1.2)
- 2026-06-05 — Language: Python 3.13+ for node and shim. Quality
  gates: `mypy --strict` + `pyright --strict`, Google-style docstrings
  (`ruff` D-rules + `pydoclint`), 100 % branch coverage via pytest,
  `ruff` lint/format, `bandit`, `pip-audit`. All gated in GitHub
  Actions. See `QUALITY.md`. (Rob, issue #1 round 3)
- 2026-06-05 — MCP retirement: node must demonstrably handle every
  call surface the existing MCP servers serve, across every agent
  that uses them, before retirement. Trigger: zero unhandled
  `mcp__homeassistant*` calls for 7 days *and* a written migration
  inventory. No calendar-based default. Cutover is one PR.
  (Rob, P1.3)
- 2026-06-05 — Versioning: date-based `YYYY.M.PATCH` matching the HA
  release the node is tested against (e.g. `2026.6.0`). Patch
  increments for fixes within a HA release. (Clawd recommendation,
  Rob "ok either way", P1.4)
