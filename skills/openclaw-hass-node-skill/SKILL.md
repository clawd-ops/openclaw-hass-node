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
- Inspecting supported config/file paths through the node, or editing them under the current mutation availability (see Safety Boundaries: `ha.config.*` mutations and protected-root / `agent_bridge` file writes are refused at this head; direct writes to unprotected paths are supported).
- Wiring agents or subagents to the Home Assistant node command surface.

**Context scope:** this skill applies in chat, cron, main-session, and subagent contexts where `nodes.invoke` is available. It does NOT apply in Assist contexts (HA voice/text turns relayed through `openclaw-hass-node-app`). In Assist, `nodes.invoke` is intentionally filtered out per the OpenClaw reduced trusted surface (≥ 2026.3.31). For Assist HA operations, the operator must enable and configure the `openclaw-hass-node-assist-tools` plugin, which exposes scoped `ha_*` wrappers that work within Assist's tool filter. Subagents spawned from an Assist turn inherit Assist's tool filter and therefore cannot use this skill unless `nodes.invoke` is actually present in their context.

The core rule: use `nodes.invoke` against the configured Home Assistant node, then choose the safest specific command family for the task. For normal Home Assistant work, use the node path rather than an MCP path.

## Do NOT Confuse These Surfaces

**`mcp__openclaw__*` tools ≠ `nodes.invoke`.** The prefix identifies gateway-hosted MCP wrappers, but it does not by itself identify the target: some wrappers operate on the gateway host, while file-transfer wrappers target a selected paired node by invoking that node's `file.fetch` / `dir.list` / `dir.fetch` host commands. Check the individual tool contract rather than inferring its target from the prefix. For this skill, use `nodes.invoke` for the Home Assistant node command surface.

**Node filesystem paths are on the hass node app, not the OC host.** When an operator asks you to inspect `/config`, `/backup`, or any other path on the hass instance, those paths live inside the HA Supervisor environment on the node. The core file-transfer wrappers can target a paired node, but this HA node does not implement their `file.fetch` / `dir.list` / `dir.fetch` commands. To list or read files on this hass node, use the node's own `fs.*` commands (`fs.list`, `fs.read`, `fs.stat`, `fs.glob`) via `nodes.invoke`.

**`fs.*` is NOT the OpenClaw file-transfer plugin.** The gateway's `file-transfer` extension registers a distinct `file.fetch` / `dir.list` / `dir.fetch` protocol (canonical path + size + MIME + base64 bytes + sha256, gated by `allowReadPaths`) as a `nodeHostCommand`. The Home Assistant node does **not** implement or advertise that protocol: `_NODE_COMMANDS` in `app/node/src/openclaw_node/gateway_ws.py` lists only `ping`, `fs.*`, `system.*`, and `ha.*`. `mcp__openclaw__file_fetch` and `mcp__openclaw__dir_fetch` against this node will be rejected before dispatch. Do not tell an operator that adding `file.fetch` to `gateway.nodes.commands.allow` or setting `allowReadPaths` will enable file fetches from this node — the protocol is not implemented here, and `fs.*` uses a different config surface (`OPENCLAW_ALLOWED_ROOTS` inside the add-on, defaulted for add-on mode).

