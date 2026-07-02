# openclaw-hass-node-assist-tools

OpenClaw gateway plugin that bridges HA Assist sessions to the paired
`openclaw-hass-node-app` command surface.

## Why this plugin exists

OpenClaw 2026.3.31+ keeps node-originated conversation sessions on a
**reduced trusted surface** (see `/app/docs/gateway/pairing.md` — "Node
event trust boundaries"). Concretely, the operator-only `nodes.invoke` tool
is **not** exposed in Assist turns relayed from `openclaw-hass-node-app`.

The agent that handles those Assist turns still has the full skill
catalog including `openclaw-hass-node-skill`, but that skill needs
`nodes.invoke` under the hood. Without it, the agent-in-Assist knows it should
operate HA but has no path to actually call `ha.*` commands on the bound
node.

This plugin closes that gap the same way OpenClaw core closes the
filesystem gap with `file-transfer` (`/app/extensions/file-transfer/`):
the plugin holds operator privilege and declares scoped per-tool wrappers
(`ha_call_service`, `ha_get_state`, etc.) that surface to all session types
including Assist.

## When it applies

- **Assist sessions** (HA voice/text → OC Gateway → the agent): require this plugin to operate HA.
- **Chat / cron / sub-agent sessions**: do not require this plugin. They have `nodes.invoke` and use `openclaw-hass-node-skill` on top.

See `docs/design/COMPONENT-NAMING.md` for how this piece fits the full
`openclaw-hass-node-*` bundle.

## Status

**Implemented.** 28 `ha_*` tools are registered in `index.ts`. The manifest
(`openclaw.plugin.json`) declares the full tool surface and per-node config schema.

The plugin is `enabledByDefault: false`. Operators must explicitly enable it.
No per-node allowlists are required — entity/service/calendar access control
lives at the hass node's tier/allowCommands + HA's own auth layer.

## Per-node config

Config lives at `plugins.entries.openclaw-hass-node-assist-tools.config.nodes.<nodeId>`.

**Routing-only design**: the plugin does not enumerate entities, services, or
calendars. Access control is delegated entirely to the hass node's
`allowCommands` tier policy and HA's own auth. The only plugin-scoped config
is the Tier B admin gate:

```json
{
  "plugins": {
    "entries": {
      "openclaw-hass-node-assist-tools": {
        "enabled": true,
        "config": {
          "nodes": {
            "hass": {}
          }
        }
      }
    }
  }
}
```

### Tier B admin ops (optional)

To enable `ha_reload_config`, `ha_addon_start`, `ha_addon_stop`,
`ha_addon_restart`, `ha_addon_update`, set both flags under the node:

```json
"nodes": {
  "hass": {
    "allowAdminOps": true,
    "adminToken": "<shared secret from hass node admin config>"
  }
}
```

`adminToken` is a shared secret between the plugin and the node's admin
surface — it is NOT the HA long-lived access token. It is injected by the
plugin and the caller cannot override it.

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
│   │   └── ha-admin-tools.ts               # reload_config, addon_start, addon_stop, addon_restart, addon_update (Tier B)
│   └── shared/
│       ├── node-invoke-policy.ts           # routing-only invoke policy with param validation
│       ├── per-node-policy.ts              # PerNodePolicy type (admin gate only)
│       └── lazy-node-invoke-policy.ts      # command allowlist
└── README.md
```

## Local install

Standard `openclaw plugins install` is blocked by two packaging gaps
(missing `dist/`, pnpm workspace symlinks). Use the included workaround:

```sh
bash scripts/install-plugin-local.sh
```

See `docs/known-workarounds.md` for details and the upstream fix tracker.

## SDK API contract

This plugin is built against the OpenClaw plugin SDK. The surface it relies
on is narrow. If an SDK upgrade breaks the plugin, check these first:

### Entry point (`openclaw/plugin-sdk/plugin-entry`)

```ts
import {
  definePluginEntry,
  type AnyAgentTool,
  type OpenClawPluginNodeInvokePolicy,
  type OpenClawPluginNodeInvokePolicyContext,
  type OpenClawPluginNodeInvokePolicyResult,
} from "openclaw/plugin-sdk/plugin-entry";
```

- **`definePluginEntry(descriptor)`** — registers the plugin with the gateway.
  Required fields: `id` (string), `name` (string), `description` (string),
  `register(api)` (function).
- **`api.registerTool(tool: AnyAgentTool)`** — registers a tool that surfaces
  in Assist sessions. `AnyAgentTool` requires `label`, `name`, `description`,
  `parameters` (TypeBox schema), and `execute(toolCallId, args, signal, onUpdate)`.
- **`api.registerNodeInvokePolicy(policy: OpenClawPluginNodeInvokePolicy)`** —
  registers the security gate for raw `node.invoke` calls. Required shape:
  `{ commands: string[], handle(ctx): Promise<result> }`.
- **`OpenClawPluginNodeInvokePolicyContext`** — the `ctx` argument passed to
  `policy.handle`:
  - `ctx.command` — the `ha.*` command being invoked
  - `ctx.nodeId` — the paired node's ID
  - `ctx.params` — raw params from the caller (validate before forwarding)
  - `ctx.pluginConfig` — plugin config object (resolved at call time by the
    gateway via `plugin-config-runtime`; may be `undefined` if not configured)
  - `ctx.invokeNode({ params })` — forwards the call to the node after policy
    check passes; returns `{ ok, payload?, error? }`

### Config reader (`openclaw/plugin-sdk/plugin-config-runtime`)

```ts
import { resolvePluginConfigObject } from "openclaw/plugin-sdk/plugin-config-runtime";
```

`ctx.pluginConfig` in the policy context is already the resolved config object
for this plugin. For tool handlers that need the config at execute time, use
`callGatewayTool("config.get", opts, {})` then `resolvePluginConfigObject(result?.payload, PLUGIN_ID)`.
The old `readPluginConfig` from `openclaw/plugin-sdk/plugin-config` is removed.

### What to check on SDK upgrades

1. `definePluginEntry` signature and `register(api)` argument shape
2. `AnyAgentTool.execute` parameter order and types
3. `OpenClawPluginNodeInvokePolicy` — `commands` array still accepted, `handle`
   still receives `OpenClawPluginNodeInvokePolicyContext` with `invokeNode`
4. `plugin-config-runtime` — `resolvePluginConfigObject` still resolves the
   plugin's own config subtree from the raw gateway config payload
5. `ctx.invokeNode` return shape (`{ ok: boolean, payload?, error? }`) — the
   policy handlers forward it directly to callers
