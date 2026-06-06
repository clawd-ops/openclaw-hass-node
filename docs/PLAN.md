# openclaw-hass-node — Plan

> **Resuming after a compaction?** Read this file end-to-end, then
> `STATUS.md`. These two files are the source of truth — your in-context
> memory is not. Update `STATUS.md` whenever you change phase or finish a
> milestone. If something here is wrong, fix the doc first, then the code.

## Goal

Build a single OpenClaw node that runs on a Home Assistant host (as an
add-on, with the same image runnable as a standalone Docker container) and
gives the gateway three capability surfaces in one process:

1. **Filesystem + shell** on the HA host (`/config`, `/share`, `/addons`,
   `/ssl`, `/media`, Supervisor API).
2. **HA control** — states, services, areas, devices, registry,
   automations, traces, logbook. Replaces the existing `homeassistant` +
   `homeassistant-readonly` MCP servers for this HA.
3. **Assist conversation agent** — registers this gateway as a HA
   conversation agent so Assist turns go to Clawd. Replaces the Anthropic
   conversation integration.

The node speaks the standard OpenClaw gateway WS protocol (role: `node`)
and pairs once via `openclaw devices approve`.

## Non-goals

- Multi-HA from one node. One node per HA instance.
- Replacing the gateway/model. The node is a peripheral, not a brain.
- Direct writes to `/config`. All mutations go through agent-bridge.

## Architecture

```
+------------------+        WS (gateway protocol)        +-----------------+
|  OpenClaw GW     | <----------------------------------> |  HASS Node      |
|  (Clawd model)   |        role: node, scopes:           |  (this repo)    |
|                  |        operator.write, operator.admin|                 |
+------------------+                                      +--------+--------+
                                                                   |
                                                  +----------------+----------------+
                                                  |                |                |
                                              +---v---+       +----v----+      +---v---+
                                              | shell |       | HA REST |      |Assist |
                                              | /cfg  |       |  + WS   |      | agent |
                                              | fs    |       |         |      | reg.  |
                                              +-------+       +---------+      +-------+
                                                  ^
                                                  | (writes only)
                                          +-------+--------+
                                          |  agent-bridge  |
                                          |  proposals     |
                                          +----------------+
```

Inside the add-on container we mount HA volumes per the HA add-on spec.
The node process is the only thing in the container. The Supervisor API
token (`SUPERVISOR_TOKEN`) is available when running as an add-on; when
running standalone, `HASS_URL` + `HASS_TOKEN` env vars are used instead.

## Surfaces in detail

### 1. Filesystem + shell

- Mounted paths (add-on `config.yaml` map): `config:rw`, `share:rw`,
  `addons:rw`, `ssl:rw`, `media:rw`, `backup:rw`.
- Bind mounts placed under an allowed root are treated as operator-trusted
  configuration; the read-only command layer does not try to distinguish or
  defeat them.
- Commands: `fs.read`, `fs.list`, `fs.stat`, `fs.glob`, `system.run`,
  `system.which`. Writes (`fs.write`, `fs.move`, `fs.delete`,
  `fs.patch`) are **proposal-gated** — they accept the args but emit an
  agent-bridge `propose_edit` and wait for resolve.
- `fs.delete` uses `trash-cli`, never `rm`. New `fs.restore` command
  recovers from trash. No sidecar `.bak` files anywhere.
- `system.run` requires `operator.admin` scope on pairing approval.
- Supervisor API exposed via `ha.supervisor.*` commands wrapping
  `http://supervisor/...` with `SUPERVISOR_TOKEN`.

### 1b. Backup / undo model

Purpose-built per-file versioning. No git in `/config`. No Supervisor
snapshot per file change. No `.bak` sidecars next to live files.

- **Store**: `/share/openclaw-backups/` (outside `/config`, survives
  add-on rebuilds, included in normal HA backups).
- **Layout**: content-addressed object store + per-path index.
  - Objects: `objects/<sha256[0:2]>/<sha256>` — raw prior bytes,
    deduplicated across versions and files.
  - Index: `index/<url-encoded-path>.jsonl` — one line per version:
    `{ts, proposal_id, sha256, size, op}`.
- **When written**: every applied proposal that mutates a file under a
  protected root captures the *prior* bytes before write. Deletes
  capture the prior bytes and mark `op: "delete"`.
