# OpenClaw Integration — node as conversation relay

> **Historical snapshot.** Captures the design pivot to using the
> existing chat surface for relay. The architecture described here is
> what shipped; cross-check against `STATUS.md` and `docs/design/PLAN.md` for the
> current state.

> **Identity check.** Clawd is OpenClaw. The "brain" answering HA Assist
> turns is Clawd running in OpenClaw — same agent that handles Discord,
> Signal, etc.
>
> **Architecture check.** The HA Assist integration is a **node**, not
> a plugin. The node connects to OpenClaw with `role: "node"` and the
> appropriate scopes. It uses the **existing chat surface** to relay HA
> Assist turns into a session and the **existing `node.invoke` surface**
> to handle `ha.*` tool calls coming back from the agent.

## What was wrong (this is the durable lesson)

Earlier iterations of this design got two things wrong:

1. **Built a parallel standalone gateway in `gateway/`** with its own
   `Brain`, providers, and pairing protocol. That work duplicated
   functionality OpenClaw already provides. It still lives as a
   reference for third-party users who don't run OpenClaw, but it is
   **not the path Rob's deployment uses**.
2. **Invented a `node.conversation.request` event type** on the WS
   between node and gateway. This was unnecessary — the OpenClaw
   Gateway Protocol already exposes `chat.send` / `sessions.send` /
   `sessions.messages.subscribe` for exactly this purpose.

The root cause: I read `/app/docs/nodes/index.md` which says

> Nodes are **peripherals**, not gateways. They don't run the gateway service.
> Telegram/WhatsApp/etc. messages land on the **gateway**, not on nodes.

and concluded that nodes can't *originate* conversation turns. **That
is wrong.** A node *receives* commands as a peripheral, but a node with
chat scopes can also *call* `chat.send` as an outbound RPC to inject a
conversation turn. The "peripheral" framing only covers half of what a
node can do.

If you're reading this after a context compaction and the upstream
OpenClaw docs still don't make the relay pattern explicit, see also the
runtime-audit note at `runtime-audits/openclaw-node-conversation-relay.md`
in the workspace.

## Architecture (the correct one)

```
HA Assist UI
    │ user turn (text + conversation_id + language)
    ▼
custom_components/openclaw_gateway/  (ConversationEntity)
    │ POST /v1/conversation
    ▼
openclaw-hass-node (Python, this repo)
    │  • already paired with OpenClaw via Gateway Protocol
    │  • calls chat.send to inject the turn into a session
    │  • subscribes via sessions.messages.subscribe for the reply
    │  • services ha.* via node.invoke when the agent calls them
    ▼
OpenClaw Gateway
    │ routes session messages to the configured agent (Clawd)
    ▼
Clawd handles the turn, uses ha.* tools as needed
    │  • ha.list_states / ha.light_turn_on / etc. via node.invoke
    │  • final assistant reply lands on the session
    ▼
Reply flows back: session message → node subscription → /v1/conversation → shim → HA Assist speech
```

Same wire protocol the node already speaks. Same `ha.*` command surface
already wired in P4. No new event types. No standalone brain.

## What the node needs (P5.11 work)

This refactor replaces the placeholder behaviour in
`node/src/openclaw_node/http_api.py::assist_turn` with real chat-surface
routing. Concretely:

1. After the connect handshake, the node already has an open WS to the
   gateway with `role: "node"`. Add the chat scopes
   (e.g. `operator.read`) to the `connect.params.scopes` list so the
   gateway will accept `chat.send` from this connection.
2. **`/v1/conversation` (POST)** — when an Assist turn arrives:
   - Pick or open a session keyed by `conversation_id` (HA's
     conversation id is stable across follow-ups, so per-`conversation_id`
     sessions thread correctly).
   - Subscribe to the session via `sessions.messages.subscribe` if not
     already subscribed.
   - Send the turn via `chat.send` (or `sessions.send` — TBD which is
     idiomatic; both are listed in protocol.md §"Chat execution").
   - Await the next assistant reply event on the subscription with a
     timeout matching `_FORWARDER_TIMEOUT_S` (30s).
   - Return the reply text as the HTTP response.
3. **`ha.*` invokes from the agent** — already work. No changes needed:
   the gateway sends `node.invoke.request`, the dispatcher routes to the
   existing handlers, the result goes back as `node.invoke.result`. The
   handlers don't know or care that the *reason* the gateway is invoking
   them is an Assist turn.

## What goes away

The following were workarounds for the wrong architecture and can be
deleted:

- **`gateway/` workspace member** (standalone server, brain, providers,
  invoke dispatcher, device registry, auth). Kept only briefly as a
  reference for the README; otherwise deleted.
- **`node/src/openclaw_node/conversation_dispatcher.py`** — invented to
  correlate `node.conversation.request` / `result` frames I shouldn't
  have invented.
- **`node.conversation.request` / `node.conversation.result`** routing
  in `node/src/openclaw_node/gateway_ws.py`.
- **`NodeRuntime.conversation_forwarder`** hook and the related
  `assist_turn` forwarder logic. The new `assist_turn` uses the chat
  surface directly via an injected `ChatRelay` (or similar) backed by
  the gateway WS.

The HACS shim, the `ha.*` command surface, the Ed25519 handshake on the
node side, the device identity / pairing flow, the `/v1/conversation`
endpoint shape — all **stay**. They were always right.

## P5.11 scope

1. Delete the wrong-direction code listed above.
2. Add a `ChatRelay` class on the node that wraps `chat.send` +
   `sessions.messages.subscribe` over the existing gateway WS.
3. Rewrite `assist_turn` to use it.
4. Tests: relay + assist_turn end-to-end with a fake WS.
5. Update `docs/design/PLAN.md` to reflect "node as conversation relay" as the
   architecture, with no more "build a brain" language.

This is real code work — best done with you available rather than
autonomously. The cleanup PR (this one) removes the wrong direction and
leaves clear hooks for the relay implementation.
