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
- Commands: `fs.read`, `fs.list`, `fs.stat`, `fs.glob`, `system.run`,
  `system.which`. Writes (`fs.write`, `fs.move`, `fs.delete`,
  `fs.patch`) are **proposal-gated** — they accept the args but emit an
  agent-bridge `propose_edit` and wait for resolve.
- `system.run` requires `operator.admin` scope on pairing approval.
- Supervisor API exposed via `ha.supervisor.*` commands wrapping
  `http://supervisor/...` with `SUPERVISOR_TOKEN`.

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

### 3. Assist conversation agent

- **Open question:** can a HA conversation agent be registered from
  outside `custom_components/`? The conversation agent interface
  (`conversation.AbstractConversationAgent`) is a Python class that has
  to be registered against the HA conversation integration. There is no
  documented external-process registration path as of this writing.
- **Plan A (preferred):** add-on exposes a small Supervisor service or
  uses the HA WS API to inject conversation turns. If a way exists to
  forward Assist → external WS, we use it. Investigate first.
- **Plan B (fallback):** ship a tiny HACS-or-manual companion
  integration whose **only** job is to register a conversation agent
  class that forwards turns to the add-on's local socket (e.g.
  `http://homeassistant.local:<port>/assist`). The add-on remains the
  workhorse; the HACS shim is a thin adapter.
- We commit to Plan B only after confirming Plan A is impossible.

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

## Open questions

1. **Conversation agent registration from outside `custom_components/`** —
   research needed before committing to add-on-only.
2. **agent-bridge connectivity from the node** — does the node connect
   to agent-bridge directly, or does the gateway broker proposals on
   the node's behalf? Leaning: gateway brokers, so node only speaks the
   gateway protocol.
3. **MCP server retirement** — phase out the existing `homeassistant`
   MCP servers only after the node has been stable for ~a week. Keep
   both running in parallel during validation.
4. **Update channel** — HA add-on store auto-updates from the published
   image tag. Pick a versioning scheme (semver, date-based?).

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
