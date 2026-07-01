# Component Naming

The OpenClaw + Home Assistant integration is intentionally multi-piece. Every
piece is named with the prefix `openclaw-hass-node-` followed by a role
suffix that says what it is. This keeps logs, PR titles, config keys, and
conversations unambiguous, and lets any piece move to its own repo later
without a rename.

## Pieces

| Name                                  | Role                                                              | Lives at (today)                                                 |
|---------------------------------------|-------------------------------------------------------------------|------------------------------------------------------------------|
| `openclaw-hass-node-app`              | The Home Assistant **app** (formerly "add-on"). Python service that runs in the HA Supervisor and exposes the `ha.*` / `fs.*` / `system.*` node command surface over the gateway WebSocket. | This repo, `app/` directory (renamed from `addon/` on 2026-07-01). |
| `openclaw-hass-node-assist`           | The HACS **integration**. Registers the OpenClaw conversation entity inside HA core and proxies turns to the app over HTTP. The piece that makes Assist see "OpenClaw HA Node — Assist" as a selectable conversation agent. | This repo, `custom_components/openclaw_hass_node_assist/`. HACS domain: `openclaw_hass_node_assist`. |
| `openclaw-hass-node-assist-tools`     | The OpenClaw **gateway plugin**. Declares scoped tools (`ha_call_service`, `ha_get_state`, etc.) that wrap `ha.*` / `fs.*` node commands so Assist sessions can operate the bound HA node. **Assist-only**: every other OC session (chat, cron, sub-agent) has the operator-only `nodes.invoke` tool and uses the `openclaw-hass-node-skill` skill on top of it. | This repo, `plugins/openclaw-hass-node-assist-tools/` (new). |
| `openclaw-hass-node-skill`            | The companion **skill** used by every non-Assist session (chat, cron, sub-agent) to drive `ha.*` / `fs.*` via `nodes.invoke`. | This repo, `skills/openclaw-hass-node-skill/`. |

## Why "app" and not "addon"

Home Assistant has shifted terminology from "add-on" toward "app" in newer
docs and UI. We adopt `-app` for the role suffix to track upstream language.
The on-disk directory was renamed from `addon/` to `app/` on 2026-07-01 to
match. Existing installs must reinstall the app under the new Supervisor
build path — clean break, no back-compat alias (per the alpha-project rule).
This is independent of HACS — HACS is just the distribution channel for the
`-assist` integration.

## Why `-assist` and not `-integration`

"Integration" is HA's generic noun for any custom_component. The actual role
of this piece is narrow: it creates the Assist conversation layer and
proxies turns to the app. Naming it `-assist` says exactly that and pairs
naturally with `-assist-tools` on the OC side.

## Why the OC plugin is `-assist-tools` (Assist-only)

Every OC session has either `nodes.invoke` (operator/chat/cron/sub-agent)
or it doesn't (Assist, per OC's "reduced trusted surface" hardening for
node-originated sessions — see `/app/docs/gateway/pairing.md` *Node event
trust boundaries*). Sessions that have `nodes.invoke` drive HA through the
`openclaw-hass-node-skill` skill; they don't need the plugin's wrappers.
Sessions that don't (Assist) need exactly this plugin's per-tool wrappers
to operate HA at all. That's why the suffix is `-assist-tools`: the tools
half of the Assist feature.

The plugin pattern is the same one OpenClaw core already uses for
`file-transfer` (`/app/extensions/file-transfer/`): the plugin holds
operator privilege, declares specific scoped tools with per-node config
(`allowServices`, `allowReadEntities`, etc.), and surfaces those tools to
all session types including node-originated Assist.

## HACS domain

The HACS custom_component uses the domain `openclaw_hass_node_assist` (renamed
from the earlier `openclaw_gateway`). Clean break — no backwards-compat
alias — because the only existing install was Rob's and a fresh setup was
acceptable. External users will only ever see the new domain.

## Future repo split

The unified naming is deliberately repo-split-safe. If/when any piece
warrants its own repository (independent release cadence, separate
maintainers, etc.), it can move to `clawd-ops/openclaw-hass-node-<role>`
with the existing id intact. Suggested split order if it ever happens:

1. `openclaw-hass-node-skill` first (cleanest separation; just a markdown bundle).
2. `openclaw-hass-node-assist-tools` next (well-scoped, depends only on the OC plugin SDK and the app's declared node command surface).
3. `openclaw-hass-node-assist` only if HACS distribution constraints force it.
4. `openclaw-hass-node-app` last, with the others depending on its versioned command surface.

Until there's a real reason, keep all pieces in this repo: cross-cutting
changes (new `ha.*` command in the app → matching tool wrapper in the
plugin) land as one PR, one review, one release, with implicit version
pinning.

## When adding a new piece

If a new component joins the bundle, name it `openclaw-hass-node-<role>`
where `<role>` is the smallest noun that says what it is in the vocabulary
of the host platform (HA → `app` / `assist`; OpenClaw → `assist-tools` /
`skill`). Document it in the table above in the same PR.
