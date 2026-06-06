# OpenClaw Integration (P5.10)

> **Identity check.** Clawd is OpenClaw. When this doc talks about "the
> brain" answering HA Assist turns, in Rob's deployment **the brain is
> Clawd running in OpenClaw** — the same agent that handles Discord,
> Signal, and the rest. The `gateway/` workspace in this repo is a
> *standalone reference* for non-OpenClaw users; the integration below
> is the canonical path for Rob.

## Decision

HA Assist integrates with OpenClaw as a **Channel plugin**, modelled on
the existing Discord / Signal / Telegram channels:

- Each `node.conversation.request` arrives as an inbound message on a
  "HA Assist" channel.
- Clawd (or whichever agent is configured for that channel) handles the
  message exactly like any other inbound turn.
- The `ha.*` command surface is exposed as Tool plugins so the agent can
  call them mid-turn. The plugin proxies tool calls back to the node
  over the existing node WS protocol.
- The agent's final response becomes `node.conversation.result`.

Same node-side protocol whether OpenClaw or the standalone gateway is
on the other end. Node + HACS shim never change.

## Why a Channel plugin (not Provider, not Tool, not new gateway code)

| Shape    | Fit                                                                                  |
| -------- | ------------------------------------------------------------------------------------ |
| Channel  | ✅ messages-in, replies-out, threaded conversations — exactly HA Assist's shape       |
| Provider | ✗ providers add models, not transports                                               |
| Tool     | ✗ tools register agent capabilities, not inbound channels                            |
| In-core  | ✗ "we wouldn't add this to OpenClaw core" — plugins are the supported extension path |

Confirmed by `/app/docs/plugins/building-plugins.md` in the OpenClaw repo.

## Plugin shape (proposed)

Package: `@clawd-ops/openclaw-ha-assist` (or in-repo as
`extensions/ha-assist/` if bundled).

Two pieces:

### 1. Channel plugin — `ha-assist`

Owns the WS listener that nodes connect to. Responsibilities:

- Accept node WS connections.
- Run the existing handshake (challenge → connect → Ed25519 verify → token).
  The verification logic in `gateway/src/openclaw_gateway/auth.py` is the
  reference; the TypeScript port mirrors it byte-for-byte against the
  same v3 payload the node-side `DeviceIdentity.sign_connect` produces.
- Persist device registry (PENDING / PAIRED + token) in OpenClaw's existing
  state store. No more in-memory or per-process JSON.
- On `node.conversation.request`, emit an inbound channel message keyed
  by `conversationId` (channel id) so multi-turn conversations thread
  correctly. Forward `text` + optional `language` as the message body.
- On agent reply, emit `node.conversation.result` with the matching
  `conversationId`.
- On disconnect, cancel any in-flight invokes (mirror
  `gateway/src/openclaw_gateway/invoke_dispatcher.py` `cancel_all`).

Pairing approval flows through whatever admin path OpenClaw already uses
for new devices (CLI subcommand, settings panel, agent-bridge proposal).

### 2. Tool plugin — `ha-tools`

Registers the 13 `ha.*` commands as agent tools (same shapes as
`gateway/src/openclaw_gateway/tools.py`'s `HA_TOOLS`). Each tool
implementation:

1. Looks up the connected node session by its current
   `conversationContextId` (or the agent's per-turn binding).
2. Sends `node.invoke.request` over that session's WS, awaits
   `node.invoke.result` keyed by `invokeId`. The correlation dispatcher
   pattern is identical to the standalone gateway's `InvokeDispatcher`.
3. Returns the wire result dict for the agent to consume.

## Reusable from this repo

When porting, lift the wire-protocol primitives — they are deliberately
small and SDK-free:

- v3 payload reconstruction and signature verification
  (`gateway/src/openclaw_gateway/auth.py`)
- Device registry state machine
  (`gateway/src/openclaw_gateway/device_registry.py`)
- Pending-future correlation pattern for invokes and conversations
  (`gateway/src/openclaw_gateway/invoke_dispatcher.py`,
  `node/src/openclaw_node/conversation_dispatcher.py`)
- The 13 tool shapes (`gateway/src/openclaw_gateway/tools.py`)

The brain / provider abstraction (`brain.py`, `providers*.py`) does
**not** port — OpenClaw already does model routing.

## What this means for the standalone gateway

`gateway/` stays as the reference for third-party users who don't run
OpenClaw. Tests and CI continue to cover it. Once the OpenClaw plugin
ships, the README's "run the gateway" section gains a parallel
"OpenClaw users skip this" callout.

## Open items before P5.10 implementation

- **Plugin language:** TypeScript per `building-plugins.md`. Translate
  the Python primitives above into TS. The auth payload string format is
  the binding contract — keep the field order identical or the node
  rejects the handshake.
- **Channel identity:** does an HA Assist conversation get its own
  channel per HA instance, or per agent? Probably per HA instance so the
  agent-bridge per-device approval flow makes sense.
- **Tool routing:** OpenClaw tools are stateless by default; routing a
  tool call back to the *specific* node that initiated the current
  conversation needs the tool to read the session's
  `conversationContextId` from the agent runtime. Look at how Discord
  channel plugins thread message-id ↔ tool-call state.
- **MCP retirement:** P6 keeps the existing `mcp__homeassistant*` MCPs
  parallel to the new `ha-tools` plugin until the readiness harness
  prints `RETIREMENT_READY`. No big-bang.
