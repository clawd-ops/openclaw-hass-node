# Identity propagation and HA-command authorization

Design doc. Captures TODO #1 (user identity propagation) as a single,
narrow problem: **the addon needs to know which HA user is talking
so it can gate destructive HA commands per user.**

Status: **draft, not implemented.** Code lands in follow-up PRs.

## Two separate concerns — this doc only covers one

These are easy to conflate. Calling them out explicitly:

### Concern A — OpenClaw agent's tool inventory (out of scope)

What the agent itself is allowed to do (write OpenClaw config,
execute shell commands, web-search, edit its own memory, etc.) is
configured per-agent in the operator's gateway `openclaw.json`.
That is a gateway / operator concern. It is not addressed in this
repo, and changing it requires no addon code.

An operator who wants, say, a "household assistant" agent that can
only read HA state and control lights configures a separate
`agentId` (`clawd-household`) with a curated tool inventory in
their gateway config, then routes that agent to the household
member. None of that touches `openclaw-hass-node`.

### Concern B — Addon gate for HA commands (the subject of this doc)

When the addon receives `node.invoke.request` for `ha.*` /
`fs.*` / `system.*` commands, it needs to know which HA user
originated the request so it can apply per-user rules. Specifically:

- Non-admin HA users should not be able to trigger HA-mutation
  commands (`ha.reload_config`, `ha.addon_start`/`stop`/`restart`,
  `ha.call_service` to dangerous service domains).
- Only explicitly-listed super-admin HA users should be able to
  trigger destructive node commands (`fs.write`, `fs.delete`,
  `system.run`, etc.).

This is concern B — entirely an addon problem because it gates
commands the addon dispatches. The operator's gateway-side
configuration in concern A complements but does not replace this
gate: even if a permissive agent has `fs.write` in its inventory,
the addon should refuse the call when the originating HA user is
not authorized for it.

Both gates are useful. They protect against different mistakes
(operator misconfigured an agent vs. user is escalated past their
HA role). This doc handles the second.

## Mechanism — addon-side gate driven by HA user identity

Two-step:

1. **Shim propagates HA user identity into the addon** via the
   existing `POST /v1/conversation/stream` request. Concretely:
   the HACS integration reads `ConversationInput.context.user_id`
   from HA's conversation request, looks up the user via
   `hass.auth.async_get_user(user_id)`, extracts `is_admin`, and
   sends both on the addon request body:

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

   Voice / unbound / service-call turns where no user context
   exists → omit the `actor` field. Addon treats absence as
   anonymous (most-restrictive defaults).

