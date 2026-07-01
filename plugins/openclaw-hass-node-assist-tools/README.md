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

**Implemented.** 28 `ha_*` tools are registered in `index.ts` (PRs #206/#207
landed the wrappers). The manifest (`openclaw.plugin.json`) declares the full
tool surface and per-node config schema.

The plugin is `enabledByDefault: false`. Operators must explicitly enable it
and provide per-node policy configuration (allowServices, allowReadEntities,
allowCalendars, allowAdminOps/adminToken for Tier B) before any `ha_*` tool
is usable in Assist turns.

## Layout

```
plugins/openclaw-hass-node-assist-tools/
├── openclaw.plugin.json     # manifest; declares all 28 tools in contracts.tools
├── package.json
├── index.ts                 # plugin entry; lazy-registers all 28 ha_* tools
├── src/
│   ├── tools/
│   │   ├── descriptors.ts                  # TypeBox schemas + tool metadata (28 descriptors)
│   │   ├── ha-call-service-tool.ts
│   │   ├── ha-get-state-tool.ts
│   │   ├── ha-list-states-tool.ts
│   │   ├── ha-calendar-get-events-tool.ts
│   │   ├── ha-list-areas-tool.ts
│   │   ├── ha-list-devices-tool.ts
│   │   ├── ha-list-entity-registry-tool.ts
│   │   ├── ha-simple-read-tools.ts         # list_services, get_config, list_events, list_config_entries, list_automations, check_config, core_logs, addon_logs, list_addons, addon_info, addon_stats, addon_changelog, addon_documentation
│   │   ├── ha-entity-scoped-read-tools.ts  # logbook, history
│   │   ├── ha-light-tools.ts               # light_turn_on, light_turn_off
│   │   └── ha-admin-tools.ts               # reload_config, addon_start, addon_stop, addon_restart (Tier B)
│   └── shared/
│       └── lazy-node-invoke-policy.ts      # per-node policy resolver
└── README.md
```
