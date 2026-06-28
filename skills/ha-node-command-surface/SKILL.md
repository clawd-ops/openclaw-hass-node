---
name: "ha-node-command-surface"
description: "Runbook for using HA node commands safely instead of legacy HA MCP tools."
status: proposal
version: "v2"
date: "2026-06-28T15:33:33.613Z"
---

# HA Node Command Surface Runbook

Use this skill when a task needs Home Assistant data or action through OpenClaw's `hass` node, when replacing a legacy `mcp__homeassistant*` call, or when wiring subagents to HA access.

The practical rule: use `nodes.invoke` against node `hass` with an `ha.*` command when the command exists. Treat the old HA MCP tools as legacy fallback only during explicit migration/debugging work.

## Quick Start

Use the node command surface like this:

```json
{
  "nodeId": "hass",
  "command": "ha.get_state",
  "params": {
    "entity_id": "sensor.example"
  }
}
```

For broad discovery, start read-only:

```json
{
  "nodeId": "hass",
  "command": "ha.list_states",
  "params": {}
}
```

For add-on logs:

```json
{
  "nodeId": "hass",
  "command": "ha.addon_logs",
  "params": {
    "slug": "<addon_slug>",
    "lines": 200
  }
}
```

If a command fails because it is not advertised or allowed, check the node command cache and gateway allowlist before assuming the command does not exist.

## Source Of Truth

Before changing command behavior, allowlists, or subagent wiring, read only the repo docs relevant to the task:

- `/home/openclaw/.openclaw/projects/openclaw-hass-node/docs/reference/COMMAND-SURFACE.md` for the live command catalog.
- `/home/openclaw/.openclaw/projects/openclaw-hass-node/docs/design/COMMAND-TIERS.md` for Tier A/B/C policy.
- `/home/openclaw/.openclaw/projects/openclaw-hass-node/docs/research/MIGRATION.md` when replacing MCP usage.
- `/home/openclaw/.openclaw/projects/openclaw-hass-node/docs/TODO.md` for current migration state.

If docs and code disagree, inspect `addon/node/src/openclaw_node/commands/dispatcher.py` and the command module, then fix docs and code together.

## Command Map

Use these `ha.*` commands for common HA work:

| Task | Node command | Params |
| --- | --- | --- |
| List all entity states | `ha.list_states` | `{}` |
| Get one entity | `ha.get_state` | `{ "entity_id": "sensor.x" }` |
| List services | `ha.list_services` | `{}` |
| Call service | `ha.call_service` | `{ "domain": "light", "service": "turn_on", "target": {...}, "data": {...} }` |
| List areas | `ha.list_areas` | `{}` |
| List devices | `ha.list_devices` | `{}` |
| List entity registry | `ha.list_entity_registry` | `{}` |
| Light on | `ha.light_turn_on` | `entity_id` or `area_id` or `device_id` |
| Light off | `ha.light_turn_off` | `entity_id` or `area_id` or `device_id` |
| Logbook | `ha.logbook` | `entity_id?`, `start?`, `end?` |
| History | `ha.history` | `entity_id?`, `start?`, `end?` |
| List automations | `ha.list_automations` | `{ "include_traces": true? }` |
| Check HA config | `ha.check_config` | `{}` |

Addon read-only commands:

| Task | Node command | Params |
| --- | --- | --- |
| Add-on logs | `ha.addon_logs` | `{ "slug": "...", "lines": 200 }` |
| List add-ons | `ha.list_addons` | `{}` |
| Add-on info | `ha.addon_info` | `{ "slug": "..." }` |
| Add-on stats | `ha.addon_stats` | `{ "slug": "..." }` |
| Add-on changelog | `ha.addon_changelog` | `{ "slug": "..." }` |
| Add-on docs | `ha.addon_documentation` | `{ "slug": "..." }` |

Operator-only commands:

| Task | Node command | Required gate |
| --- | --- | --- |
| Reload HA config domain | `ha.reload_config` | `admin_token` |
| Start add-on | `ha.addon_start` | `admin_token` + add-on lifecycle allowlist |
| Stop add-on | `ha.addon_stop` | `admin_token` + add-on lifecycle allowlist |
| Restart add-on | `ha.addon_restart` | `admin_token` + add-on lifecycle allowlist |

## Legacy MCP Replacement

When replacing old Home Assistant MCP usage, map intent to the node command:

- `mcp__homeassistant*_ha_get_state` -> `ha.get_state`
- `mcp__homeassistant*_ha_list_states` -> `ha.list_states`
- `mcp__homeassistant*_ha_list_services` -> `ha.list_services`
- `mcp__homeassistant*_ha_list_areas` -> `ha.list_areas`
- `mcp__homeassistant*_ha_list_devices` -> `ha.list_devices`
- `mcp__homeassistant*_ha_list_entity_registry` -> `ha.list_entity_registry`
- `mcp__homeassistant*_ha_call_service` -> `ha.call_service`
- `mcp__homeassistant*_ha_light_turn_on` -> `ha.light_turn_on`
- `mcp__homeassistant*_ha_light_turn_off` -> `ha.light_turn_off`

For add-on inspection, use the Tier A `ha.addon_*` node commands rather than trying to expose Supervisor access through MCP.

## Subagent Rules

Subagents may use only Tier A read-only add-on commands unless Rob explicitly authorizes a broader operator-controlled path.

Allowed for subagents after software allowlist enforcement lands:

- `ha.addon_logs`
- `ha.list_addons`
- `ha.addon_info`
- `ha.addon_stats`
- `ha.addon_changelog`
- `ha.addon_documentation`

Never put these on the subagent allowlist:

- `ha.addon_start`
- `ha.addon_stop`
- `ha.addon_restart`
- `ha.reload_config`
- any future install, uninstall, update, rebuild, or write command

Prompt instructions are not enough. The node or gateway path must software-block subagents from non-Tier-A commands.

## Safety Checklist For Command Changes

When adding, changing, or wiring a command:

1. Confirm registration in `commands/dispatcher.py`.
2. Confirm docs in `docs/reference/COMMAND-SURFACE.md`.
3. Confirm tier policy in `docs/design/COMMAND-TIERS.md`.
4. Confirm gateway/operator allowlists where required.
5. Add tests for happy path, malformed input, permission denial, and secret/privacy filtering.
6. For Supervisor responses, return allowlisted fields only. Do not return raw Supervisor payloads.

## Troubleshooting

If a known command is unavailable:

1. Check whether the node advertises it in the live command list.
2. Check gateway `nodes.allowCommands` in private config.
3. Check the paired-node command cache; WS reconnect alone may not refresh cached command lists.
4. For add-on command changes, a full add-on restart/handshake may be needed to refresh advertised commands.

If a Supervisor call returns noisy HTML or raw upstream errors, suppress the raw body and return a short structured error. Do not dump HTML into chat.

## Reporting Progress

When reporting status, name the actual lane:

- command usage/runbook work,
- command implementation,
- subagent allowlist enforcement,
- subagent wiring,
- MCP replacement audit,
- readiness validation,
- or remaining HA Assist UX bugs.

Avoid saying only "MCP sunset is in progress" without the concrete lane.