2. **Addon dispatcher gates HA / node commands** using a static
   rule table keyed by command (and for `ha.call_service`, also
   keyed by service domain). For each `node.invoke.request` the
   addon receives, it looks up the originating actor (see "open
   technical question" below) and applies:

   | Command class | Required actor flag |
   |---|---|
   | Read-only HA + safe-domain `ha.call_service` (lights, switches, scenes, covers, media_player, climate, fan, vacuum, notify, input_*, lock-lock, script) | none — anyone, including anonymous |
   | HA admin commands — `ha.reload_config`, `ha.addon_start`/`stop`/`restart`, `ha.call_service` to admin-required domains (`homeassistant`, `automation`, `supervisor`) | `is_admin = true` |
   | Destructive — `fs.write`, `fs.delete`, `fs.move`, `fs.restore`, `fs.patch`, `system.run`, `ha.call_service` to `shell_command.*` / `python_script.*` / `command_line.*`, `(homeassistant, stop)` specifically | `is_admin = true` AND user in addon-options `identity.super_admins` |
   | Unknown command, unknown service domain | deny for non-admin, allow for `is_admin = true` (admin can experiment) |

   Resolution order for `ha.call_service`: exact (domain, service)
   match first → domain match → fallback. So `(homeassistant, stop)`
   correctly elevates to super-admin even though the `homeassistant`
   domain alone is admin-required.

## Addon options

The only operator-facing config this introduces:

```yaml
identity:
  super_admins:
    - "<ha-user-uuid-of-rob>"
  # everyone else: role derived from HA's is_admin flag
```

No `admins:` list — HA tracks that natively. No `user_agent_map`
or `default_agent_id` — that's concern A and lives in the operator's
gateway config, not here.

## What this protects against

- Non-admin HA users (e.g., kids' accounts, guest accounts) can ask
  the agent for anything, but the addon refuses to dispatch
  HA-mutation or destructive commands on their behalf. Worst case
  for them: lights toggle, read sensor state, run pre-defined
  scripts (script library is operator's responsibility).
- HA admins (e.g., spouse) get HA's full UI-equivalent power
  conversationally. They cannot trigger destructive node commands
  (`fs.write`, `system.run`, etc.) unless explicitly added to
  `super_admins`. This mirrors the distinction between "HA admin
  via UI" (deliberate clicks) and "OpenClaw super-admin"
  (voice-grade no-friction).
- Super-admins (Rob) get everything.

## What this does NOT protect against

**Agent self-modification via gateway-side tools.** Concern A —
the agent has its own tool inventory (`Write`, `Edit`, `Bash`, MCP
file ops) that runs in the gateway's process context, not the
addon's. None of those calls go through the addon dispatcher; the
addon cannot gate them. Mitigation is operator-side:

- Curate per-agent tool inventory in `openclaw.json` so destructive
  gateway tools aren't in the agent the household uses.
- System prompt instructions for the agent (prompt-injectable,
  weak).

Documented prominently in user-facing README + INSTALL so operators
understand the boundary.

## Open technical question — actor correlation on inbound invokes

The addon receives `node.invoke.request` from the gateway. The
canonical envelope (verified in
`/app/dist/node-registry-D3vmVKIR.js:268-275`) is:

```json
{
  "id": "<request-uuid>",
  "nodeId": "<node-uuid>",
  "command": "ha.call_service",
  "paramsJSON": "<...>",
  "timeoutMs": 60000,
  "idempotencyKey": null
}
```

**No `runId`, no `sessionKey`, no `agentId`, no actor of any kind.**
Only exception: `system.run` specifically gets a `runId` injected
into its `params` via `withSystemRunEventRunId` on the gateway side.

That means today the addon cannot correlate an inbound invoke back
to the HA user that originated the conversation turn. Without that
correlation, step 2 of the mechanism above cannot fire.

**Three resolution paths**, ordered by preference:

1. **Find an addon-only correlation surface.** Investigate whether
   HA's `Conversation` integration provides a stable mapping from
   the in-flight `chat.send` to a turn id the addon can stash, and
   whether the addon could correlate via a turn-serial counter
   (only one turn per HA `conversation_id` is in flight at a time
   by HA's contract). If yes, addon-only is feasible by keying on
   `(conversation_id, turn_serial)` instead of `runId`.
2. **Stash actor on the most recently-seen chat.send for this
   session and trust monotonicity.** Workable for typical HA
   Assist turns (one user, one in-flight turn per session), brittle
   if HA Assist ever overlaps turns or restarts. Defense-in-depth
   only; needs a clear bail-out.
3. **Gateway PR** to add `sessionKey` (or `runId`) to the invoke
   envelope. Strictly violates the "addon-only" rule for this
   project but is the cleanest end-state. Smallest possible
   gateway change: one field added to a payload that already
   exists.

**This doc commits to options 1 + 2 first, with option 3 as the
fallback if neither lands cleanly.** The investigation of (1) and
(2) is the next thing to do after this doc merges; the
implementation order below assumes correlation is solved.

## Implementation order (post correlation-investigation)

1. **Shim** — forward `actor: {user_id, is_admin}` on
   `/v1/conversation/stream`. Voice / unbound turns omit. Tests
   with mocked `hass.auth.async_get_user`.
2. **Addon options schema** — add `identity.super_admins: []` to
   `addon/config.yaml`.
3. **Addon ChatRelay** — stash actor at chat.send time keyed by
   the correlation surface chosen above.
4. **Addon dispatcher** — static rule table + service-domain
   policy + dispatcher gate before handler invocation. Regression
   test per row. Includes a probe with a forged actor (addon
   should not accept actor data from anywhere except the shim
   path).
5. **Tier B commands** — `ha.addon_start`/`stop`/`restart`
   register against the existing dispatcher and the admin gate
   applies automatically.
6. **Docs** — update `docs/COMMAND-TIERS.md` to reference this
   doc for the gate mechanism (drop `OPENCLAW_ADMIN_TOKEN`).
   README + INSTALL: operator-facing `identity:` block + the
   self-modification caveat (concern A out-of-scope here).

## References

- `docs/TODO.md` — TODO #1 (user identity propagation)
- `docs/COMMAND-TIERS.md` — Tier A/B/C policy this design feeds the gate mechanism for
- `/app/dist/node-registry-D3vmVKIR.js:268-275` — canonical `node.invoke.request` envelope (no actor today)
