# openclaw-hass-node

Three-piece Home Assistant integration that gives a Claude-powered brain
direct access to HA: filesystem + shell, HA control surface, and a
conversation entity that turns Assist questions into agentic answers.

```
HA Assist UI
    │ user turn
    ▼
custom_components/openclaw_gateway/   ← HACS shim (ConversationEntity)
    │ POST /v1/conversation
    ▼
node/                                  ← OpenClaw node (add-on)
    │ node.conversation.request  (WS, Ed25519-authed)
    ▼
gateway/                               ← OpenClaw gateway brain
    │ Claude Opus 4.7 + ha.* tools
    │ ↕ node.invoke.request / result   (ha.list_states, ha.light_turn_on, …)
    ▼
response → node.conversation.result → speech
```

## Status

Phase **P5 — Assist agent** complete; trial-mode E2E Assist runs end-to-end.

See `docs/STATUS.md` for the live state and `docs/PLAN.md` for the full
architecture.

## Repo layout

- **`node/`** — the OpenClaw node. Pairs with the gateway, runs the
  add-on, exposes the `fs.*`, `system.*`, and `ha.*` command surface.
  Local HTTP API on port 8099 services Assist turns and health checks.
- **`gateway/`** — a **standalone reference brain**. WS server that
  accepts node connections, runs `Brain.handle_turn` (Claude Opus 4.7
  *or* GPT-5.5; provider is config-driven) with the `ha.*` catalog as
  tools, and routes tool calls back to the node.

  This is the **trial / reference** gateway for users who don't run
  OpenClaw. In the maintainer's deployment, an OpenClaw plugin will
  eventually handle `node.conversation.request` instead — that
  integration lives in a follow-up phase (P5.10). Both paths use the
  same node-side protocol, so the node and shim never change.
- **`custom_components/openclaw_gateway/`** — the HACS shim. A
  ~150 LOC `ConversationEntity` that forwards Assist turns to the
  node's local HTTP API.
- **`addon/`** — Home Assistant add-on packaging (Dockerfile + config).
- **`docs/`** — durable plan, status, command surface, decisions.

## Run the gateway

```bash
export ANTHROPIC_API_KEY=sk-…
export OPENCLAW_GATEWAY_AUTO_APPROVE=true       # trial mode; default is operator approval
python -m openclaw_gateway
```

Env vars (see `gateway/src/openclaw_gateway/config.py`):

| Var                              | Default                                  |
| -------------------------------- | ---------------------------------------- |
| `OPENCLAW_GATEWAY_HOST`          | `0.0.0.0`                                |
| `OPENCLAW_GATEWAY_PORT`          | `8765`                                   |
| `OPENCLAW_GATEWAY_PROVIDER`      | `anthropic` (or `openai`)                |
| `OPENCLAW_GATEWAY_MODEL`         | `claude-opus-4-7` / `gpt-5.5`            |
| `OPENCLAW_GATEWAY_SYSTEM_PROMPT` | (empty)                                  |
| `OPENCLAW_GATEWAY_AUTO_APPROVE`  | `false`                                  |
| `OPENCLAW_GATEWAY_DATA_DIR`      | `~/.openclaw/hass-gateway`               |
| `ANTHROPIC_API_KEY`              | required when provider=anthropic         |
| `OPENAI_API_KEY`                 | required when provider=openai            |

Device registry persists to `$DATA_DIR/devices.json` so pairings survive
restarts.

## Install the shim

Once the node add-on is running, install the HACS shim from this repo,
configure the add-on's local socket URL in the shim's config flow, then
pick **OpenClaw Gateway** as the conversation agent in HA's voice settings.

## Architecture decisions

Captured in `docs/PLAN.md` and `docs/RESEARCH-CONVERSATION-AGENT.md`:

- HA exposes no out-of-process conversation-agent registration hook;
  Plan B (HACS shim) is the only path. See § RESEARCH-CONVERSATION-AGENT.
- Brain runs in the gateway on **Opus 4.7 or GPT-5.5**. Subagents the
  brain spawns are unpinned and pick whichever cheaper model fits per
  task. The node carries no model knowledge.
- Every `/config` mutation goes through agent-bridge proposals; reads
  and shell are direct.
- One node per HA instance.

## Source of truth across compactions

The docs in `docs/` are durable. When resuming work after a context
compaction, **start by reading `docs/PLAN.md` and `docs/STATUS.md`**;
those two files describe the goal, architecture, what's done, what's
next, and open questions. Update `STATUS.md` whenever a milestone moves.

## Development

`uv sync --all-extras` installs both workspace members.

CI gates (all six, see `.github/workflows/ci.yaml`):

- `uv run ruff check node/src node/tests gateway/src gateway/tests`
- `uv run ruff format --check …`
- `uv run mypy --strict …`
- `uv run pytest node/tests/ gateway/tests/` (≥ 95% branch coverage)
- `uv run bandit -ll -r node/src gateway/src`
- `uv run pip-audit`

## Rules

- Mutation surface (`fs.write`, `fs.patch`, `ha.config.*`) is
  proposal-gated through agent-bridge. No direct writes to `/config`.
- Add-on first; HACS shim only for the conversation entity HA cannot
  register out-of-process.
- One node per HA instance.
- Docs in `docs/` are the source of truth across context compactions.
