---
name: home-assistant-node
description: Use when inspecting, diagnosing, operating, or safely editing Home Assistant through OpenClaw's hass node command surface.
---

# Home Assistant Node

Use this skill whenever an agent needs to inspect, diagnose, operate, or safely edit something through OpenClaw's `hass` node.

Good fits:

- Reading Home Assistant entity state, services, areas, devices, registries, automations, add-ons, logs, or metadata.
- Running bounded diagnostics against the Home Assistant node.
- Performing explicitly authorized Home Assistant actions.
- Inspecting or editing supported config/file paths through the node.
- Wiring agents or subagents to the `hass` node command surface.

The core rule: use `nodes.invoke` against node `hass`, then choose the safest specific command family for the task.

## Invocation Pattern

Use this shape:

```json
{
  "nodeId": "hass",
  "command": "<command-family>.<command>",
  "params": {}
}
```

Examples:

```json
{
  "nodeId": "hass",
  "command": "ha.get_state",
  "params": {
    "entity_id": "sensor.example"
  }
}
```

```json
{
  "nodeId": "hass",
  "command": "ping",
  "params": {}
}
```

Use these command families by intent:

- `ha.*` for Home Assistant API operations: states, services, areas, devices, registries, add-ons, config checks, and approved control actions.
- `fs.*` for supported file inspection or proposal-gated file work exposed by the node.
- `system.*` for bounded node/system diagnostics.
- `ping` for connectivity and basic health.

Prefer read-only commands first. Prefer dedicated commands over broad generic mutation paths.

## Safety Boundaries

Safe by default for agents and subagents:

- Entity/state reads.
- Registry, area, device, service, automation, and add-on discovery.
- Add-on information, logs, documentation, and changelog reads.
- Bounded diagnostics.
- Config validation.
- File reads/inspection through approved node commands.

Privileged unless Rob explicitly authorizes the action:

- `ha.call_service`.
- Entity/device/light mutations.
- Add-on start, stop, or restart.
- Reloads.
- Any command that changes Home Assistant state.
- Any command that changes add-on state.
- Any command that edits configuration or files.
- Any broad or generic command where the effect is unclear.

Operating rules:

- Prefer observation over action.
- Prefer dedicated read commands over generic commands.
- Treat anything that changes Home Assistant, add-ons, files, configuration, or physical devices as privileged.
- Do not assume a generic service call is safe.
- Do not hand privileged commands to background subagents.

## Subagents

Subagents should use only read-only command paths unless Rob explicitly authorizes a broader operator-controlled path and software enforcement exists for that path.

Prompt instructions are not enough for subagent safety. The node or gateway path must software-block subagents from commands outside the approved read-only surface before relying on the delegation.

## Legacy MCP Note

Legacy Home Assistant MCP tools are historical/fallback paths. Do not use them for normal Home Assistant work unless Rob explicitly asks for that path.

## Details And Source Of Truth

When exact command names, parameters, tiers, or edge cases matter, read the repo docs rather than duplicating them in this skill:

- `/home/openclaw/.openclaw/projects/openclaw-hass-node/docs/reference/COMMAND-SURFACE.md` - complete command catalog, parameters, and command families.
- `/home/openclaw/.openclaw/projects/openclaw-hass-node/docs/design/COMMAND-TIERS.md` - risk tiers, safety expectations, and enforcement model.
- `/home/openclaw/.openclaw/projects/openclaw-hass-node/docs/reference/HA-CONFIG-EDITING.md` - safe Home Assistant config editing guidance.
- `/home/openclaw/.openclaw/projects/openclaw-hass-node/docs/research/MIGRATION.md` - historical MCP migration context only.
- `/home/openclaw/.openclaw/projects/openclaw-hass-node/docs/TODO.md` - active implementation gaps and follow-up work.

If docs and code disagree, inspect the dispatcher and relevant command module, then fix docs and code together.
