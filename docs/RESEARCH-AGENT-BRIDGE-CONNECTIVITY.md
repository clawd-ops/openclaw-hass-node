# Research: agent-bridge connectivity from the node

**Question.** Does the node connect to agent-bridge directly, or does
the gateway broker proposals on the node's behalf?

**Verdict: Gateway brokers.** The node speaks only the OpenClaw
gateway WS protocol. When a node command wants to mutate a
protected-root path, it emits a structured `propose_edit` *request*
back to the gateway over its existing WS connection. The gateway
translates that into an MCP `propose_edit` call against the
agent-bridge MCP server (which lives in the gateway's MCP server
config), and relays the eventual `resolve_proposal` result back to
the node.

## Why broker, not direct

1. **One transport, one auth.** The node has already authenticated to
   the gateway as `hass-node@<ha-instance-id>` with `operator.write`
   and `operator.admin` scopes. agent-bridge has its own auth model
   (per-peer keys). Asking the node to also stand up a second
   connection means a second credential to provision, rotate, and
   audit.

2. **Network shape.** The node sits behind a HA installation —
   possibly home NAT, no inbound. The gateway is the consolidation
   point that already has outbound connectivity to the agent-bridge
   MCP server. Mirroring that connectivity at the node duplicates
   firewall + DNS surface for no behavioral gain.

3. **Auditability.** All proposals on this gateway already flow
   through the gateway-side agent-bridge client. Routing the node's
   proposals through the same client means one log surface, one
   audit trail, one peer identity in the bridge UI.

4. **Node stays dumb.** The PLAN treats the node as a peripheral
   ("not a brain"). Brokering keeps the node's surface to "I want to
   write this; tell me yes or no". The node never needs to know
   about MCP, topics, peer identities, or the bridge protocol.

5. **Failure isolation.** If agent-bridge is unreachable from the
   gateway, the gateway can surface a clear "proposals unavailable"
   state to both the user and the node. With direct connectivity,
   the node has its own partial-failure mode to reason about.

## How it looks on the wire

Node → Gateway (over existing node WS):

```json
{
  "op": "node.propose",
  "id": "req-001",
  "kind": "fs.patch",
  "path": "/config/automations.yaml",
  "patch": "...unified diff...",
  "summary": "Add doorbell automation",
  "meta": { "ha_version": "2026.6.1", "domain": "automation" }
}
```

Gateway side:
1. Validates the node's scopes (`operator.write` for protected roots).
2. Calls agent-bridge MCP `propose_edit` with topic, body, and the
   node's peer identity tagged in the proposal metadata.
3. Waits for the user's `resolve_proposal(accept|reject)` via the
   normal agent-bridge UI / MCP flow.
4. Sends the result back to the node:

```json
{
  "op": "node.propose.result",
  "id": "req-001",
  "accepted": true,
  "proposal_id": "ab12cd34"
}
```

5. Node applies the mutation (after capturing the prior bytes into
   `/share/openclaw-backups/` per `BACKUPS.md`), and reports
   `node.propose.applied` with the resulting sha.

## What this changes in PLAN.md

- "Mutation control" section already assumes proposal gating; this
  research confirms the transport is gateway-brokered.
- `agent-bridge.*` module in `node/src/commands/` is renamed to
  `propose.*` and contains only the gateway-side request/response
  helpers — no MCP client, no agent-bridge URL config in the node.
- One open question closed.

## When we'd revisit

- If the node ever needs to participate in multi-agent collaboration
  outside the scope of "ask the user to accept this write" — e.g.
  the node coordinating directly with another node — direct
  connectivity becomes worth reconsidering. Not in scope for v1.
