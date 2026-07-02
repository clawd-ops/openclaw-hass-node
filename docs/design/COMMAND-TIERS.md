# Command tier policy (addon-management surface)

Addon-management commands are grouped by blast radius. Tier A is the
only tier subagents are ever allowed to call. Tier B is operator-only
behind an admin token. Tier C is explicitly out of scope.

This file replaces the old `HANDOFF-2026-06-20-addon-command-surface.md`,
which was deleted in the pre-1.0 doc sweep. The policy survives the
handoff — capture it here so the agent can find it.

## Why the tiering

TODO item #11 (sunset the HA MCP path → node-tool surface) commits the
subagent-callable tool surface to being **software-blocked** read-only —
not just prompt-instructed. Mixing state-changing commands into the same
surface defeats that property. So commands are grouped by risk tier and
the subagent allowlist only ever includes Tier A.

## Tier A — read-only, subagent-safe

No gate beyond "must be invokable by the node". Live on the subagent
allowlist once #11's subagent-side enforcement lands.

Shipped:

- `ha.addon_logs` — `GET /addons/<slug>/logs`
- `ha.list_addons` — `GET /addons` (discovery path for the slug-required commands)
- `ha.addon_info` — `GET /addons/<slug>/info` (options / schema / repository / capability flags **stripped at the boundary**)
- `ha.addon_stats` — `GET /addons/<slug>/stats`
- `ha.addon_changelog` — `GET /addons/<slug>/changelog`
- `ha.addon_documentation` — `GET /addons/<slug>/documentation`

Every Tier A command MUST:

- Use `supervisor_get_text` / `supervisor_get_json` (URL is always built
  as `http://supervisor{path}` — never combined with a user-supplied URL).
- Apply a fixed field allowlist before returning. Never `return raw`
  (`ha.list_addons`'s allowlist is the canonical example).
- Validate the slug against the existing `_valid_addon_slug` rule.
- Cap response size (`supervisor_get_text` keeps a bounded 1 MiB tail).

### Open question on Tier A — reading addon `options`

Several legitimate Tier A use cases want to *see* an addon's `options`
(e.g. "what `hass_url` is this addon configured with?"). Current
allowlist drops `options` entirely to avoid leaking secrets. Two
choices when the next iteration happens:

1. **Drop options entirely (status quo).** Safest. Subagents lose
   option-introspection.
2. **Return option keys only, never values.** Reveals schema, hides
   secrets. Likely the right balance.

Decide before iterating on `ha.addon_info`.

## Tier B — lifecycle, admin-gated, NEVER on the subagent allowlist

Reserved for the primary agent or Rob himself. Same `OPENCLAW_ADMIN_TOKEN`
gate as `ha.reload_config`.

Shipped surface:

- `ha.addon_start` — `POST /addons/<slug>/start`
- `ha.addon_stop` — `POST /addons/<slug>/stop`
- `ha.addon_restart` — `POST /addons/<slug>/restart`
- `ha.addon_update` — `POST /addons/<slug>/update`; updates to the latest available version

Additional constraints on top of the admin-token gate:

- **Slug allow/deny list at addon-config level.** Always deny
  `homeassistant`, `supervisor`, and `core_*` regardless of token.
  Other slugs default-deny via `addon_lifecycle.allowlist`, with an
  optional extra `addon_lifecycle.denylist`.
- **Audit log every invocation** at WARNING with command + slug. Per-HA-user
  actor is currently available only on the Assist ingress, not on the
  `node.invoke.request` envelope for command dispatch.
- **Idempotent shape.** Start-of-already-started and stop-of-already-
  stopped should return `{ok: True, state: "<current>"}` rather than
  surfacing Supervisor's `400`.

## Tier C — install / uninstall / rebuild — NOT adding

Easy to brick the HA instance. Sequencing requires careful UX (read
changelog, preserve options, take a backup, handle migrations). Out
of scope until there's a specific, scoped ask. If a need surfaces,
file a separate proposal — not an opportunistic add.

Note: `ha.addon_update` (update to the latest available version) is intentionally
Tier B rather than Tier C. It does not change which add-on is installed — only
brings an existing installation forward. The same slug denylist and allowlist
gate that governs start/stop/restart applies here.

## Gateway allowlist sync — required, easy to forget

Every new node command must be added to `nodes.allowCommands` in the
operator's private gateway config (NOT in this repo, NOT in any
public repo). Without that entry, the command is registered on the
node but the gateway refuses to dispatch it — the tool effectively
doesn't exist for callers.

Tier B will likely belong on a *separate* node-config admin allowlist
if/when one is introduced, not on `nodes.allowCommands` alongside the
read-only surface. Pin down before implementing.

Each new command also goes in `docs/reference/COMMAND-SURFACE.md` (the canonical
command catalog) in this repo. Doc + allowlist + code go together; PRs
that miss one of the three should be flagged in review.

There is also a per-node `commands` cache in
`~/.openclaw/nodes/paired.json` that is set at original pair time and
is NOT refreshed by WS reconnect — see
`docs/operations/LESSONS.md` → "Gateway caches the node's advertised commands at
pair time". `hassio.addon_restart` (full handshake) is what rewrites
that cache after a release.

## PR cadence

- One Tier A command per PR, each individually reviewable.
- Tier B landed with the admin token gate and slug allowlist in the same
  implementation PR as identity routing.
- Tier C never lands without a fresh, scoped ask.
- Cross-agent code review (Anthropic plans/drives, GPT-5.5 reviews)
  is required for every Tier A and Tier B PR.

## Order of operations for closing TODO #11

1. Ship the remaining Tier A commands (done).
2. Ship the subagent-side allowlist enforcement at the node
   (`commands/dispatcher.py` or a new policy layer) — MUST land
   BEFORE any subagent path is wired to call these commands.
3. Wire the subagent path to use the Tier A surface instead of the
   HA MCP server.
4. Tier B surface with the admin allowlist gate (done in the identity
   routing implementation PR).
