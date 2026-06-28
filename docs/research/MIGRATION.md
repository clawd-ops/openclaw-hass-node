# MCP Migration Inventory

> **Scope.** This document is about retiring `mcp__homeassistant*` MCP
> servers configured in **the maintainer's upstream OpenClaw deployment**
> (the gateway repo, not this one). Third-party installs of
> openclaw-hass-node never had those MCP servers in the first place —
> they get HA control via `ha.*` through this repo's gateway and node
> from day one and can ignore P6 entirely.
>
> Kept in this repo because retiring those upstream MCPs depends on
> *this* repo's command surface being complete, and the migration
> bookkeeping logically belongs alongside the surface it's tracking.

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

## Tier A read-only addon surface (node-only, no MCP equivalent)

The node ships six Supervisor add-on commands that have no
`mcp__homeassistant*` counterpart — they read Supervisor add-on
endpoints (`/addons`, `/addons/<slug>/info`, `/stats`, `/logs`,
`/changelog`, `/documentation`) that the MCP servers never exposed.
Listed here so the surface is documented in one place; they don't
gate MCP retirement.

| Node command              | Notes                                                  |
| ------------------------- | ------------------------------------------------------ |
| `ha.list_addons`          | All installed add-ons; `repository` dropped at boundary |
| `ha.addon_info`           | Per-addon metadata; `options`/`schema`/`repository` dropped |
| `ha.addon_stats`          | Allowlisted utilisation metrics                        |
| `ha.addon_logs`           | Bounded 1 MiB trailing window                          |
| `ha.addon_changelog`      | Bounded 1 MiB trailing window                          |
| `ha.addon_documentation`  | Bounded 1 MiB trailing window                          |

## `mcp__openclaw__*` (35 tools)

The OpenClaw MCP tools are gateway-internal, **not** routed through the
node. They are out of scope for HA-node migration. Retirement of the
`homeassistant*` MCPs does not affect them.

## Per-agent call inventory

Per PLAN.md the retirement gate also requires zero unhandled MCP calls
across **every agent** that uses them. As of 2026-06-06, the relevant
agents and their HA MCP touchpoints:

| Agent          | Former HA MCP usage                              | Migration status                            |
| -------------- | ----------------------------------------------- | ------------------------------------------- |
| main session   | Ad-hoc HA queries via `mcp__homeassistant*`      | Fresh sessions are instructed to use `nodes.invoke` against the connected `hass` node with `ha.*` commands |
| ReefMaster     | `ha_list_states` / `ha_get_state` on tank sensors| Fresh sessions should use read-only `ha.list_states` / `ha.get_state` through `hass` |
| PoolMaster     | `ha_get_state` on pump telemetry                 | Fresh sessions should use read-only `ha.get_state` through `hass` |
| HomeOps        | Mixed; some calls via Supervisor REST direct     | Out of scope — does not use MCP HA tools    |
| heartbeats     | Calendar entity reads via `ha_get_state`         | Fresh sessions should use read-only `ha.get_state` through `hass` |

All agents that used `mcp__homeassistant*` are read- or service-call-driven
and have direct equivalents in the node surface. The canonical replacement
path is `nodes.invoke` against node `hass` with the matching `ha.*` command.

## Retirement plan

Runtime cutover status as of 2026-06-28:

1. Per-agent startup guidance now says Home Assistant work uses the `hass`
   node command surface via `nodes.invoke` and `ha.*`, not
   `mcp__homeassistant*`.
2. The live OpenClaw config has removed the `homeassistant` and
   `homeassistant-readonly` MCP server entries.
3. The `hass` node validates as paired/connected and exposes the replacement
   `ha.*` command surface.

This means fresh sessions should no longer see or use the retired MCP tools.
Already-running sessions may still hold stale MCP tool inventories until they
exit. Formal retirement still requires the validation window below: zero
unhandled `mcp__homeassistant*` calls for 7 consecutive clean days.

## Validation harness (P6.1)

`scripts/check-mcp-retirement-readiness.sh` is **source-agnostic** — it
reads log lines on stdin so the caller picks how to produce them. This
keeps it usable across any deployment that's running upstream OpenClaw,
not just the maintainer's Kubernetes pod.

```bash
# Local file
cat /var/log/openclaw.log                       | scripts/check-mcp-retirement-readiness.sh
# systemd unit
journalctl -u openclaw --since=24h              | scripts/check-mcp-retirement-readiness.sh
# Docker container
docker logs --since 24h openclaw 2>&1           | scripts/check-mcp-retirement-readiness.sh
# Kubernetes pod
kubectl -n ai logs openclaw-0 --since=24h       | scripts/check-mcp-retirement-readiness.sh
```

Verdicts: `MCP_READINESS_OK` / `RETIREMENT_READY` / `MCP_READINESS_NOT_READY`.
With `--state-file` the script tracks a clean-day streak; 7 consecutive
clean days flip the verdict to `RETIREMENT_READY`.

## Open items

- **Decision: include `mcp__homeassistant-readonly__*` in retirement?**
  Yes — it's strictly a subset of `mcp__homeassistant__*` and the node
  read surface covers it. Single-PR cutover handles both.
