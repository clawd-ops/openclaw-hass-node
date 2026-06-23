# Identity propagation and scope authorization

Design doc. Captures TODO #1 (user identity propagation), the Tier B
admin gate from `docs/COMMAND-TIERS.md`, and the subagent-allowlist
piece of TODO #11 as a single unified model.

Status: **draft, not implemented.** Code lands in follow-up PRs.

## Problem statement

HA Assist → gateway → tool dispatch currently does not propagate which
human is talking. Every conversation turn is treated identically; the
node's command dispatcher and the agent's tool registry both have no
notion of "who is asking". That leaves three failure modes:

1. **Conversational blast radius is uniform.** A household member with
   HA voice access can ask the agent for anything the agent can do,
   including destructive ops (`fs.write`, `system.run`,
   `homeassistant.stop`). UI-shaped friction doesn't apply over voice.
2. **The Tier B addon-lifecycle commands need an admin gate** but
   gating them with a static `OPENCLAW_ADMIN_TOKEN` was a placeholder
   for "we don't have user identity yet". Once we have identity, the
   token is redundant.
3. **The subagent allowlist enforcement** (TODO #11) needs a way to
   identify the caller's actor role to decide which commands are
   subagent-safe. Same identity plumbing.

This design lands all three on one mechanism.

## Roles

Three explicit roles. Mapping is automatic for `user`/`admin` based on
HA's native `is_admin` flag; `super_admin` is an explicit opt-in list
in addon options.

| Role | How you get it | Notes |
|---|---|---|
| **user** | HA `is_admin = False` | Read + safe-domain `call_service`. Default for unknown / unmapped / voice-satellite turns. |
| **admin** | HA `is_admin = True` | Everything `user` can do, plus HA lifecycle (`reload_config`, addon `start`/`stop`/`restart`), `homeassistant.restart`, most service domains. |
| **super_admin** | Explicit list in addon options | Everything `admin` can do, plus destructive node commands (`fs.write`, `fs.delete`, `system.run`) and dangerous services (`homeassistant.stop`, `shell_command.*`, `python_script.*`, `command_line.*`). Also gates OpenClaw self-config. |

### Why explicit opt-in for super_admin

HA admins already have UI-level access to everything in the
`super_admin` row through Settings / Developer Tools / addons / File
Editor. The UI requires deliberate clicks; the conversational
interface fires from a 5-word voice command with no confirmation.
Different threat models, different gates.

A HA admin (Ash) gets the `admin` role automatically — she can
control the household via voice without Rob doing anything. She
**doesn't** get destructive node commands or destructive service
domains unless Rob adds her to `super_admins:`.

## Mapping config (addon options)

The only thing Rob has to configure:

```yaml
identity:
  super_admins:
    - "<rob-ha-uuid>"
  # role is otherwise derived from HA is_admin
```

No `admins:` list — HA tracks that natively. No `users:` list — it's
the default.

## Wire shape

The role resolution happens in the addon (before `chat.send`), not
the gateway. Adding a new field to one upstream message rather than
distributing the role lookup across the gateway and per-tenant config
keeps the gateway agnostic of HA users.

### HACS shim → addon

Existing `POST /v1/conversation/stream` body gains:

```json
{
  "conversation_id": "...",
  "text": "...",
  "actor": {
    "user_id": "<ha-user-uuid>",
    "is_admin": true
  }
}
```

`user_id` and `is_admin` come from `ConversationInput.context.user_id`
+ a HA user lookup (`hass.auth.async_get_user(user_id)`). Voice
turns without a bound user → omit the `actor` field; addon treats as
`user` role.

### Addon → gateway (`chat.send`)

Addon resolves role from its `identity.super_admins` list plus the
shim's `is_admin` flag, then forwards on `chat.send`:

```json
{
  "method": "chat.send",
  "params": {
    "sessionKey": "...",
    "text": "...",
    "actor": {
      "user_id": "<ha-user-uuid>",
      "role": "user" | "admin" | "super_admin"
    },
    "...": "..."
  }
}
```

Gateway forwards `actor` into the agent's per-turn context AND
includes it on every `node.invoke.request` derived from this run.

### Gateway → node (`node.invoke.request`)

```json
{
  "method": "node.invoke.request",
  "params": {
    "nodeId": "...",
    "command": "ha.call_service",
    "paramsJSON": "...",
    "actor": {
      "user_id": "<ha-user-uuid>",
      "role": "admin"
    }
  }
}
```

Node dispatcher reads `actor.role` and gates the invoke.

## Enforcement

Two layers, in order:

### 1. Node dispatcher gate (software-blocked)

`commands/dispatcher.py` gets a per-command `min_role` table:

```python
COMMAND_MIN_ROLE = {
    "ping": "user",
    "fs.read": "user",
    "fs.list": "user",
    "fs.stat": "user",
    "fs.glob": "user",
    "fs.history": "user",
    "fs.diff": "user",
    "fs.restore": "super_admin",
    "fs.write": "super_admin",
    "fs.delete": "super_admin",
    "fs.move": "super_admin",
    "fs.patch": "super_admin",
    "system.run": "super_admin",
    "system.which": "user",
    "ha.list_states": "user",
    "ha.get_state": "user",
    "ha.list_areas": "user",
    "ha.list_devices": "user",
    "ha.list_services": "user",
    "ha.list_entity_registry": "user",
    "ha.list_automations": "user",
    "ha.logbook": "user",
    "ha.history": "user",
    "ha.check_config": "user",
    "ha.addon_logs": "user",
    "ha.list_addons": "user",
    "ha.addon_info": "user",
    "ha.addon_stats": "user",
    "ha.addon_changelog": "user",
    "ha.addon_documentation": "user",
    "ha.light_turn_on": "user",
    "ha.light_turn_off": "user",
    "ha.reload_config": "admin",
    "ha.call_service": "policy",  # see service-domain allowlist below
    # Tier B (proposed, not yet implemented)
    "ha.addon_start": "admin",
    "ha.addon_stop": "admin",
    "ha.addon_restart": "admin",
}
```

Dispatcher refuses with `INSUFFICIENT_ROLE` when `actor.role` is
below the command's `min_role`. The check happens before the handler
runs; prompt instructions to the model can't bypass it.

### 2. Service-domain allowlist for `ha.call_service` (admin-tier split)

`ha.call_service` is too coarse to gate with one `min_role` — most
service domains are user-safe, some are destructive. Static lists in
`commands/ha_services_policy.py`:

```python
USER_ALLOWED_SERVICE_DOMAINS = frozenset({
    "light", "switch", "scene", "cover", "media_player",
    "climate", "fan", "vacuum", "notify", "input_boolean",
    "input_number", "input_select", "input_text",
    "lock",  # lock only; ha.lock_lock vs ha.lock_unlock split downstream
    "script",  # user can run pre-defined scripts; risk surface is Rob's script library
})

ADMIN_REQUIRED_SERVICE_DOMAINS = frozenset({
    "automation",  # mass-toggle automations is admin-shaped
    "scene", "input_*",  # explicit add for completeness
    "homeassistant",  # restart/reload — admin can do this
    "supervisor",
})

SUPER_ADMIN_REQUIRED_SERVICE_DOMAINS = frozenset({
    "shell_command",
    "python_script",
    "command_line",
})

# `homeassistant.stop` specifically is super_admin even though
# `homeassistant.restart` is admin. Resolved at call-time by inspecting
# the (domain, service) pair, not just the domain.
SUPER_ADMIN_REQUIRED_SERVICES = frozenset({
    ("homeassistant", "stop"),
})
```

Resolution order at dispatch:
1. If (domain, service) is in `SUPER_ADMIN_REQUIRED_SERVICES` → super_admin.
2. If domain is in `SUPER_ADMIN_REQUIRED_SERVICE_DOMAINS` → super_admin.
3. If domain is in `ADMIN_REQUIRED_SERVICE_DOMAINS` → admin.
4. If domain is in `USER_ALLOWED_SERVICE_DOMAINS` → user.
5. **Unknown domain → admin.** Default-deny for `user`; opt-in for known-safe domains only.

### 3. Agent system prompt (informational, not enforcement)

The agent receives the actor's role in its per-turn system context so
it can:

- Phrase refusals helpfully ("I can't restart Home Assistant for you;
  ask Rob to do that") rather than getting a raw `INSUFFICIENT_ROLE`
  error and surprising the user.
- Avoid trying obviously-denied actions to save round-trips.

This is **not** the gate — the dispatcher gate is. The prompt
instruction just makes the experience cleaner.

## What this does NOT protect against (yet)

**Gateway-side tool surface is currently un-gated.** The agent has its
own tool set (`Write`, `Edit`, `Bash`, MCP file ops, etc.) that runs
in the gateway's process context with full filesystem access to
`~/.openclaw/`. None of that goes through the node dispatcher.

That means under the design above, a user with `admin` role can still
ask the agent to:

- Delete `~/.openclaw/openclaw.json` via `Write`/`Bash`
- Wipe `~/.openclaw/memory/` via `Bash`
- Modify other agents' workspaces

**Phase 2 (separate gateway ticket):** mirror the node's `min_role`
gate on the gateway's tool dispatcher. Each tool declares a `min_role`
+ optional path allowlist. `Write`/`Edit` outside known-safe roots
(working buffer, daily memory) require super_admin. `Bash` requires
super_admin (or a per-command allowlist). Symmetric to the node
gate.

Until Phase 2 lands, the protection for OpenClaw self-modification is
**prompt-level only**: the agent's system prompt receives the actor's
role and is instructed to refuse self-modification for non-super
actors. Prompt-injectable, weaker than the node-side enforcement.

This asymmetry is intentional for an initial cut — the node gate is
where most of the realistic blast radius lives, and the gateway gate
is a deeper architectural change that touches every tool in every
agent's inventory.

## Implementation order

This design replaces / unifies TODO #1, the Tier B section of
`docs/COMMAND-TIERS.md`, and the subagent-allowlist piece of TODO #11.

1. **Shim**: forward `user_id` + `is_admin` on `/v1/conversation/stream`.
   Voice/anonymous turns omit the field. Doc-test that
   `ConversationInput.context.user_id` resolves correctly.
2. **Addon options**: add `identity.super_admins: []` to
   `addon/config.yaml` schema.
3. **Addon**: resolve `role` from `(is_admin, super_admins)` before
   `chat.send`. Default to `user` when shim omits the field.
4. **Gateway**: accept `actor` on `chat.send`, forward into agent
   context, attach to outbound `node.invoke.request`. Gateway-side
   change — separate PR, separate review.
5. **Node**: per-command `min_role` table + dispatcher gate. Service-
   domain policy for `ha.call_service`. Regression tests including a
   forged-actor probe (gateway claims `super_admin` but lacks
   signature) to confirm the gate doesn't fail-open.
6. **Agent system prompt**: receive actor role + reflect in refusals.
7. **(Phase 2, separate gateway ticket)**: gateway-side tool middleware
   with `min_role` + path allowlist for destructive tools.

Tier B addon lifecycle commands (`ha.addon_start`/`stop`/`restart`)
land **after** step 5 — they ride the same dispatcher gate that was
built for the existing commands.

## Open questions

- **Where does the role-name canon live?** `user`/`admin`/`super_admin`
  in this doc. Should we use the same names in addon options, gateway
  envelope, and dispatcher table? Yes — pick one canonical spelling
  and use it everywhere. No silent aliasing.
- **What about forged actors?** Gateway must not trust a node-side
  `actor` field on inbound RPC — only the shim → addon → gateway
  direction is authoritative. Gateway-side enforcement: actor comes
  from the chat.send turn, not from the node. Node dispatcher trusts
  the `actor` on `node.invoke.request` because the request came from
  the gateway and the WS channel is mutually authenticated.
- **Voice turns / unknown users.** Default to `user`. Document in
  user-facing docs so operators know the safe-default.
- **`script.*` services.** Treated as `user` for now because scripts
  are author-defined and the risk surface is the operator's library.
  Flag in docs but don't gate at the OpenClaw layer.
- **`lock.unlock`.** Treated as `user` for now; high-value
  household services often need it. Operators can override per
  install via a future per-service deny list if needed.

## References

- TODO #1 — original user-identity item (`docs/TODO.md`)
- TODO #11 — sunset HA MCP → node-tool path
- `docs/COMMAND-TIERS.md` — Tier A/B/C policy this design builds on
- `docs/LESSONS.md` — "Cross-agent code review applies to CI / scripts
  / workflows too" (this design crosses multiple subsystems; treat
  each PR accordingly)
