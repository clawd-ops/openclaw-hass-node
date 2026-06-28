---
name: home-assistant-node
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

The core rule: use `nodes.invoke` against the configured Home Assistant node, then choose the safest specific command family for the task. For normal Home Assistant work, use the node path rather than an MCP path.

The node ID is deployment-specific. It may be `hass` in one environment and something else in another. Discover or confirm the node ID before invoking commands.

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
