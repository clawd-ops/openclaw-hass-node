# openclaw-hass-node-assist-tools

Scaffold for the OpenClaw gateway plugin that bridges HA Assist sessions to
the paired `openclaw-hass-node-app` command surface.

## Why this plugin exists

OpenClaw 2026.3.31+ keeps node-originated conversation sessions on a
**reduced trusted surface** (see `/app/docs/gateway/pairing.md` — "Node
event trust boundaries"). Concretely, the operator-only `nodes.invoke` tool
is **not** exposed in Assist turns relayed from `openclaw-hass-node-app`.

The agent that handles those Assist turns (Clawd) still has the full skill
catalog including `openclaw-hass-node-skill`, but that skill needs
`nodes.invoke` under the hood. Without it, Clawd-in-Assist knows it should
operate HA but has no path to actually call `ha.*` commands on the bound
node.

This plugin closes that gap the same way OpenClaw core closes the
filesystem gap with `file-transfer` (`/app/extensions/file-transfer/`):
the plugin holds operator privilege, declares scoped per-tool wrappers
(`ha_call_service`, `ha_get_state`, etc.) with per-node config
(`allowServices`, `allowReadEntities`, ...), and those tools surface to
all session types including Assist.

## When it applies

- **Assist sessions** (HA voice/text → OC Gateway → Clawd): require this plugin to operate HA.
- **Chat / cron / sub-agent sessions**: do not require this plugin. They have `nodes.invoke` and use `openclaw-hass-node-skill` on top.

See `docs/design/COMPONENT-NAMING.md` for how this piece fits the full
`openclaw-hass-node-*` bundle.

## Status

**Scaffold only.** The manifest (`openclaw.plugin.json`) declares the
intended tool surface and per-node config schema; concrete implementations
(TypeScript handlers in `src/`, tests, root TS infra, CI lane) land in
follow-up commits on this branch.

## Layout (planned)

```
plugins/openclaw-hass-node-assist-tools/
├── openclaw.plugin.json     # manifest (this commit)
├── package.json             # follow-up
├── index.ts                 # plugin entry; registers tools + node-host commands
├── src/
│   ├── tools/
│   │   ├── descriptors.ts   # TypeBox schemas + tool metadata
│   │   ├── ha-call-service-tool.ts
│   │   ├── ha-get-state-tool.ts
│   │   └── ...one file per tool
│   ├── node-host/           # server-side handlers if any wrappers run partly on the node
│   └── shared/              # lazy-loaders, per-node policy resolvers
└── README.md
```
