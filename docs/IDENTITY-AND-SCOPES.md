# Identity propagation and scope authorization (HA Assist)

Design doc. Captures TODO #1 (user identity propagation) and the
Tier B admin gate from `docs/COMMAND-TIERS.md` as a single unified
model that ships **addon-only** — no changes to gateway code
(`/app/dist/*`) or to any external repo.

Scope: **HA Assist only.** Other channels (Discord/Signal/etc.) are
gateway-owned ingress and out of scope for this project.

Status: **draft, not implemented.** Code lands in follow-up PRs.

## Problem statement

HA Assist routes every conversation turn through the addon without
propagating which HA user is talking. The addon-internal
`ChatRelay` opens a session, sends `chat.send`, and dispatches any
resulting `node.invoke.request`s. If a household member with HA
voice access asks for something destructive — `homeassistant.stop`,
`fs.delete /config/configuration.yaml`, `system.run rm -rf` — there
is nothing software-enforced between their voice and the
destructive call. The conversational interface has zero of the UI
friction that protects the same surfaces today.

We need a model that:

1. Maps each HA user to a scope appropriate for them.
2. Defaults to the most restrictive scope for unknown / voice /
   anonymous turns (fail-closed).
3. Replaces the placeholder `OPENCLAW_ADMIN_TOKEN` gate the Tier B
   addon-lifecycle commands were going to use.
4. Stays inside the addon — no gateway PRs, no `/app/dist/*`
   changes, no operator-private gateway config schema additions
   that aren't already in scope.

## Mechanism — two gates in series

Two independent checks. Both addon-side. Both fail-closed.

### Gate 1 — agent routing (which inventory)

The addon picks which `agentId` to use on `chat.send` based on the
HA user. Each agent has its own tool inventory (configured in
`openclaw.json` operator-side, no `/app` changes). The model can
only attempt tools that are in the agent's inventory; tools the
agent doesn't have can't be called at all.

Addon options:

```yaml
identity:
  user_agent_map:
    "<rob-ha-uuid>": "clawd"
    "<ash-ha-uuid>": "clawd"
    # Stricter operator setup might look like:
    # "<household-ha-uuid>": "clawd-household"   # tools: ha.list_*, lights/switches/scenes via call_service
    # "<visitor-ha-uuid>":   "clawd-readonly"    # tools: ha.list_*, ha.get_state — no call_service at all
  default_agent_id: "clawd"
  # ↑ Used for voice turns / unbound users / anyone not in the map.
  # Operators wanting a hard fail-closed default set this to a
  # read-only agent.
```

For Rob's house: empty `user_agent_map` (or both Rob and Ash
mapped to `clawd`). For other operators: they curate
`clawd-household` / `clawd-readonly` / etc. in their own
`openclaw.json` and point the map at them.

### Gate 2 — addon dispatcher checks `is_admin` on HA-mutating commands

Defense in depth for the case where an operator's `user_agent_map`
is permissive but they still want HA's existing admin distinction
respected. The addon's `node.invoke` dispatcher reads the actor's
`is_admin` flag (stored at chat.send time, keyed by `runId`) and
applies static rules:

| Command class | Required actor flag |
|---|---|
| Read-only HA commands (`ha.list_states`, `ha.get_state`, `ha.list_areas`, `ha.list_devices`, `ha.list_services`, `ha.list_entity_registry`, `ha.list_automations`, `ha.logbook`, `ha.history`, `ha.check_config`, `ha.addon_logs`, `ha.list_addons`, `ha.addon_info`, `ha.addon_stats`, `ha.addon_changelog`, `ha.addon_documentation`) | none — anyone |
| Safe HA service calls via `ha.call_service` (domains: `light`, `switch`, `scene`, `cover`, `media_player`, `climate`, `fan`, `vacuum`, `notify`, `input_boolean`, `input_number`, `input_select`, `input_text`, `lock`, `script`) | none — anyone |
| HA admin commands: `ha.reload_config`, `ha.addon_start`, `ha.addon_stop`, `ha.addon_restart`, and `ha.call_service` to any other domain (`homeassistant`, `automation`, `supervisor`, `shell_command`, `python_script`, `command_line`, anything unknown) | `is_admin = true` |
| Destructive node commands: `fs.write`, `fs.delete`, `fs.move`, `fs.restore`, `fs.patch`, `system.run` | `is_admin = true` **and** user in `identity.super_admins` |