**`fs.read` returns text or base64 in-band, not a binary transfer.** `fs.read` accepts `path`, `encoding?` (`utf-8` default or `binary` for base64), and (as of merged PR #278) validated `offset?` / `length?` byte-range parameters bounded by `max_bytes?`. It returns `{content, size, file_size, offset, length, eof, sha256}` for the returned slice. It is not a `file.fetch` substitute for arbitrary large binaries: reads are clamped by `max_bytes` (1B–16MiB) and return `TOO_LARGE` rather than truncating; use ranged reads for larger files. See `docs/reference/COMMAND-SURFACE.md` for the full `fs.*` contract.

**The node-command allowlist governs node commands only, but it does apply to file-transfer wrappers.** The gateway's `gateway.nodes.commands.allow` / `gateway.nodes.commands.deny` policy controls which node commands (`ha.*`, `fs.*`, `system.*`, `file.fetch`, `dir.list`, `dir.fetch`, and so on) are permitted on a paired node. That policy applies regardless of whether a node command is reached through raw `nodes.invoke` or through a higher-level MCP wrapper such as `mcp__openclaw__file_fetch` / `dir_list` / `dir_fetch`, which invoke `file.fetch` / `dir.list` / `dir.fetch` on the selected node. Host-local `mcp__openclaw__*` tools (ones whose contracts do not target a paired node) are unrelated to this allowlist. Adding a command name to `gateway.nodes.commands.allow` that the node does not implement or advertise (for example `file.fetch` on this HA node) does not create the capability: the Home Assistant node's `_NODE_COMMANDS` in `app/node/src/openclaw_node/gateway_ws.py` does not include `file.fetch` / `dir.list` / `dir.fetch`, so those wrappers will still be rejected against this node even with the allowlist entry present.

**In Assist context, `nodes.invoke` is unavailable.** Assist turns (relayed through `openclaw-hass-node-app`) use the scoped `ha_*` wrappers from the `openclaw-hass-node-assist-tools` plugin. These wrappers ARE the node command surface for Assist — they internally route to the paired node. Do not attempt to call `nodes.invoke` in Assist; it is not in the tool filter. The Assist plugin exposes `ha_*` wrappers only; it does not surface `fs.*`.

**When a request cannot be fulfilled, say so clearly.** If a filesystem or capability request cannot be satisfied by `ha.*` or `fs.*` on this node, say that clearly rather than inventing a config change. Never fabricate `gateway.nodes.commands.allow` entries, `allowReadPaths` adjustments, or claim `file.fetch`/`dir.fetch` support on this node without confirming the command is actually registered and the path is inside a configured allowed root.

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
- `ha.config.*` for HA-native domain-config editing (lovelace dashboards, automations, scripts, scenes, helpers, area/device/entity registries, integrations/config_entries). **Mutating actions are currently unavailable at this head.** `commands/config_mutation.py` returns `PROPOSAL_REQUIRED` for every mutation because no trusted approval verifier is implemented yet; a caller-supplied `proposal_id` is audit metadata, not authorization, and cannot bypass the refusal. Read-only enumeration remains available: REST-based per-id domains (`ha.config.automation` / `script` / `scene`) validate `id` against HA's `cv.slug` (`^[a-z0-9_]+$`), and enumeration for the REST-per-id domains goes through the existing `ha.list_automations` and `ha.list_states` filtered by `script.*` / `scene.*`.
- `fs.*` for supported file inspection and direct writes to unprotected paths inside `OPENCLAW_ALLOWED_ROOTS`. Writes routed through protected roots or the `agent_bridge` flag are currently unavailable: `commands/fs_write.py` and `commands/fs_move_delete.py` return `PROPOSAL_REQUIRED` because the gateway-side proposal bridge (P3.3) is not shipped yet. Writes to `.storage/` return `STORAGE_READONLY` and must go through the HA REST config API instead.
- `system.*` for bounded node/system diagnostics.
- `ping` for connectivity and basic health.

Prefer read-only commands first when investigating. Prefer dedicated commands over broad generic paths.

### Narrow automation listings

Use `ha.list_automations` instead of fetching every Home Assistant state and
filtering locally. Its optional parameters are:

- `entity_filter`: a case-sensitive, `fnmatch`-style entity-ID glob. It must be
  a non-empty string beginning with `automation.` and is limited to 256
  characters. A literal entity ID is valid.
- `state_filter`: a non-empty string limited to 256 characters and matched
  exactly against the automation state, usually `on` or `off`.
- `include_traces`: a boolean that defaults to `false`. When true, the node
  fetches traces only after applying both filters.

Example:

```json
{
  "nodeId": "<home-assistant-node-id>",
  "command": "ha.list_automations",
  "params": {
    "entity_filter": "automation.morning_*",
    "state_filter": "on",
    "include_traces": false
  }
}
```

If a requested filter is invalid, correct it or report the error; never retry
without the filter because that widens the request. The node returns
`INVALID_PARAM` for empty or non-string filter values, an out-of-domain
`entity_filter`, either filter exceeding 256 characters, and unknown parameters.
A valid no-match filter returns an empty list. In Assist, use the
`ha_list_automations` wrapper with the same filter semantics.

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
- Any `ha.config.*` mutating action (`save` / `delete` / `create` / `update` / `remove`). **These paths currently refuse unconditionally with `PROPOSAL_REQUIRED` regardless of `proposal_id`.** Re-enabling them requires the trusted approval verifier and human round-trip described in `docs/design/AUTHORIZATION-MODEL.md`, not a params flag; there is no valid `proposal_id` for this surface at this head. When that ships, mutations will additionally require citing a `docs.lookup` for integration-level `config_entries.disable` / `enable` so a reviewer can see why the change was made.
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
