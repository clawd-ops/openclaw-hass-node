# MCP Migration Inventory

Tracks the coverage gap between the existing OpenClaw MCP servers and the
node command surface that replaces them. **P6 cannot start (i.e. the MCP
servers cannot be retired) until every row below is either ✅ Covered or
explicitly waived.**

## Coverage gate

Per PLAN.md §3 (P1.3, 2026-06-05): retire the MCP servers only after the
node has demonstrably handled every call surface they currently serve,
across every agent that uses them, for **7 consecutive days** with zero
unhandled `mcp__homeassistant*` calls in the gateway logs.

## `mcp__homeassistant__*` (9 tools)

| MCP tool                            | Node equivalent           | Covered    |
| ----------------------------------- | ------------------------- | ---------- |
| `ha_list_states`                    | `ha.list_states`          | ✅ P4.1     |
| `ha_get_state`                      | `ha.get_state`            | ✅ P4.1     |
| `ha_call_service`                   | `ha.call_service`         | ✅ P4.1     |
| `ha_list_areas`                     | `ha.list_areas`           | ✅ P4.2     |
| `ha_list_devices`                   | `ha.list_devices`         | ✅ P4.2     |
| `ha_list_services`                  | `ha.list_services`        | ✅ P4.2     |
| `ha_list_entity_registry`           | `ha.list_entity_registry` | ✅ P4.2     |
| `ha_light_turn_on`                  | `ha.light_turn_on`        | ✅ P4.4     |
| `ha_light_turn_off`                 | `ha.light_turn_off`       | ✅ P4.4     |

All 9 mutating + read tools are covered by the node command surface.

## `mcp__homeassistant-readonly__*` (9 tools)

Identical surface to `mcp__homeassistant__*` but read-only. All 9 are
covered by the same node commands (since read commands never mutate).

## `mcp__openclaw__*` (35 tools)

The OpenClaw MCP tools are gateway-internal, **not** routed through the
node. They are out of scope for HA-node migration. Retirement of the
`homeassistant*` MCPs does not affect them.

## Per-agent call inventory

Per PLAN.md the retirement gate also requires zero unhandled MCP calls
across **every agent** that uses them. As of 2026-06-06, the relevant
agents and their HA MCP touchpoints:

| Agent          | HA MCP usage                                    | Migration status                            |
| -------------- | ----------------------------------------------- | ------------------------------------------- |
| main session   | Ad-hoc HA queries via `mcp__homeassistant*`      | Will switch when gateway routes via node    |
| ReefMaster     | `ha_list_states` / `ha_get_state` on tank sensors| Same — read-only is satisfied               |
| PoolMaster     | `ha_get_state` on pump telemetry                 | Same                                        |
| HomeOps        | Mixed; some calls via Supervisor REST direct     | Out of scope — does not use MCP HA tools    |
| heartbeats     | Calendar entity reads via `ha_get_state`         | Same                                        |

All agents that use `mcp__homeassistant*` are read- or service-call-driven
and have direct equivalents in the node surface.

## Retirement plan

When the validation window closes (zero unhandled MCP calls × 7 days):

1. Update the per-agent prompts that reference `mcp__homeassistant*` by
   name to reference `ha.*` (via the gateway).
2. Drop the `homeassistant` and `homeassistant-readonly` MCP server
   entries from the gateway config in one PR.
3. Restart the OpenClaw gateway. The MCP servers stop running; the agents
   continue working unchanged because the gateway routes their existing
   `ha.*` calls to the node.

This cutover is a single PR — no migration scripts, no data move.

## Open items

- **Validation harness**: we don't have a log scraper that asserts "zero
  unhandled MCP calls for 7 days" yet. Lands in P6.1 as a small script
  that greps the gateway logs and emits a daily readiness signal.
- **Decision: include `mcp__homeassistant-readonly__*` in retirement?**
  Yes — it's strictly a subset of `mcp__homeassistant__*` and the node
  read surface covers it. Single-PR cutover handles both.
