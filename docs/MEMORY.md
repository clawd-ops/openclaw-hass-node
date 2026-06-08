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
    │ ChatRelay: chat.send + sessions.messages.subscribe (operator WS)
    │ node-invoke surface (node WS)
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

**Tool surface live; Assist relay broken (2026-06-08 PM).** Invokes
work end-to-end. The P5.12 ChatRelay (PR #72) shipped but the first
real Assist turn returned `INVALID_REQUEST unauthorized role: node`
(#82) because the gateway's role policy is binary and `chat.send` is
operator-scope. Tracking the dual-connection refactor under **P5.13**
(#84). See `docs/STATUS.md`.

### P5.12 design decisions (still valid for the relay code itself)

The session/event/concurrency machinery doesn't change in P5.13 —
only the transport. Documented in `chat_relay.py` docstring:

1. Fresh session per `conversation_id`
2. Default agent (gateway-routed)
3. `chat.send` for full agent pipeline (will route through operator WS in P5.13)
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

### Roles / scopes (corrected 2026-06-08)

The gateway's role policy is **binary per-method**: node-role can only
call node-scope methods; operator-role can only call operator-scope
methods. `chat.send` and `sessions.messages.subscribe` are
`operator.write`, so they require an operator-role connection.
There is no `node.chat.send`. P5.13 fixes this by running **two
parallel WS connections** (one as `node` for invokes, one as
`operator` for ChatRelay). Device is paired as dual-role via
`PAIRING_SETUP_BOOTSTRAP_PROFILE` (`openclaw qr` flow).

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
- **Node as conversation relay (dual-WS)**: the node calls `chat.send`
  / `sessions.messages.subscribe` over a **second** gateway WS
  connection opened as `role: operator`. The existing `role: node`
  connection keeps serving `node.invoke.*`. No parallel gateway, no
  new protocol primitives — only a second connection with a different
  role. P5.12 attempted the single-connection variant and was
  disproved by the gateway's binary role policy (#82). See
  `docs/RESEARCH-OPENCLAW-INTEGRATION.md` for the brain-vs-relay
  post-mortem; the P5.12 role-policy post-mortem is in
  `docs/PLAN.md` § Assist conversation agent.
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
