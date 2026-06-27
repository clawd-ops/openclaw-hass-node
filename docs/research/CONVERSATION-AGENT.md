# Research: Registering an HA Assist Conversation Agent from an Add-on (App)

> **Historical snapshot.** The `node.conversation.request/result`
> event types proposed in this document were deleted from the design;
> the correct relay architecture uses `chat.send` +
> `sessions.messages.subscribe` over the existing gateway WS. See
> `docs/research/OPENCLAW-INTEGRATION.md` for the current design. The
> Plan A / Plan B verdict below (HACS shim required) still stands.

**Question.** Can a Home Assistant Assist "conversation agent" be registered from an HA add-on (app) (or via Supervisor / WS / REST API) WITHOUT shipping a companion `custom_components/` Python integration?

**Verdict: Plan A (add-on (app) alone) is NOT viable as of HA core ~2026.6. Plan B (thin `custom_components/` shim that forwards to the add-on (app) socket) is the only realistic option.**

## Why

### 1. The `conversation` integration registers agents via in-process Python only

The modern path is `ConversationEntity` (subclass of `RestoreEntity`) plus the legacy `AbstractConversationAgent`. Registration is a Python callback inside the HA process:

```python
@callback
def async_set_agent(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    agent: AbstractConversationAgent,
) -> None: ...
```

This requires (a) a live `HomeAssistant` object, (b) a `ConfigEntry` produced by an HA config flow, and (c) an `AbstractConversationAgent` subclass — all of which only exist inside the HA Python process. There is no equivalent of `async_set_agent` exposed over the wire. [1][2][6]

`ConversationEntity` is the preferred modern form; the entity is added through a normal `async_add_entities` call from an integration's `__init__.py` / `conversation.py`. Same in-process requirement. [2]

### 2. The WS and REST APIs are input-only, not registration

The `conversation` integration exposes `conversation/process` (WS) and `POST /api/conversation/process` (REST) for *sending* a turn to an already-registered agent. There is no `conversation/register`, `conversation/agent/create`, or equivalent command. The WS surface is documented as: process a sentence, list agents, list sentences/intents — none let an external process declare itself as a new agent. [3][7]

The `agent_id` parameter on `conversation/process` selects an *existing* agent (by entity_id or legacy engine id); it cannot conjure one. [3]

### 3. Supervisor gives add-ons (apps) no privileged registration path

`homeassistant_api: true` in `config.yaml` just hands the add-on (app) a `SUPERVISOR_TOKEN` that proxies to the normal Core REST/WS API at `http://supervisor/core/api/`. That is the *same* surface as a user — no special "register a conversation agent" route is exposed. The Supervisor API itself has no conversation endpoints. [4]

### 4. `assist_pipeline` selects agents by entity_id; it cannot mint new engines

Pipelines reference a `conversation_engine` that must already resolve to a registered `ConversationEntity` (or legacy agent keyed by config_entry.entry_id). The pipeline integration does not provide a registration hook. The list comes from `agent_manager` populated by `async_set_agent` / entity platform. [1][2]

### 5. 100 % of precedent ships as `custom_components/`

Every conversation-agent project surveyed — `openai_conversation` and `anthropic` (core), `ollama` (core), `extended_openai_conversation`, `hasscc/ai-conversation`, `grok_conversation`, `home-llm`, `home-generative-agent`, `custom-conversation`, `hass_llm_assist` — installs as a Python integration into `custom_components/`. No project ships as add-on (app)-only. The "external conversation agent" community thread explicitly resolves to a custom_component shim. [5][8][9]

### 6. The 2025–2026 `llm` helper / `AssistAPI` does not change this

`homeassistant.helpers.llm` (the `AssistAPI` / tool-calling helper) is consumed by in-process agents to expose HA control to an LLM. It does not provide an inbound external-registration hook; it is a Python API. [6]

## Plan B (recommended)

Ship a tiny HACS-installable `custom_components/openclaw_gateway/` with:

