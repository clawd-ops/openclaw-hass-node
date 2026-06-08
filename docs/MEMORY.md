# MEMORY — build context for the Clawd agent

> **This is not the user-facing README.** Users want
> [`INSTALL.md`](INSTALL.md). This file is the durable build memory the
> Clawd agent reads on resume — current state of the architecture, what
> the moving parts are, and where to find the rest. The user-visible
> README at the repo root delegates installs to `INSTALL.md`.

# openclaw-hass-node

Home Assistant integration that connects HA to an OpenClaw gateway as a
node: filesystem + shell on the HA host, the full `ha.*` control
surface, and an Assist conversation entity that routes Assist turns to
whichever agent OpenClaw has configured for the session (Clawd).

```
HA Assist UI
    │ user turn
    ▼
custom_components/openclaw_gateway/   ← HACS shim (ConversationEntity)
    │ POST /v1/conversation
    ▼
node/  (OpenClaw add-on (app))
    │ chat.send + sessions.messages.subscribe
    ▼
OpenClaw gateway (existing)
    │ routes the message to the configured agent
    ▼
Agent uses ha.* tools via node.invoke ↔ node command surface
    ▼
Reply on the session → node subscription → /v1/conversation → HA Assist speech
```

No bespoke gateway server, no parallel brain. The node is a standard
OpenClaw node speaking the existing Gateway Protocol.

## Status

**Full pipeline wired (2026-06-08).** P5.12 ChatRelay merged (PR #72,
Codex v1→v6). HA Assist turns relay through the node into OpenClaw
agent sessions via `chat.send` + `sessions.messages.subscribe`. HACS
shim timeout aligned to 35s (30s relay + 5s slack). Next: Rob's E2E
validation, then polish and P6.2 MCP cutover. See `docs/STATUS.md`.

### P5.12 design decisions (documented in `chat_relay.py` docstring)

1. Fresh session per `conversation_id`
2. Default agent (gateway-routed)
3. `chat.send` for full agent pipeline
4. 30s single monotonic deadline per turn
5. Dual event family handling (`session.message` + `chat*`)
6. Content-block array extraction (`[{"type":"text","text":"..."}]`)
7. Per-session `asyncio.Lock` for concurrency
8. `runId`-based event filtering (stale events rejected)
9. Events only captured when a turn is actively waiting

### Security fix in PR #71

`ha_client._ha_url()` now hard-pins to `http://supervisor/core` when
`SUPERVISOR_TOKEN` is present. Previously, a user-supplied `HASS_URL`
would receive the privileged Supervisor token.

### Scopes

Node requests `["operator.read", "operator.write"]` on connect for
session/chat RPCs. Whether the gateway grants `operator.write` to a
node-role connection needs E2E validation.

## Repo layout

- **`node/`** — Python add-on (app). Pairs with the gateway via the
  Gateway Protocol, exposes `fs.*` / `system.*` / `ha.*` (13 tools)
  through `node.invoke`, and runs a local HTTP API on port 8099 for
  health checks + Assist turn relay.
- **`custom_components/openclaw_gateway/`** — HACS shim. ~150 LOC
  `ConversationEntity` that POSTs Assist turns to the node's
  `/v1/conversation`. Required because HA's conversation-agent
  registration is in-process Python only.
- **`addon/`** — Home Assistant add-on (app) packaging (Dockerfile + config).
- **`docs/`** — durable plan, status, command surface, decisions, and
  the architecture post-mortem in `RESEARCH-OPENCLAW-INTEGRATION.md`.

## Install

1. Install the **add-on (app)** from this repo's add-on (app) repo URL. Start it.
2. Approve the node on the gateway: `openclaw devices approve <id>`.
3. Install the **HACS shim** from this repo, point its config flow at
   the add-on (app)'s local socket URL.
4. In HA → Settings → Voice → pick **OpenClaw Gateway** as the
   conversation agent.

## Architecture decisions

Full detail in `docs/PLAN.md`. Headline rules:

- **Conversation registration**: HA exposes no out-of-process hook, so
  the HACS shim is required (Plan B in
  `docs/RESEARCH-CONVERSATION-AGENT.md`).
- **Node as conversation relay**: the node calls `chat.send` /
  `sessions.messages.subscribe` over its existing gateway WS connection
  to relay HA Assist turns. No parallel gateway, no new protocol
  primitives. See `docs/RESEARCH-OPENCLAW-INTEGRATION.md` (and the
  post-mortem section for why earlier iterations got this wrong).
- **`/config` is proposal-gated** through agent-bridge. Reads and shell
  are direct.
- **One node per HA instance.**
- **Brain is the OpenClaw-configured agent** (Opus 4.7 or GPT-5.5);
  subagents it spawns are unpinned and prefer cheaper models. The node
  carries zero model knowledge.

## Source of truth across compactions

`docs/PLAN.md` and `docs/STATUS.md` are durable. When resuming after a
context compaction, **read those two first**. They describe the goal,
architecture, what's done, what's next, and open questions. Update
`STATUS.md` whenever a milestone moves.

## Development

`uv sync --all-extras` installs the workspace.

CI gates (six, see `.github/workflows/ci.yaml`):

- `uv run ruff check node/src node/tests`
- `uv run ruff format --check node/src node/tests`
- `uv run mypy --strict node/src node/tests`
- `uv run pytest node/tests/` (≥ 95% branch coverage)
- `uv run bandit -ll -r node/src`
- `uv run pip-audit`

## Rules

- Mutation surface (`fs.write`, `fs.patch`, `ha.config.*`) is
  proposal-gated through agent-bridge. No direct writes to `/config`.
- Add-on (App) first; HACS shim only for the conversation entity HA cannot
  register out-of-process.
- One node per HA instance.
- Docs in `docs/` are the source of truth across context compactions.
