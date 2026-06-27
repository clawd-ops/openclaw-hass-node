# MEMORY — agent recall entry point

> **This is not the user-facing README.** Users want
> [`INSTALL.md`](INSTALL.md). This file is the post-compaction landing
> page for the Clawd agent: minimal, pointer-heavy, so a fresh session
> can orient in seconds without re-deriving everything from code.

## When you resume, read these in order

1. [`STATUS.md`](STATUS.md) — what's shipped, what's known-broken.
2. [`TODO.md`](TODO.md) — open work + recent merged PRs.
3. [`design/PLAN.md`](design/PLAN.md) — *why* the architecture looks the way it does (only if you need design context for the work).
4. [`README.md`](README.md) — docs index if you need to navigate further.

If those four are current, you're current.

## Architecture in one diagram

```
HA Assist UI
    │ user turn
    ▼
custom_components/openclaw_gateway/   ← HACS shim (ConversationEntity)
    │ POST /v1/conversation
    ▼
node/  (OpenClaw add-on)
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

No bespoke gateway server, no parallel brain. The node is a standard OpenClaw node speaking the existing Gateway Protocol.

## Load-bearing invariants

These are the rules that surprise people. Front-loaded so you don't break them.

- **Dual-role WS pairing.** The node holds two parallel gateway connections: `role: node` (for `node.invoke.*`) and `role: operator` (for `chat.send` + `sessions.messages.subscribe`). Gateway role policy is binary per-method; `chat.send` is operator-scope. There is no `node.chat.send`.
- **`/config` is proposal-gated.** Mutation handlers (`fs.write`, `fs.patch`, `fs.move`, `fs.delete`, `ha.config.*`) return `PROPOSAL_REQUIRED` today; the agent-bridge round-trip is TODO #20.
- **`.storage/` is read-only to the node.** Hard rule. Writes are refused at the dispatcher unless `unsafe_storage=true` + accepted proposal.
- **HA URL is hard-pinned to `http://supervisor/core`** when `SUPERVISOR_TOKEN` is present, so a user-supplied `HASS_URL` never receives the privileged Supervisor token.
- **Actor signing is derived from `local_api_token`** via HMAC label `openclaw-hass-node actor-signing v1`. There is no separate `actor_secret`.
- **One node per HA instance.**
- **HACS shim is required** because HA's conversation-agent registration is in-process Python only. See [`research/CONVERSATION-AGENT.md`](research/CONVERSATION-AGENT.md).

## Resume rituals

- Bump version → `scripts/bump-version.py <version>` (touches all 5 sources together; CI fails on drift).
- Cross-provider review is policy: Clawd writes, Codex (gpt-5.5) reviews before merge. See [`CONTRIBUTING.md`](CONTRIBUTING.md).
- After meaningful work, refresh this file if the diagram or invariants changed; otherwise leave it alone.
