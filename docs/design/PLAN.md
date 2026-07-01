# openclaw-hass-node — Plan

> **Resuming after a compaction?** Read this file end-to-end, then
> `STATUS.md`. These two files are the source of truth — your in-context
> memory is not. Update `STATUS.md` whenever you change phase or finish a
> milestone. If something here is wrong, fix the doc first, then the code.

## Goal

Build a single OpenClaw node that runs on a Home Assistant host as a
Supervisor add-on. (A standalone Docker run mode is a design goal but
not shipped during beta — current install/packaging assume the
Supervisor add-on path.) The node gives the gateway three capability
surfaces in one process:

1. **Filesystem + shell** on the HA host (`/config`, `/share`,
   `/media`; Supervisor API for addon lifecycle).
2. **HA control** — states, services, areas, devices, registry,
   automations, traces, logbook. Replaces the existing `homeassistant` +
   `homeassistant-readonly` MCP servers for this HA.
3. **Assist conversation agent** — registers this gateway as a HA
   conversation agent so Assist turns go to Clawd. Replaces the Anthropic
   conversation integration.

The node speaks the standard OpenClaw gateway WS protocol over **two
parallel connections**: `role: node` (for the node-invoke surface) and
`role: operator` (for ChatRelay's `chat.send` / `sessions.messages.*`).
The device is paired as dual-role `[node, operator]` via the QR /
bootstrap-token flow (same `PAIRING_SETUP_BOOTSTRAP_PROFILE` mobile
clients use). The single-role `role: node` approach was attempted in
P5.12 and disproved by the gateway's binary role policy — see #82.
Refactor tracked under **P5.13** / #84.

## Non-goals

- Multi-HA from one node. One node per HA instance.
- Replacing the gateway/model. The node is a peripheral, not a brain.
- Direct writes to `/config`. All mutations go through agent-bridge.

## Architecture

```
+------------------+   WS #1 role: node (invoke surface)  +-----------------+
|  OpenClaw GW     | <----------------------------------> |  HASS Node      |
|  (Clawd model)   |   WS #2 role: operator (ChatRelay)   |  (this repo)    |
|                  | <----------------------------------> |                 |
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

Inside the add-on (app) container we mount HA volumes per the HA add-on (app) spec.
The node process is the only thing in the container. The Supervisor API
token (`SUPERVISOR_TOKEN`) is available when running as an add-on (app); when
running standalone, `HASS_URL` + `HASS_TOKEN` env vars are used instead.

## Surfaces in detail

### 1. Filesystem + shell

- Mounted paths (add-on (app) `config.yaml` map): `config:rw`, `share:rw`,
  `media:rw`. (`addons`, `ssl`, and `backup` are intentionally not
  mapped — Supervisor surfaces those via the Supervisor API rather
  than direct mounts.)
- Bind mounts placed under an allowed root are treated as operator-trusted
  configuration; the read-only command layer does not try to distinguish or
  defeat them.
- Commands: `fs.read`, `fs.list`, `fs.stat`, `fs.glob`, `system.run`,
  `system.which`. Writes (`fs.write`, `fs.move`, `fs.delete`,
  `fs.patch`) are **proposal-gated**: today the handlers return
  `PROPOSAL_REQUIRED` for protected roots / when `agent_bridge=true`.
  Wiring the actual `propose_edit` → `resolve_proposal` round-trip
  through the agent-bridge UI is the next major milestone (see
  `docs/STATUS.md` "Next concrete steps").
- `fs.delete` uses `send2trash` (FreeDesktop.org spec) with an
  OpenClaw-managed trash directory fallback, never `rm`. `fs.restore`
  recovers from trash. No sidecar `.bak` files anywhere.
- `system.run` gated by `OPENCLAW_ADMIN_TOKEN` env var; caller must
  pass matching `admin_token` param.
- Supervisor API access uses `SUPERVISOR_TOKEN` against
  `http://supervisor/...`. Today this is exposed through the
  `ha.addon_*` Tier A/B command surface (`ha.list_addons`,
  `ha.addon_info`, `ha.addon_stats`, `ha.addon_logs`,
  `ha.addon_changelog`, `ha.addon_documentation`,
  `ha.addon_start`/`stop`/`restart`). A generic `ha.supervisor.*`
  command family is not registered; broader Supervisor surfaces
  (snapshots, host, network) remain out-of-scope until proposal-gated
  write semantics land.

### 1b. Backup / undo model

Per-file content-addressed versioning under `/share/openclaw-backups/`
(outside `/config`, survives addon rebuilds, included in HA backups).
Every applied proposal that mutates a file under a protected root
captures the prior bytes; `fs.restore`/`fs.history`/`fs.diff` surface
the versions. No git in `/config`, no per-change Supervisor snapshots,
no `.bak` sidecars.

Full design (storage layout, retention, edge cases):
[`../reference/BACKUPS.md`](../reference/BACKUPS.md).

### 2. HA control

- Commands mirror what the existing MCP servers offer:
  `ha.list_states`, `ha.get_state`, `ha.list_services`,
  `ha.call_service`, `ha.list_areas`, `ha.list_devices`,
  `ha.list_entity_registry`, plus convenience wrappers
  (`ha.light_turn_on/off`).
- Transport: HA WebSocket API for state/event streams; REST for
  one-shots. Token comes from `SUPERVISOR_TOKEN` (add-on (app)) or
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

See `docs/reference/HA-CONFIG-EDITING.md` for the per-domain API map.

See `docs/reference/HA-CONFIG-EDITING.md` for per-domain detail.

### 2c. Always rooted in installed HA version + breaking-change verification

> **Status: deferred.** None of the pieces below are implemented. The
> mechanism is gated on the proposal-gated write path actually
> round-tripping through agent-bridge (TODO #20) — until writes land
> there's no place for pre-change verification to fire. Tracked as
> TODO #23. Kept here as the design contract for when #20 unblocks
> it.

- Node detects HA core version on connect (Supervisor `/info` or
  `/api/config`), emits it as pairing metadata so gateway model
  always knows the live version.
- New `docs.lookup(topic, version=current)` command that fetches from
  the `home-assistant/home-assistant.io` repo at the tag matching the
  running core version, with local cache.
- New `docs.breaking_changes(version=current, since=<prev>?, domain=?)`
  command. Pulls the relevant release notes' breaking-changes section
  from the docs repo. Used by the rule below.

**Mandatory pre-change verification (HARD rule, deferred with §2c):**

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
(see `docs/CONTRIBUTING.md`) re-runs `docs.breaking_changes` against the diff
and blocks merge if the generator missed a relevant breaking change
or fix.

### 3. Assist conversation agent

**Architecture (corrected 2026-06-08 — P5.13):** the HA node is a
**standard OpenClaw node** that relays Assist turns into an OpenClaw
agent session using the *existing* gateway chat surface. There is no
parallel brain, no custom event types, no plugin code. Clawd (the
agent) is the brain. The node maintains **two** parallel WS
connections to the gateway — one as `role: node` for tool invokes,
one as `role: operator` for ChatRelay's chat RPCs — because the
gateway's role policy is binary per-method and `chat.send` is an
operator-scope method (see P5.12 post-mortem below).

End-to-end flow:

```
HA Assist → ConversationEntity integration → node /v1/conversation
         → node ChatRelay calls `chat.send` on its OPERATOR WS
         → OpenClaw routes the message to the configured agent (Clawd)
         → agent calls ha.* tools via node.invoke on the NODE WS (P4)
         → agent reply arrives on the session
         → node receives it via sessions.messages.subscribe (operator WS)
         → /v1/conversation returns the reply text
         → integration surfaces it as Assist speech
```

Three pieces, only one of which is bespoke:

1. **HACS integration** (`custom_components/openclaw_hass_node_assist/`, ~150 LOC).
   `ConversationEntity` subclass whose `async_process` POSTs to the
   add-on (app)'s local HTTP endpoint. Distributed via HACS. Required by HA
   core because conversation-agent registration is in-process Python
   only (see `docs/research/CONVERSATION-AGENT.md`).
2. **Node** (this repo). Pairs with the gateway as dual-role `[node,
   operator]` via the QR / bootstrap-token flow (same
   `PAIRING_SETUP_BOOTSTRAP_PROFILE` mobile clients use). Opens two
   gateway WS connections with independent reconnect loops. The
   `ChatRelay` owns `chat.send` and `sessions.messages.subscribe` on
   the operator socket; the existing node-invoke dispatcher stays on
   the node socket. Keyed by HA's `conversation_id` so multi-turn
   threads correctly.
3. **OpenClaw** (no changes). The relay uses primitives the Gateway
   Protocol already ships: `chat.send`, `sessions.messages.subscribe`,
   `node.invoke`. Pair the node as dual-role, approve it, point an
   agent at the session, done.

**P5.12 post-mortem (2026-06-08, #82):** P5.12 was built calling
`chat.send` from the single `role: node` connection. The gateway's
role check is `isCoreNodeGatewayMethod(method) ? role === 'node' :
role === 'operator'`, and `chat.send` is scope `operator.write`. A
node-role connection can never call it. The phone client appeared to
"just work" — in reality it pairs dual-role and connects as operator
for chat. The fix is the dual-WS refactor under P5.13 / #84; the
existing ChatRelay code (concurrency, content-block extraction, runId
filter, deadline) is sound and gets reused, only the transport
changes.

**Why this is right (and the earlier "build a brain" path was wrong):**
the OpenClaw gateway already owns model routing, agent orchestration,
tool dispatch, and conversation state. A node that originates a turn
just needs the chat scope on its connect frame and the two RPC calls
above. Earlier iterations built a parallel Python gateway with its own
brain, providers, and invented `node.conversation.*` events; all of
that was deleted in P5.11 (see `docs/research/OPENCLAW-INTEGRATION.md`
for the post-mortem and the P5.12 implementation plan).

**Routing model (2026-06-06):** the agent the node routes turns to is
on a premium tier (Opus 4.7 or GPT-5.5). Subagents the agent spawns
for work are unpinned — picked per task by whichever cheaper model
fits. The node carries no model knowledge.

## Mutation control (agent-bridge gated)

> **Status: partially shipped.** Today the write handlers
> (`fs_write.py`, `fs_patch.py`, `fs_move_delete.py`) return
> `PROPOSAL_REQUIRED` for protected roots or when `agent_bridge=true`.
> They do **not** yet emit `propose_edit` or wait for
> `resolve_proposal` — that round-trip is blocked pending the
> gateway/agent-bridge proposal bridge, which is the next major
> milestone (see `docs/STATUS.md` "Next concrete steps"). The model
> below is the target end-state, not the shipped behaviour.

- Every write-shaped command on the node has two outcomes (target):
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
  `/data/openclaw/node-key` (HA add-on (app) `/data` is per-add-on (app) durable).

## Packaging

- Single Docker image. Today only the HA add-on run mode is shipped:
  - **HA add-on (app)**: `config.yaml` declares slug, mapped volumes,
    `hassio_api: true`, `hassio_role: manager`, `homeassistant_api: true`.
    Built per HA arch matrix (`amd64`, `aarch64`, `armv7`).
  - **Standalone Docker** (planned, not in beta): would `docker run`
    with explicit volume mounts and `HASS_URL` + `HASS_TOKEN` env;
    the entrypoint already branches on `SUPERVISOR_TOKEN`. Tracked as
    a future packaging item — install/CHANGELOG do not advertise it.
- Repo published as a HA add-on (app) repository (`repository.yaml`) so users
  can add the URL in HA → Add-on (App) Store → Repositories.

See `docs/design/PLAN.md` for the full layout.

## Process + quality

Process (Conventional Commits, cross-provider review) lives in
[`../CONTRIBUTING.md`](../CONTRIBUTING.md). CI gates (mypy strict,
ruff, pytest coverage ≥95%, bandit, pip-audit, addon smoke build)
live in [`../operations/QUALITY.md`](../operations/QUALITY.md). Both
the node and the HACS integration are Python 3.13+ (HA core requires Python
for the integration; the node aligns to remove a build chain).

## Resolved design questions (historical)

The four early design questions that shaped this plan are documented
in the research folder; their resolutions are baked into the
architecture above:

- Conversation-agent registration → HACS integration required ([`../research/CONVERSATION-AGENT.md`](../research/CONVERSATION-AGENT.md)).
- agent-bridge connectivity → gateway brokers ([`../research/AGENT-BRIDGE-CONNECTIVITY.md`](../research/AGENT-BRIDGE-CONNECTIVITY.md)).
- MCP retirement criteria → 7 days of zero unhandled `mcp__homeassistant*` + written inventory ([`../research/MIGRATION.md`](../research/MIGRATION.md)).
- Versioning → date-based `YYYY.M.PATCH` (now expressed as `2026.6.20bN` pre-1.0).

Current open work is in [`../TODO.md`](../TODO.md); current shipped state is in [`../STATUS.md`](../STATUS.md). This file is the *why*, not the *what's-shipped-now*.

---

## Packaging & repo layout

> Folded in from the former `docs/PACKAGING.md` during the Phase 2 doc
> reshape. Version policy and the five-source bump flow live in
> [`../operations/RELEASE.md`](../operations/RELEASE.md).

### Language

Python 3.13+ for both packages (node + HACS integration). See
[`../CONTRIBUTING.md`](../CONTRIBUTING.md) for the CI gates (ruff check
+ format, mypy strict, pytest with branch coverage).

### Repo layout

```
openclaw-hass-node/
├── README.md
├── repository.yaml                  # HA add-on store descriptor
├── hacs.json                        # HACS descriptor for the integration
├── docs/
│   ├── README.md                    # docs site landing page
│   ├── INSTALL.md / STATUS.md / TODO.md / CONTRIBUTING.md / MEMORY.md
│   ├── design/                      # PLAN, IDENTITY-AND-SCOPES, COMMAND-TIERS
│   ├── reference/                   # COMMAND-SURFACE, HA-CONFIG-EDITING, BACKUPS
│   ├── operations/                  # RELEASE, QUALITY, UAT-PLAN, LESSONS
│   └── research/                    # historical design rationale
├── app/                           # Build context for Supervisor
│   ├── config.yaml                  # HA add-on manifest
│   ├── Dockerfile                   # HA per-arch Python base
│   ├── build.yaml                   # Per-arch BUILD_FROM + labels
│   ├── run.sh                       # Entrypoint (exports env, runs node)
│   ├── icon.png / logo.png
│   └── node/                        # OpenClaw node (Python package)
│       ├── pyproject.toml
│       ├── src/openclaw_node/
│       │   ├── __init__.py          # Version + importlib.metadata
│       │   ├── __main__.py          # Detects add-on vs standalone
│       │   ├── config.py            # Env-driven configuration
│       │   ├── authz.py             # HA actor role/disclaimer + agent routing
│       │   ├── identity.py          # Ed25519 device identity
│       │   ├── gateway_ws.py        # Gateway WS client (role: node)
│       │   ├── ha_client.py         # HA REST + WS client
│       │   ├── http_api.py          # Local HTTP API (bearer-gated)
│       │   ├── safe_fd.py           # TOCTOU-safe fd primitives
│       │   ├── backup_store.py      # Content-addressed backup store
│       │   └── commands/            # Command registry + handlers
│       └── tests/
└── custom_components/
    └── openclaw_hass_node_assist/            # HACS integration (conversation integration)
        ├── __init__.py
        ├── manifest.json
        ├── config_flow.py
        ├── conversation.py
        ├── const.py
        └── strings.json
```

### Add-on `config.yaml`

Canonical source: `app/config.yaml`. Current shipped shape:

```yaml
name: OpenClaw Node
version: "2026.6.20b7"
slug: openclaw_hass_node
arch: [amd64, aarch64, armv7]
init: false
# Least-privilege API surface. The local HTTP API authenticates with
# `local_api_token` directly via hmac.compare_digest, not HA-issued tokens.
homeassistant_api: true
hassio_api: true
hassio_role: manager
# `hassio_role: manager` is required for the read-only Tier A addon command
# surface. Do not add lifecycle mutation commands without a separate admin
# gate. `auth_api` remains omitted.
map:
  - config:rw    # fs.* mutations gated by software _is_protected("/config")
                 # → PROPOSAL_REQUIRED before any write/rename/unlink
  - share:rw    # backups + delete-trash store
  - media:rw    # generic fs.* write root
# `ssl:ro`, `addons:ro`, `backup:ro` were removed; no shipped feature
# consumes them and they leak sensitive material via the generic fs.read
# surface.
options:
  gateway_url: "wss://gateway.example.com/ws"
  pairing_token: ""
  node_name: ""
  local_api_token: ""    # shared bearer; also root for HA actor-signing subkey
  hass_url: ""
  hass_token: ""
ingress: false
```

No host port mapping. The local HTTP API is only reachable inside the
Supervisor add-on network by default. The local API is fail-closed:
non-public paths require `Authorization: Bearer <local_api_token>`.
When the option is unset, those paths return `401 NO_TOKEN_CONFIGURED`.
Only `/health`, `/v1/health`, and `/v1/conversation/info` are reachable
without a token (HA add-on probe + HACS integration config-flow discovery).
Health responses redact identity details to counts/booleans so public
probes cannot enumerate HA user UUIDs, agent mappings, lifecycle
allowlists, or forbidden-command contents.

### Docker base image

The Dockerfile uses Home Assistant's per-arch Python base images
(e.g. `ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.20`),
set via `BUILD_FROM` in `build.yaml`. Supervisor requires these HA
base images; a bare `python:3.13-alpine` is silently ignored by
Supervisor and causes a `pip: not found` failure.

The HA base image is also required by `app/run.sh`, which uses
`#!/usr/bin/with-contenv sh` to pick up Supervisor's injected env
(notably `SUPERVISOR_TOKEN`). Bare Python images do not ship
s6-overlay / `with-contenv` and the addon will not start.

### Standalone Docker (not supported during beta)

Running the Docker image outside HA Supervisor is **not a supported
install path** during the pre-1.0 beta, for the with-contenv reason
above. Standalone-mode detection in `__main__.py` is kept so the node
can run directly on the dev host (`python -m openclaw_node` with
`HASS_URL` + `HASS_TOKEN`), but the image itself is HA-only.

Entrypoint detects mode for the standalone dev-host path:
- `SUPERVISOR_TOKEN` present -> add-on mode, talks to
  `http://supervisor/` and `http://homeassistant/`.
- Else -> standalone, uses `HASS_URL` + `HASS_TOKEN`.
