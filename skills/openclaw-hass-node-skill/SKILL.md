---
name: openclaw-hass-node-skill
description: Use when inspecting, diagnosing, operating, or safely editing Home Assistant through OpenClaw's Home Assistant node command surface.
---

# Home Assistant Node

Use this skill whenever an agent needs to inspect, diagnose, operate, or safely edit something through an OpenClaw Home Assistant node.

Good fits:

- Reading Home Assistant entity state, services, areas, devices, registries, automations, add-ons, logs, or metadata.
- Running bounded diagnostics against the Home Assistant node.
- Performing explicitly authorized Home Assistant actions.
- Inspecting or editing supported config/file paths through the node.
- Wiring agents or subagents to the Home Assistant node command surface.

**Context scope:** this skill applies in chat, cron, main-session, and subagent contexts where `nodes.invoke` is available. It does NOT apply in Assist contexts (HA voice/text turns relayed through `openclaw-hass-node-app`). In Assist, `nodes.invoke` is intentionally filtered out per the OpenClaw reduced trusted surface (≥ 2026.3.31). For Assist HA operations, the operator must enable and configure the `openclaw-hass-node-assist-tools` plugin, which exposes scoped `ha_*` wrappers that work within Assist's tool filter. Subagents spawned from an Assist turn inherit Assist's tool filter and therefore cannot use this skill unless `nodes.invoke` is actually present in their context.

The core rule: use `nodes.invoke` against the configured Home Assistant node, then choose the safest specific command family for the task. For normal Home Assistant work, use the node path rather than an MCP path.

## Do NOT Confuse These Surfaces

**`mcp__openclaw__*` tools ≠ hass node commands.** MCP tools prefixed `mcp__openclaw__` (e.g. `mcp__openclaw__dir_list`, `mcp__openclaw__dir_fetch`, `mcp__openclaw__file_fetch`, `mcp__openclaw__ha_get_state`) execute against the **OC host** — the Linux machine running the OpenClaw gateway. They do not target any node. Do not call them expecting to reach the hass node filesystem or HA API.

**Node filesystem paths are on the hass node addon, not the OC host.** When an operator asks you to inspect `/config`, `/backup`, or any other path on the hass instance, those paths live inside the HA Supervisor environment on the node. They are not visible to `mcp__openclaw__dir_list` or any OC host tool. To list or read files on the hass node, use the file-transfer plugin (`fs.*` node commands) if it is enabled and the path is in its `allowReadPaths` — not any `mcp__openclaw__` tool.

**`allowCommands` governs node commands only.** The gateway's `gateway.nodes.allowCommands` setting controls which `ha.*` / `fs.*` / `system.*` node commands are permitted. It has no effect on `mcp__openclaw__*` MCP tools. Telling an operator to add a command to `allowCommands` will not enable an MCP tool, and vice versa.

**In Assist context, `nodes.invoke` is unavailable.** Assist turns (relayed through `openclaw-hass-node-app`) use the scoped `ha_*` wrappers from the `openclaw-hass-node-assist-tools` plugin. These wrappers ARE the node command surface for Assist — they internally route to the paired node. Do not attempt to call `nodes.invoke` in Assist; it is not in the tool filter.

**When a request cannot be fulfilled, say so clearly.** If a filesystem or capability request cannot be satisfied by `ha.*` commands or the file-transfer plugin, say that clearly rather than inventing a config change. Never fabricate `allowCommands` entries or `allowReadPaths` adjustments without confirming the feature exists and the path is supported.

The node ID is deployment-specific. It may be `hass` in one environment and something else in another. Discover or confirm the node ID before invoking commands. Never derive a node ID from an add-on slug or an add-on display name; `openclaw-hass-node-app` is the add-on, not the node ID.

## Invocation Pattern

Use this shape:

```json
{
  "nodeId": "<home-assistant-node-id>",
  "command": "<command-family>.<command>",
  "params": {}
}
```

