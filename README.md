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
node/  (OpenClaw add-on)
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

Node + shim install-ready. **P5.12** (the small Python class that
calls `chat.send` and listens for replies) is the only piece between
the current placeholder behaviour and full E2E Assist. See
`docs/STATUS.md`.

## Repo layout

- **`node/`** — Python add-on. Pairs with the gateway via the
  Gateway Protocol, exposes `fs.*` / `system.*` / `ha.*` (13 tools)
  through `node.invoke`, and runs a local HTTP API on port 8099 for
  health checks + Assist turn relay.
- **`custom_components/openclaw_gateway/`** — HACS shim. ~150 LOC
  `ConversationEntity` that POSTs Assist turns to the node's
  `/v1/conversation`. Required because HA's conversation-agent
  registration is in-process Python only.
- **`addon/`** — Home Assistant add-on packaging (Dockerfile + config).
- **`docs/`** — durable plan, status, command surface, decisions, and
  the architecture post-mortem in `RESEARCH-OPENCLAW-INTEGRATION.md`.

## Install

1. Install the **add-on** from this repo's add-on repo URL. Start it.
2. Approve the node on the gateway: `openclaw devices approve <id>`.
3. Install the **HACS shim** from this repo, point its config flow at
   the add-on's local socket URL.
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
- Add-on first; HACS shim only for the conversation entity HA cannot
  register out-of-process.
- One node per HA instance.
- Docs in `docs/` are the source of truth across context compactions.
