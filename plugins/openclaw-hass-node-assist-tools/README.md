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

**Implemented.** 30 `ha_*` tools are declared in the manifest
(`openclaw.plugin.json`) with the corresponding registrations in
`index.ts`. The plugin exposes read/observability + `ha_call_service` +
Tier B lifecycle and admin wrappers. Lifecycle operations require the paired
node boundary, `allowAdminOps`, and the node's lifecycle slug policy, without
another token. `ha_reload_config` and `ha_update_install` remain separate admin
operations that also require `adminToken`. The plugin does **not** expose the
`ha.config.*` domain-config editors (lovelace, automation, script,
scene, helpers, area/device/entity registries, config_entries). Those
are proposal-gated mutations meant for chat/cron/sub-agent flows via
`nodes.invoke`, not for HA Assist voice turns.

The plugin is `enabledByDefault: false`. Operators must explicitly enable it.
No per-node allowlists are required — entity/service/calendar access control
lives at the hass node's tier/allowCommands + HA's own auth layer.

## Unreleased service/result repair (#266)

`ha_call_service` now accepts canonical `data` and the compatibility alias
`service_data`. Equal aliases are accepted; conflicts or non-object payloads
fail before dispatch. The wrapper sends `data` to the node. Shared invoke
handling rejects node/HA errors, including failures inside older successful
envelopes, so failed operations do not produce success text. Authoritative
gateway refusals are distinguished from local/socket failures, retaining the
gateway code, details, and supplied retryability metadata. This is not a
per-service authorization policy; that work remains outstanding.

The TypeScript suite includes a real wrapper-to-Python-dispatcher contract test.
From the repository root run `uv sync --package openclaw-node` before
`pnpm test`; the fixture uses that `.venv/bin/python`. HA I/O is mocked and no
live HA or gateway is needed. It also checks the exact nested changed-state
payload rendered by the wrapper. CI installs Python 3.13 and runs this contract
when either language changes. See the [command reference](../../docs/reference/COMMAND-SURFACE.md#service-payload-and-result-contract-unreleased-266)
for compatibility and failure semantics.

## Automation narrowing

The `ha_list_automations` Assist tool supports server-side narrowing before it
optionally fetches traces:

- `entity_filter` is an `fnmatch`-style glob over `entity_id`. It must be a
  non-empty string beginning with `automation.` and is limited to 256
  characters. A literal entity ID such as `automation.morning` is also valid.
- `state_filter` is a non-empty string matched exactly against the automation's
  state, commonly `on` or `off`.
- `include_traces` defaults to `false`. When true, traces are requested only for
  automations that remain after both filters are applied.

Supplied strings are forwarded without trimming or coercion. Empty or
wrong-typed values are rejected by the tool schema or node instead of silently
turning them into an unfiltered request. The node also rejects unknown
parameters with `INVALID_PARAM`.

For example, `entity_filter: "automation.morning_*"` with
`state_filter: "on"` returns only enabled matching automations. A valid filter
with no matches returns `count: 0` and an empty `automations` list.

## Per-node config

Config lives at `plugins.entries.openclaw-hass-node-assist-tools.config.nodes.<nodeId>`.

**Routing-only design**: the plugin does not enumerate entities, services, or
calendars. Access control is delegated entirely to the hass node's
`allowCommands` tier policy and HA's own auth. The only plugin-scoped config
is the Tier B gate:

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

### Tier B operations (optional)

Set `allowAdminOps: true` to enable `ha_addon_start`, `ha_addon_stop`,
`ha_addon_restart`, and `ha_addon_update`. The node also requires the target
slug in `addon_lifecycle.allowlist` and always denies `homeassistant`,
`supervisor`, and `core_*`:

```json
"nodes": {
  "hass": {
    "allowAdminOps": true,
    "adminToken": "<shared secret for reload_config and update_install only>"
  }
}
```

`adminToken` is optional unless `ha_reload_config` or `ha_update_install` is
used. For those two admin operations it is a shared secret between the plugin
and the node's admin surface. It is not the HA long-lived access token. The
plugin injects it and the caller cannot override it. Lifecycle operations do
not send or consult it.

## Layout

```
plugins/openclaw-hass-node-assist-tools/
├── openclaw.plugin.json     # manifest; declares all 30 tools in contracts.tools
├── package.json
├── index.ts                 # plugin entry; lazy-registers all 30 ha_* tools
├── src/
│   ├── tools/
│   │   ├── descriptors.ts                  # TypeBox schemas + tool metadata (30 descriptors)
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
│   │   └── ha-admin-tools.ts               # reload_config, addon_start, addon_stop, addon_restart, addon_update, update_install (Tier B)
│   └── shared/
│       ├── node-invoke-policy.ts           # routing-only invoke policy with param validation
│       ├── per-node-policy.ts              # PerNodePolicy type (lifecycle enablement + separate admin token)
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
