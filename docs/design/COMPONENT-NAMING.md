# Component Naming

The OpenClaw + Home Assistant integration is intentionally multi-piece. Every
piece is named with the prefix `openclaw-hass-node-` followed by a role
suffix that says what it is. This keeps logs, PR titles, config keys, and
conversations unambiguous, and lets any piece move to its own repo later
without a rename.

## Pieces

| Name                              | Role                                                              | Lives at (today)                                                |
|-----------------------------------|-------------------------------------------------------------------|------------------------------------------------------------------|
| `openclaw-hass-node-app`          | The Home Assistant **app** (formerly "add-on"). Python service that runs in the HA Supervisor and exposes the `ha.*` / `fs.*` / `system.*` node command surface over the gateway WebSocket. | This repo, `addon/` directory (kept as-is for now to avoid churn). |
| `openclaw-hass-node-integration`  | The HACS **integration** (custom_component). Registers the OC conversation entity inside HA core and proxies turns to the app over HTTP. | This repo, `custom_components/openclaw_gateway/`.                |
| `openclaw-hass-node-plugin`       | The OpenClaw **gateway plugin**. Declares scoped tools (`ha_call_service`, `ha_get_state`, etc.) that wrap node commands so Assist sessions can operate the bound HA node without needing the (operator-only) generic `nodes.invoke` tool. | This repo, `plugins/openclaw-hass-node-plugin/` (new).           |
| `openclaw-hass-node-skill`        | The companion **skill** that documents how Clawd should use the surface. | OpenClaw skill registry (`home-assistant-node`); to be renamed to `openclaw-hass-node-skill` on the next skill touch for full symmetry. |

## Why "app" and not "addon"

Home Assistant has shifted terminology from "add-on" toward "app" in newer
docs and UI. We adopt `-app` for the role suffix to track upstream language.
The on-disk directory keeps its existing `addon/` name for now to avoid
churning install paths, build scripts, and CI lanes; the rename happens when
there's a natural reason to touch those paths.

This is independent of HACS. HACS is just the distribution channel for the
integration piece. The HA-side terminology shift to "app" applies to the
Supervisor-installed service, regardless of whether HACS is involved.

## Why the gateway plugin is mandatory

OpenClaw 2026.3.31+ keeps node-originated conversation sessions on a
"reduced trusted surface" (see `/app/docs/gateway/pairing.md` "Node event
trust boundaries"). Generic `nodes.invoke` is operator-only and is **not**
exposed to Assist turns relayed from a node. Without
`openclaw-hass-node-plugin`, Assist on the HA node has memory, identity, and
Skill access but cannot call any `ha.*` or `fs.*` command against its own
node — making the bundle unusable for actually operating HA.

The plugin pattern is the same one OpenClaw core already uses for
`file-transfer` (`/app/extensions/file-transfer/`): the plugin holds
operator privilege, declares specific scoped tools with per-node config
(`allowServices`, `allowReadEntities`, etc.), and surfaces those tools to
all session types including node-originated Assist.

## Future repo split

The unified naming is deliberately repo-split-safe. If/when any piece
warrants its own repository (independent release cadence, separate
maintainers, etc.), it can move to `clawd-ops/openclaw-hass-node-<role>`
with the existing id intact. Suggested split order if it ever happens:

1. `openclaw-hass-node-skill` first (cleanest separation; just a markdown bundle).
2. `openclaw-hass-node-plugin` next (well-scoped, depends only on the OC plugin SDK and the app's declared node command surface).
3. `openclaw-hass-node-integration` only if HACS distribution constraints force it.
4. `openclaw-hass-node-app` last, with the others depending on its versioned command surface.

Until there's a real reason, keep all pieces in this repo: cross-cutting
changes (new `ha.*` command in the app → matching tool wrapper in the
plugin) land as one PR, one review, one release, with implicit version
pinning.

## When adding a new piece

If a new component joins the bundle, name it `openclaw-hass-node-<role>`
where `<role>` is the smallest noun that says what it is in the same
vocabulary the host platform uses (HA → app/integration; OpenClaw →
plugin/skill). Document it in the table above in the same PR.