- **Restore**: `fs.restore path=<p> [--at <ts>|--proposal <id>|--version <n>]`
  proposal-gates the restore itself (it's a write).
- **Retention**: default keep-all up to a configurable cap (e.g. 500 MB
  per node); LRU evict whole versions once cap is hit, never partial.
  Per-path "pin last N versions" override for hot files.
- **Diff/list**: `fs.history path=<p>` lists versions; `fs.diff
  path=<p> from=<v> to=<v>` produces a unified diff.
- **Supervisor snapshots**: only used when *the user explicitly opts
  in* per operation, or on user-defined cadence — never automatically
  per proposal. Coarse, expensive, and not the right grain for normal
  edits.

See `docs/BACKUPS.md` for the storage format and edge cases.

### 2. HA control

- Commands mirror what the existing MCP servers offer:
  `ha.list_states`, `ha.get_state`, `ha.list_services`,
  `ha.call_service`, `ha.list_areas`, `ha.list_devices`,
  `ha.list_entity_registry`, plus convenience wrappers
  (`ha.light_turn_on/off`).
- Transport: HA WebSocket API for state/event streams; REST for
  one-shots. Token comes from `SUPERVISOR_TOKEN` (add-on) or
  `HASS_TOKEN` env (standalone).
- Once stable, the existing `homeassistant` + `homeassistant-readonly`
  MCP servers in this gateway config are removed for this HA.

### 2b. Config editing — HA-native first, fs is the exception

**Hard rule, no exceptions without explicit user override**:

- HA-managed config domains (automations, scripts, scenes, dashboards,
  helpers, areas/devices/entities, integrations/config entries) are
  edited **only** through HA's native REST/WS config APIs, regardless
  of whether they currently live in YAML or `.storage/`. The node
  exposes a `ha.config.<domain>.*` surface that hits those APIs.
- `fs.patch` against `/config/` is reserved for files HA has no API
  for. That includes: `configuration.yaml` top-level (only when the
  change can't be expressed as a helper/integration via API), YAML-only
  integrations, packages, `custom_components/`, themes, custom JS
  modules, blueprint YAML in `/config/blueprints/`, and user-authored
  yaml the user has placed there.
- **`.storage/` is read-only to the node.** Reads allowed for
  diagnostics. Writes are refused at the command layer with a clear
  error, even if a proposal tries to target it. The only way to write
  `.storage/` is an explicit `--unsafe-storage` flag on the call *plus*
  a proposal that the user accepts. This is a HARD rule baked into the
  command dispatcher, not a guideline.
- Blueprints always live in `/config/blueprints/`; blueprint edits go
  through proposal-gated `fs.patch` since there's no REST API for
  them.

See `docs/HA-CONFIG-EDITING.md` for the per-domain API map.

See `docs/HA-CONFIG-EDITING.md` for per-domain detail.

### 2c. Always rooted in installed HA version + breaking-change verification

- Node detects HA core version on connect (Supervisor `/info` or
  `/api/config`), emits it as pairing metadata so gateway model
  always knows the live version.
- New `docs.lookup(topic, version=current)` command that fetches from
  the `home-assistant/home-assistant.io` repo at the tag matching the
  running core version, with local cache.
- New `docs.breaking_changes(version=current, since=<prev>?, domain=?)`
  command. Pulls the relevant release notes' breaking-changes section
  from the docs repo. Used by the rule below.

**Mandatory pre-change verification (HARD rule):**

Before any proposal that touches HA config (yaml or API-driven), the
generator must:

1. Call `docs.lookup` for the target domain at the running version.
2. Call `docs.breaking_changes` covering the running version (and any
   versions since the last time the touched domain was edited, if
   trackable).
3. If a breaking change affects the edit, the proposal must include
   the functional fix, not just the original edit. The proposal body
   must cite the specific breaking-change entry.
4. `ha.check_config` (for yaml) or domain reload-dry-run (for API
   edits where supported) before commit.

**Cross-validation of the verification:** the Codex reviewer pass
(see `PROCESS.md`) re-runs `docs.breaking_changes` against the diff
and blocks merge if the generator missed a relevant breaking change
or fix.

### 3. Assist conversation agent

**Decision (P1.1, 2026-06-05): Plan B.** HA's conversation agent
registration is in-process Python only — `async_set_agent` and
`ConversationEntity` both require a live `HomeAssistant` + `ConfigEntry`
in the HA process. No WS, REST, or Supervisor surface exposes
registration. All precedent (openai_conversation, anthropic, ollama,
extended_openai_conversation, etc.) ships as `custom_components/`.
Full research in `docs/RESEARCH-CONVERSATION-AGENT.md`.

- The add-on remains the workhorse: pairing, HA client, fs/system,
  proposal handling, and a local HTTP/WS endpoint for forwarded turns.
- Ship a thin `custom_components/openclaw_gateway/` (~150 LOC):
  - `manifest.json`
  - Config flow capturing the add-on's local socket (e.g.
    `http://a0d7b954-openclaw-gateway:8099`)
  - One `ConversationEntity` subclass whose `async_process` proxies
    turns over HTTP/WS to the add-on, streams tokens back via
    `chat_log`.
- Distribution: HACS-installable via the same repo
  (`hacs.json` + `custom_components/openclaw_gateway/`), and bundle
  install instructions referencing the add-on slug for socket
  discovery. Future: an add-on first-run hook can write a
  per-instance pairing token the shim reads via `/api/services`.

## Mutation control (agent-bridge gated)

- Every write-shaped command on the node has two outcomes:
  - If `dry_run=true` or `agent_bridge=true` (default for `/config`):
    emit `propose_edit` to agent-bridge with the patch/content, return
    proposal ID. Apply only after `resolve_proposal(accepted)`.
  - If `agent_bridge=false` and path is outside protected roots
    (`/tmp`, `/share/clawd-scratch`): apply directly.
- Protected roots (always proposal-gated, no override):
  `/config`, `/addons`, `/ssl`.

## Pairing + identity

- Node identity: `hass-node@<ha-instance-id>` (UUID from HA core).
- Scopes requested on first connect: `operator.write` + `operator.admin`.
- One-time approval: `openclaw devices approve <requestId>`.
- Token rotation handled by gateway. Node persists its key under
  `/data/openclaw/node-key` (HA add-on `/data` is per-add-on durable).

## Packaging

- Single Docker image. Two run modes:
  - **HA add-on**: `config.yaml` declares slug, mapped volumes,
    `hassio_api: true`, `hassio_role: admin`, `homeassistant_api: true`.
    Built per HA arch matrix (`amd64`, `aarch64`, `armv7`).
  - **Standalone Docker**: `docker run` with explicit volume mounts and
    `HASS_URL` + `HASS_TOKEN` env. Same entrypoint detects which mode
    it's in.
- Repo published as a HA add-on repository (`repository.yaml`) so users
  can add the URL in HA → Add-on Store → Repositories.

See `PACKAGING.md` for the full layout.

## Cross-validated code changes (build process)

Every code change to this repo follows:

1. Claude Code subagent generates the change, opens a PR.
2. A Codex (OpenAI, pi runtime) subagent is spawned with a review-only
   prompt against the diff; posts inline comments + verdict.
3. Merge only if Codex returns no blocking issues, or Claude addresses
   them in a follow-up commit that Codex re-reviews.
4. Process and prompts live in `docs/PROCESS.md`.

## Code quality gates

In addition to cross-review, every PR must pass a mechanical quality
bar enforced in GitHub Actions:

- Strict type checking: `mypy --strict` + `pyright --strict`.
- Google-style docstrings on every public symbol (`ruff` D-rules +
  `pydoclint`).
- 100 % branch coverage on shipped code (`pytest` + `coverage.py`).
- Lint/format: `ruff check` and `ruff format --check`.
- Security: `bandit`, `pip-audit`.
- Add-on smoke build for `amd64`/`aarch64`/`armv7`.

Full details in `docs/QUALITY.md`. Language is Python 3.13+ for both
the node and the `custom_components/openclaw_gateway/` shim (the
shim is required to be Python by HA core, so the node aligns to
remove a build chain).

## Open questions

1. **Conversation agent registration from outside `custom_components/`** —
   research needed before committing to add-on-only.
2. ~~**agent-bridge connectivity from the node**~~ **Resolved (P1.2,
   2026-06-05): gateway brokers.** Node speaks only the gateway WS
   protocol; emits `node.propose` requests, gateway translates to
   agent-bridge MCP calls and relays the result. See
   `docs/RESEARCH-AGENT-BRIDGE-CONNECTIVITY.md`.
3. ~~**MCP server retirement**~~ **Resolved (P1.3, 2026-06-05):**
   retire `homeassistant` + `homeassistant-readonly` MCP servers
   *only after the node has proven it can handle every call surface
   they currently serve*, across every agent that uses them (main
   session, ReefMaster, PoolMaster, HomeOps, heartbeats). No
   calendar-based default. Concrete trigger: zero unhandled
   `mcp__homeassistant*` tool calls in the gateway logs for 7
   consecutive days *and* a written inventory in
   `docs/RESEARCH-MIGRATION.md` confirming coverage. Cutover is a
   single PR that drops the MCP servers from gateway config and
   updates any agent prompts that referenced them by name.
   Migration scope tracked separately under P1.3.
4. ~~**Update channel**~~ **Resolved (P1.4, 2026-06-05):**
   date-based versioning, `YYYY.M.PATCH` (e.g. `2026.6.0`). The
   leading two components track the HA release this node was tested
   against, so users can read add-on/HA compatibility at a glance.
   Patch increments for fixes within a HA release. The breaking-change
   discipline in §2c is the same discipline we want at the version
   bump — both happen on HA's cadence.

## Phases

- **P0 — Plan** *(this doc; in progress)*
- **P1 — Research** — answer the 4 open questions, especially
  conversation agent registration.
- **P2 — Skeleton** — `addon/` Dockerfile + `config.yaml`, `node/`
  entrypoint that pairs with the gateway and answers `ping`.
- **P3 — Filesystem + shell surface** — read paths first, then
  proposal-gated writes.
- **P4 — HA control surface** — port the MCP server commands.
- **P5 — Assist agent** — Plan A or Plan B based on P1 outcome.
- **P6 — Retire MCP servers** for this HA after validation window.
- **P7 — Publish add-on repo** + docs.