- `manifest.json` (single dependency on `conversation`),
- a config flow capturing the add-on (app)'s local socket (e.g. `http://a0d7b954-openclaw-gateway:8099`),
- one `ConversationEntity` subclass whose `async_process` proxies the turn over HTTP/WS to the add-on (app), streaming tokens back via `chat_log`.

This is ~150 LOC and is the minimum HA core requires.

## Routing model (decision, 2026-06-06)

Rob: "I want Assist as the brain, just like in OC. The brain should be able
to use subagents for work, so I'd say shouldn't be pinned to anything. But
the layer I talk to should be Opus or GPT-5.5. And subagents should always
be the right models and cheaper."

**Implications:**

- **Brain (user-facing turn)** runs in the **OpenClaw gateway**, not in the
  node. The brain model is **Opus or GPT-5.5** (premium tier). It owns the
  multi-turn conversation, the system prompt, and the choice of tools.
- **Subagents** spawned by the brain are **not pinned**. The brain picks per
  task and prefers the smallest model that works (Haiku, GPT-5.4-mini, etc.).
  The gateway is free to evolve which subagent it picks without changing the
  shim or the node.
- **Node** (this repo) is the **tool runtime** for the brain: it forwards
  every turn over `node.conversation.request`, then services any number of
  `ha.*` invocations the brain issues mid-turn via the existing
  `node.invoke.request` path (P3/P4 command surface). The dispatcher's
  pending-future model means a single conversation turn can span many tool
  rounds before the brain emits `node.conversation.result`.

**What this means for code in this repo:**

- Do **not** add model-pinning logic on the node side. The node carries no
  knowledge of which model the gateway uses. The conversation forwarder
  payload deliberately does not name a model.
- Do **not** add subagent orchestration on the node side. That is the
  brain's job; the node just answers tool invocations.
- The 30 s forwarder timeout (`_FORWARDER_TIMEOUT_S` in `http_api.py`) bounds
  a single Assist turn. If multi-step tool use needs longer, lift this
  bound — but the model itself must time out the user turn somewhere, so
  raising this is fine.
- If we ever need to give the gateway routing hints (e.g. "user prefers
  fast answers" vs "user is in deep-work mode"), add them as opaque
  fields on the conversation request payload; do not interpret them on the
  node.

## Citations

1. `homeassistant/components/conversation/__init__.py` — `async_set_agent(hass, config_entry, agent)` — https://github.com/home-assistant/core/blob/dev/homeassistant/components/conversation/__init__.py
2. `homeassistant/components/conversation/entity.py` — `ConversationEntity(RestoreEntity)` — https://github.com/home-assistant/core/blob/dev/homeassistant/components/conversation/entity.py
3. Conversation API (dev docs) — `conversation/process` WS + REST — https://developers.home-assistant.io/docs/intent_conversation_api/
4. Add-on (App) communication / `homeassistant_api: true` / `SUPERVISOR_TOKEN` — https://developers.home-assistant.io/docs/api/supervisor/endpoints and https://developers.home-assistant.io/docs/add-ons (apps)/communication
5. `extended_openai_conversation` (custom_component precedent) — https://github.com/jekalmin/extended_openai_conversation
6. "Future proofing the Conversation integration" (dev blog, `async_set_agent` breaking change) — https://developers.home-assistant.io/blog/2023/01/24/conversation-updates/
7. HA WebSocket API integration page — https://www.home-assistant.io/integrations/websocket_api/
8. Community thread: "Custom component to enable an external conversation agent" — https://community.home-assistant.io/t/custom-component-to-enable-an-external-conversation-agent/873116
9. `hasscc/ai-conversation`, `braytonstafford/grok_conversation`, `acon96/home-llm`, `michelle-avery/custom-conversation` — all `custom_components/` — https://github.com/hasscc/ai-conversation , https://github.com/braytonstafford/grok_conversation , https://github.com/acon96/home-llm , https://github.com/michelle-avery/custom-conversation
