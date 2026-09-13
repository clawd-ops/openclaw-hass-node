# Command tier policy (addon-management surface)

Addon-management commands are grouped by blast radius. Tier A is the
only tier subagents are ever allowed to call. Tier B is operator-only.
Lifecycle commands use the paired operator boundary plus explicit slug
policy; separate admin effects require operator approval. Tier C is
explicitly out of scope.

This file replaces the old `HANDOFF-2026-06-20-addon-command-surface.md`,
which was deleted in the pre-1.0 doc sweep. The policy survives the
handoff — capture it here so the agent can find it.

## Why the tiering

The completed HA MCP sunset established the node-tool surface as the supported
path. Independently, the subagent-callable surface must be **software-blocked**
read-only, not just prompt-instructed. Mixing state-changing commands into the
same surface defeats that property, so commands are grouped by risk tier and
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

Released in `2026.9.13b1`, but not yet observed in the installed environment:

- `ha.supervisor_info` — `GET /info` (allowlisted host/Supervisor versions and
  architecture; `hostname`, `timezone`, and network fields excluded, and
  non-scalar values dropped so nothing rides through nested)

Every Tier A command MUST:

- Use `supervisor_get_text` / `supervisor_get_json` (URL is always built
  as `http://supervisor{path}` — never combined with a user-supplied URL).
- Apply a fixed field allowlist before returning. Never `return raw`
  (`ha.list_addons`'s allowlist is the canonical example).
- Validate the slug against the existing `_valid_addon_slug` rule **when the
  command takes a slug**. A parameterless command such as `ha.supervisor_info`
  has no caller input to validate; that is a stronger position, not an exemption.
- Cap response size (`supervisor_get_text` keeps a bounded 1 MiB tail).
- Never return an upstream error body verbatim when the response is a
  confidentiality boundary. `supervisor_get_json` embeds up to 512 bytes of the
  upstream body in `HAClientError.message`, which can name the host, so return
  fixed text and keep only the error code.

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

## Tier B — lifecycle + admin, NEVER on the subagent allowlist

Reserved for the primary agent or Rob himself. Tier B has two
authorization levels (#262 reconciliation). Neither uses an add-on admin
token; see [Authorization model](AUTHORIZATION-MODEL.md).

### Tier B lifecycle (pairing auth + slug policy)

Authenticated by the established pairing session. The node checks
slug allowlist/denylist policy. The plugin requires `allowAdminOps` only.

- `ha.addon_start` — `POST /addons/<slug>/start`
- `ha.addon_stop` — `POST /addons/<slug>/stop`
- `ha.addon_restart` — `POST /addons/<slug>/restart`
- `ha.addon_update` — `POST /addons/<slug>/update`; updates to the latest available version (Supervisor API, slug-based)

### Tier B admin (operator approval required)

No add-on admin token exists. These commands require `allowAdminOps` plus a
resolved OpenClaw plugin permission request, the same gate used for other
Home Assistant mutations. See
[Authorization model](AUTHORIZATION-MODEL.md).

- `ha.reload_config` — `POST /api/services/homeassistant/reload_core_config`;
  reloads the HA core configuration only. The optional `domain` argument may
  be omitted, blank, or `core`; all three select the core reload. Any other
  domain is rejected with `UNSUPPORTED` before HA I/O. Per-domain reloads are
  not implemented and remain pending the effect policy.
- `ha.update_install` — `POST /api/services/update/install`; installs a pending HA update via the `update.*` entity domain (covers HACS integrations, HA Core, add-ons as entities). Entity ID must be in the `update.` domain.

Additional constraints on lifecycle ops (on top of the `allowAdminOps` gate):

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

Note: `ha.addon_update` and `ha.update_install` are intentionally Tier B rather
than Tier C. Both bring existing installations forward without changing which
component is installed. `ha.update_install` is entity-scoped (must be `update.*`)
and is gated by operator approval without a slug allowlist, since update
entities are HA-registry objects rather than Supervisor slugs.

## Gateway allowlist sync — required, easy to forget

Every new node command must be added to `gateway.nodes.commands.allow`
in the operator's private gateway config (NOT in this repo, NOT in any
public repo). Without that entry, the command is registered on the
node but the gateway refuses to dispatch it — the tool effectively
doesn't exist for callers.

Tier B will likely belong on a *separate* node-config admin allowlist
if/when one is introduced, not on `gateway.nodes.commands.allow`
alongside the read-only surface. Pin down before implementing.

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
- Tier B initially landed with an admin token gate and slug allowlist. Current
  lifecycle authorization uses the paired session plus slug policy without a
  separate token. `ha.reload_config` and `ha.update_install` still *carry* the
  admin-token check in code, but that gate is inert because the variable cannot
  be set, so both commands are unreachable. The ratified target removes the
  token from those two commands and gates them on operator approval instead.
  See [Authorization model](AUTHORIZATION-MODEL.md).
- Tier C never lands without a fresh, scoped ask.
- Cross-agent code review (Anthropic plans/drives, GPT-5.5 reviews)
  is required for every Tier A and Tier B PR.

## Independent follow-on policy work

1. Ship the remaining Tier A commands (done).
2. Ship the subagent-side allowlist enforcement at the node
   (`commands/dispatcher.py` or a new policy layer) — MUST land
   BEFORE any subagent path is wired to call these commands.
3. Keep subagent access on the Tier A node surface.
4. Tier B lifecycle surface uses the paired session plus slug policy without a
   separate token; the contract was reconciled under issue #262.