Examples:

```json
{
  "nodeId": "<home-assistant-node-id>",
  "command": "ha.get_state",
  "params": {
    "entity_id": "sensor.example"
  }
}
```

```json
{
  "nodeId": "<home-assistant-node-id>",
  "command": "ping",
  "params": {}
}
```

Current primary command families include:

- `ha.*` for Home Assistant API operations: states, services, areas, devices, registries, add-ons, config checks, and approved control actions.
- `ha.config.*` for HA-native domain-config editing (lovelace dashboards, automations, scripts, scenes, helpers, area/device/entity registries, integrations/config_entries). Every mutating action is proposal-gated (`proposal_id` required, `"direct"` refused). REST-based per-id domains (`ha.config.automation` / `script` / `scene`) validate `id` against HA's `cv.slug` (`^[a-z0-9_]+$`). WS-based domains use HA's storage-collection surface. Enumeration for the REST-per-id domains goes through the existing `ha.list_automations` and `ha.list_states` filtered by `script.*` / `scene.*`.
- `fs.*` for supported file inspection or proposal-gated file work exposed by the node.
- `system.*` for bounded node/system diagnostics.
- `ping` for connectivity and basic health.

Prefer read-only commands first when investigating. Prefer dedicated commands over broad generic paths.

## Safety Boundaries

Generally safe starting points:

- Entity/state reads.
- Registry, area, device, service, automation, and add-on discovery.
- Add-on information, logs, documentation, and changelog reads.
- Bounded diagnostics.
- Config validation.
- File reads/inspection through approved node commands.

Actions that need extra care:

- Generic `ha.call_service`, especially broad or sensitive service domains.
- Entity/device/light mutations where the target, scope, or requested outcome is unclear.
- Add-on start, stop, or restart.
- Reloads.
- Any command that changes Home Assistant state.
- Any `ha.config.*` mutating action (`save` / `delete` / `create` / `update` / `remove`). These edit the HA config store directly and must carry a real `proposal_id` naming the agent-bridge proposal that authorized the change. Never pass `proposal_id="direct"`. For `config_entries.disable` / `enable`, cite a `docs.lookup` for the integration before mutating so a reviewer can see why the change was made.
- Any command that changes add-on state.
- Any command that edits configuration or files.
- Any broad or generic command where the effect is unclear.

Operating rules:

- Prefer observation over action.
- Prefer dedicated read commands over generic commands.
- Use the least-privileged command that accomplishes the task.
- Respect the node's built-in authorization model instead of assuming every mutation is privileged.
- If your human clearly requests a specific low-risk action, such as turning on a named light, that request is sufficient authorization.
- If the target, scope, blast radius, or safety of the requested action is unclear, ask your human instead of guessing.
- When exact permission boundaries matter, consult `docs/design/IDENTITY-AND-SCOPES.md`; do not guess whether a command or service domain is allowed.
- Do not hand privileged commands to background subagents.

## Subagents

When acting as a subagent, use only read-only/Tier A command paths. Do not attempt privileged commands, lifecycle actions, service calls, reloads, or file edits unless the controlling agent provides an explicitly authorized, software-enforced path.

## Details And Source Of Truth

When exact command names, parameters, tiers, or edge cases matter, read the repo docs rather than duplicating them in this skill:

- `docs/reference/COMMAND-SURFACE.md` - complete command catalog, parameters, and command families.
- `docs/design/COMMAND-TIERS.md` - risk tiers, safety expectations, and enforcement model.
- `docs/design/IDENTITY-AND-SCOPES.md` - identity, scopes, and authorization model.
- `docs/reference/HA-CONFIG-EDITING.md` - safe Home Assistant config editing guidance.
- `docs/TODO.md` - active implementation gaps and follow-up work.

If docs, advertised commands, and runtime behavior disagree, stop and report the inconsistency instead of guessing.
