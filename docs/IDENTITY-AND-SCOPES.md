# Identity propagation and per-user authorization

Design doc covering TODO #1 (user identity propagation).

Status: **draft, not implemented.** Code lands in follow-up PRs.

---

## Two distinct concerns

Authorization for an HA Assist turn has two separate questions. They
look similar; they are not the same. This doc keeps them in their own
sections so they aren't conflated.

### Concern A — OpenClaw agent permissions within OpenClaw itself

**What the agent itself is permitted to do**: write its own
config files, run shell commands on the gateway host, web-search,
edit its own memory, call MCP tools, etc. This is the agent's
tool inventory plus any gateway-side policy on those tools.

**This is operator-side, gateway config.** Lives in the operator's
`openclaw.json` per `agentId`. Outside the addon. Not addressed
by this doc, and changing it requires no addon code.

When an operator wants a "household assistant" agent that can't
modify OpenClaw's own state, they curate a separate `agentId`
(e.g. `clawd-household`) with a restricted tool inventory in
their gateway config. The addon's only role in concern A is
**which** `agentId` to use on `chat.send` (see "Agent selection
on chat.send" below — this is the lone overlap with concern B).

### Concern B — Restrictions on HA commands the addon dispatches

**What HA / node commands the addon will dispatch for a given
HA user.** Even if an agent has `fs.write` in its inventory, the
addon should refuse to execute it on behalf of a non-admin HA
user.

**This IS this project's job.** Mechanism is detailed below in
"Concern B — addon-side disclaimer + optional agent routing".

These overlap only in that the agentId selected on `chat.send`
affects what the agent can attempt at all (concern A's surface).
The addon's per-user enforcement of HA commands is concern B.

**Both ship in this design.** The addon does its own concern-B
work (disclaimer injection on every turn) AND offers the
operator a way to route different HA users to different
operator-configured agents (the addon-side hook into concern A).
The agent personality / scope / inventory itself stays operator
config; the routing is the addon's job.

---

## Concern B — addon-side disclaimer + optional agent routing

The clean addon-only mechanism. No gateway changes, no
correlation-on-invoke-envelope dependency.

### Step 1 — shim forwards HA user identity

HACS integration reads HA's conversation context and forwards
identity on the existing `POST /v1/conversation/stream`:

```json
{
  "conversation_id": "<ha-conversation-uuid>",
  "text": "<user utterance>",
  "actor": {
    "user_id": "<ha-user-uuid>",
    "is_admin": true
  }
}
```

`user_id` comes from `ConversationInput.context.user_id`.
`is_admin` comes from `hass.auth.async_get_user(user_id).is_admin`.

Voice / unbound / system-generated turns where no user context
exists → omit the `actor` field. Addon treats absence as
anonymous (most-restrictive defaults — same forbidden list as
`user`).

### Step 2 — addon resolves a role

The addon options carry one explicit list:

```yaml
identity:
  super_admins:
    - "<ha-user-uuid-of-rob>"
  # everyone else: role derived from is_admin
```

Resolution (addon-internal):

| `actor.is_admin` | `user_id` in `super_admins` | Role |
|---|---|---|
| true | true | `super_admin` |
| true | false | `admin` |
| false | n/a | `user` |
| (no actor) | n/a | `user` (anonymous fallback) |

### Step 3 — addon generates the per-turn forbidden-commands list

Static defaults baked into addon code, keyed by role. **No
operator config needed in the simple case** — defaults are
auto-generated from the role.

| Role | Auto-forbidden commands |
|---|---|
| `user` | `fs.write`, `fs.delete`, `fs.move`, `fs.restore`, `fs.patch`, `system.run`, `ha.reload_config`, `ha.addon_start`, `ha.addon_stop`, `ha.addon_restart`, `ha.call_service` to any domain other than the safe-domain set (`light`, `switch`, `scene`, `cover`, `media_player`, `climate`, `fan`, `vacuum`, `notify`, `input_*`, `lock`, `script`) |
| `admin` | `fs.write`, `fs.delete`, `fs.move`, `fs.restore`, `fs.patch`, `system.run`, `ha.call_service` to super-only domains (`shell_command`, `python_script`, `command_line`) and to the specific `(homeassistant, stop)` service |
| `super_admin` | none |

Operators wanting finer control can override per role via addon
options:

```yaml
identity:
  super_admins: [<rob-uuid>]
  # Optional per-role overrides (advanced)
  forbidden_commands:
    user:
      add: ["ha.call_service:lock.unlock"]   # restrict beyond default
      remove: ["script.*"]                    # loosen default
    admin:
      add: ["ha.call_service:notify.discord"] # operator-specific concern
```

`add`/`remove` patch the defaults; full replacement is
intentionally not supported (operators should NOT have to know
the full default list to make a single-item tweak).

### Step 4 — addon injects a disclaimer into the chat.send turn

The forbidden list is converted to a prompt prefix prepended to
the user turn before `chat.send`:

```text
[OpenClaw authorization context]
Calling HA user: <user_id> (role: user)
You are FORBIDDEN from invoking the following commands or service
domains for this turn:
  - fs.write, fs.delete, fs.move, fs.restore, fs.patch
  - system.run
  - ha.reload_config, ha.addon_start, ha.addon_stop, ha.addon_restart
  - ha.call_service to any domain other than light/switch/scene/
    cover/media_player/climate/fan/vacuum/notify/input_*/lock/script
If asked to do any of these, refuse politely and explain that this
user is not authorized.
[end authorization context]

<original user utterance>
```

This is **prompt-level only**, not software-enforced. A
sufficiently determined prompt-injection attack on the user side
could talk past it. Honesty in the docs: this catches the model
trying to comply with a casual / misfire request from a
not-authorized user; it does not stop an adversarial prompt.

### Step 5 — per-user agent selection on chat.send

The addon's first-class hook into concern A. Today's `chat.send`
from this addon sends no `agentId` field (`chat_relay.py:326-332`
and `:522-528`), so the gateway picks whatever default it has
configured for the session. After this design lands, the addon
resolves a per-HA-user agent and passes it explicitly.

Use cases (both real, both supported):

- **Different permissions per member.** Household members route to
  agents that operator configured (in `openclaw.json`) with
  restricted tool inventories — e.g. `clawd-household` lacks
  `fs.write`/`system.run`, `clawd-readonly` lacks `ha.call_service`
  entirely. Belt-and-suspenders with the disclaimer.
- **Different personalities per member.** Kids get a fun helper
  agent; spouse gets a no-nonsense one; you get the full Clawd.
  Nothing to do with security — just the right voice for the right
  person.

Addon options:

```yaml
identity:
  super_admins: [<rob-uuid>]
  user_agent_map:
    "<ash-uuid>": "clawd-household"
    "<kid-uuid>": "clawd-kid"
  default_agent_id: "clawd"
  # Unmapped users (incl. anonymous voice satellite turns) → default_agent_id
```

Resolution at chat.send time:

1. Look up `actor.user_id` in `user_agent_map` → that's the agentId.
2. Miss → use `default_agent_id`.
3. No default configured → omit the `agentId` field (gateway picks
   its own default, today's behavior).

Addon adds `"agentId": "<resolved>"` to the `chat.send` `params`
block when it resolves a non-null agent. Verified the gateway
already accepts this field (server-chat.js:139) — no gateway
change needed.

For Rob's house: leave `user_agent_map` empty, set
`default_agent_id: clawd`. Same as today, disclaimer is the only
protection. Add per-user mappings when concern-A agents exist.

---

## What this design does NOT enforce

**Hard software-block on shared-agent setups.** When multiple HA
users share one agent (Rob's case), the only thing protecting
the non-super users from destructive commands is the prompt
disclaimer. The addon cannot software-gate the inbound
`node.invoke.request` because the gateway's invoke envelope
carries no actor / sessionKey / runId (verified in
`/app/dist/node-registry-D3vmVKIR.js:268-275`). The correlation
gap is real and addon-only solutions for it (in-flight
serialization, monotonic-order tagging) are too fragile to
ship.

Operators who want hard enforcement use the optional concern-A
mechanism (Step 5): route restricted users to agents whose
inventories don't include destructive tools.

**Agent self-modification.** Same concern A: the agent has its
own gateway-side tools (`Write`, `Edit`, `Bash`, MCP file ops)
that don't go through the addon dispatcher. Operator-side
mitigation is to curate the agent's inventory.

---

## Implementation order

1. **Shim** — forward `actor: {user_id, is_admin}` on
   `/v1/conversation/stream`. Voice / unbound / system-generated
   turns omit the field. Tests with mocked
   `hass.auth.async_get_user`.
2. **Addon options schema** — add `identity.super_admins: []`,
   optional `identity.forbidden_commands` per-role
   `add`/`remove`, optional `identity.user_agent_map` +
   `identity.default_agent_id`.
3. **Addon role resolver** — pure function:
   `(actor, super_admins) -> role`.
4. **Addon forbidden-list generator** — pure function:
   `(role, overrides) -> list[str]`. Single source of truth for
   the defaults table above.
5. **Addon ChatRelay** — at `chat.send` time, build the
   disclaimer block from the resolved role and forbidden list,
   prepend to `text`. Select `agentId` from `user_agent_map` (or
   default). Tests exercising each role + override combination.
6. **Docs** — README + INSTALL get the operator-facing
   `identity:` block explanation and the honest "prompt-level
   only" caveat for shared-agent setups.

## Open questions

- **Disclaimer placement.** Prepending to user text is the
  simplest and most-supported approach. If the gateway exposes a
  `system_context` / `instructions` field on `chat.send` later,
  switch to that. Should not require addon changes beyond
  swapping the field name.
- **Forbidden-list updates as new commands ship.** When a future
  PR adds `ha.foo_command`, the defaults table must be updated.
  Add a CI lint: registered commands MUST appear in at least one
  role's defaults table OR be tagged as `read_only_safe`.
- **HA "Local" user types.** HA has a few special user kinds
  (long-lived access tokens, system users, OAuth clients). The
  shim's `user.system_generated` check covers the obvious cases;
  edge cases need empirical testing during implementation.

## References

- `docs/TODO.md` — TODO #1
- `docs/COMMAND-TIERS.md` — Tier A/B/C policy this disclaimer enumerates from
- `/app/dist/node-registry-D3vmVKIR.js:268-275` — canonical `node.invoke.request` envelope (no actor today; addon-only enforcement infeasible at this layer)