`(homeassistant, stop)` specifically — even though `homeassistant` is
already in the `is_admin = true` row, treat the `stop` service as
super-admin only (stopping HA is meaningfully different from
restarting it; restart recovers, stop doesn't).

Unknown commands → deny. Unknown service domains → deny for
non-admin, allow for `is_admin = true` (admins routinely write
their own scripts, integrations, etc.).

### Why two gates

- Gate 1 (agent routing) is the primary defense and is operator-
  configurable. A correct operator setup never lets Gate 2 fire
  because the destructive tools aren't in the inventory.
- Gate 2 (dispatcher) is belt-and-suspenders. If an operator
  configures a permissive agent for everyone (Rob's setup, where
  Ash + Rob both use `clawd`), the addon still respects HA's
  existing admin/non-admin distinction so Ash doesn't get more
  power conversationally than she has through the HA UI.
- Together: if either gate denies, the call is blocked. Operator
  has to make a deliberate mistake in BOTH places for a non-admin
  to do something destructive.

## Wire shape

### HACS shim → addon

`POST /v1/conversation/stream` body gains an `actor` block:

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

`user_id` and `is_admin` come from
`ConversationInput.context.user_id` + `hass.auth.async_get_user()`.
Voice turns or unbound users → omit the `actor` field; addon treats
the request as anonymous (default agent, `is_admin = false`,
not in `super_admins`).

### Addon-internal state

`ChatRelay` already has `_chat_send_canonical: dict[str, str]`
keyed by the chat.send request id. Add a parallel
`_actor_by_run: dict[str, Actor]` keyed by `runId`, populated when
the chat.send ack returns the real `runId`. Cleared on turn end.

Lookup key on inbound `node.invoke.request` is the run's `runId`
(present in the canonical invoke envelope). If `_actor_by_run`
doesn't have an entry (e.g., the run originated outside HA Assist
— shouldn't happen in this project, but defensive code) → treat as
anonymous (deny anything beyond read-only).

### Addon → gateway (`chat.send`)

`chat.send` carries the `agentId` selected by Gate 1. No new fields
on the wire — the gateway already supports `agentId` on
`chat.send`. No `actor` field on `chat.send` (would require a
gateway change).

### Gateway → addon (`node.invoke.request`)

No envelope change. Addon correlates by `runId` from its own
`_actor_by_run` map.

## What this does NOT protect against

**Agent self-modification.** The agent has its own tool inventory
that runs in the gateway's process context (`Write`, `Edit`,
`Bash`, MCP file ops). Those calls never reach the addon
dispatcher — they execute against the gateway host's filesystem,
including `~/.openclaw/*`. This project cannot software-block them
without changing gateway code.

Mitigations available without `/app` changes:

- **Don't put destructive tools in the agent's inventory.** Operator
  configures `openclaw.json` per-agent tool lists. A
  `clawd-household` agent that doesn't include `Bash`/`Write` can't
  attempt them even if asked.
- **System prompt.** Tell the agent: actor is `<name>` with
  `is_admin=<bool>`; refuse self-modification when actor is not
  in the super-admin list. Prompt-injectable, weak.

The honest summary for operators: **HA Assist non-admins are
fully software-blocked from HA-side destructive ops.
OpenClaw-side (`~/.openclaw/*`) self-modification protection is
prompt-level only until upstream gateway gates land.** Document
this prominently in the user-facing README + INSTALL.

## Implementation order

Single repo, single project, no external coordination.

1. **Shim** (`custom_components/openclaw_gateway/conversation.py`):
   forward `actor: {user_id, is_admin}` on
   `/v1/conversation/stream`. Voice / unbound turns omit the
   field. Tests with a mocked `hass.auth.async_get_user`.
2. **Addon options schema** (`addon/config.yaml`): add
   `identity.user_agent_map` (dict) and
   `identity.default_agent_id` (string) and
   `identity.super_admins` (list).
3. **Addon `ChatRelay`**: read `actor` from the inbound stream
   request. Resolve `agentId` (Gate 1) before `chat.send`. Store
   actor in `_actor_by_run[runId]` after ack.
4. **Addon dispatcher** (`addon/node/src/openclaw_node/commands/dispatcher.py`):
   per-command static rule table (read / admin-required /
   super-required) and per-domain table for `ha.call_service`.
   Gate before handler invocation. Regression test for each row.
5. **Tier B commands** drop in cleanly: `ha.addon_start`/`stop`/
   `restart` register against the existing dispatcher and the
   admin-required gate applies automatically.
6. **Docs**: update `docs/COMMAND-TIERS.md` to point at this doc
   for the gate mechanism (drop `OPENCLAW_ADMIN_TOKEN` references).
   Update README + INSTALL with the operator-facing
   `identity:` config block + the self-modification caveat.

## Open questions

- **`script.*` services as user-safe.** The risk surface is the
  operator's own script library; we treat it as user-safe because
  scripts are deliberately authored. Flag in docs but don't gate.
- **`lock.unlock` specifically.** Treated as user-safe because
  household members want it. Operators uncomfortable with that can
  put non-admin users on a `clawd-readonly` agent that doesn't
  have `ha.call_service` at all.
- **Roles vs flags.** This doc uses `is_admin` (HA-native flag) +
  `super_admins` (addon-config list) rather than a three-value
  enum (`user`/`admin`/`super_admin`). Decision: stay close to HA's
  vocabulary. Logging and error messages can still use role-
  shaped strings for operator clarity ("denied: admin required",
  "denied: super-admin required").
- **`user_agent_map` and `default_agent_id` interaction with the
  gateway's existing agent selection logic.** Verify the gateway
  honours `agentId` on `chat.send` consistently across agent
  configurations. (Today's HA Assist sessions don't pass `agentId`
  — they get the gateway-default agent. Need to confirm explicit
  agentId works without a gateway-side allow list.)

## References

- `docs/TODO.md` — TODO #1 (user identity propagation)
- `docs/COMMAND-TIERS.md` — Tier A/B/C policy this design supersedes for the gate mechanism
- `docs/LESSONS.md` — "Cross-agent code review applies to CI / scripts / workflows too" (each implementation PR goes through GPT-5.5 review)
