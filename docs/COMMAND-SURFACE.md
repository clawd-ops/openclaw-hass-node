# Command Surface

> **Beta.** This file documents the **live** command registry in
> `commands/dispatcher.py`. Commands listed here are registered and
> working. Planned commands that are not yet implemented are listed
> separately at the end.

Commands the node exposes via `node.invoke`. Group prefixes match
OpenClaw conventions where they exist. 37 commands are registered.

Addon-management commands are tiered by blast radius. Tier A
(read-only) is on the subagent allowlist; Tier B (lifecycle) is
admin-gated; Tier C (install / uninstall / update / rebuild) is
explicitly out of scope. Full policy + constraints in
[`docs/COMMAND-TIERS.md`](./COMMAND-TIERS.md).

## `ping` — liveness

| Command | Args        | Notes  |
|---------|-------------|--------|
| `ping`  | `message?`  | Always available |

## `fs.*` — filesystem (11 commands)

| Command       | Args                                | Notes                     |
|---------------|-------------------------------------|---------------------------|
| `fs.read`     | `path`, `offset?`, `limit?`         |                           |
| `fs.list`     | `path`, `recursive?`, `glob?`       |                           |
| `fs.stat`     | `path`                              |                           |
| `fs.glob`     | `pattern`, `cwd?`                   |                           |
| `fs.write`    | `path`, `content`, `mode?`          |                           |
| `fs.patch`    | `path`, `patch` (unified diff)      |                           |
| `fs.move`     | `src`, `dst`                        |                           |
| `fs.delete`   | `path`                              | Uses `send2trash` (FreeDesktop.org spec) with an OpenClaw-managed trash directory fallback |
| `fs.restore`  | `trash_id?` or `path?`              | Restore from trash        |
| `fs.history`  | `path`                              | Content-addressed backup history |
| `fs.diff`     | `path`, `version?`                  | Diff against backup version |

All filesystem commands are scoped to the add-on's allowed roots
(`/config`, `/share`, `/media` in add-on mode, matching the `map:`
entries in `addon/config.yaml`; configurable via `OPENCLAW_ALLOWED_ROOTS`
in standalone mode). Path traversal and symlink escape are blocked by
`safe_fd.py`.

## `system.*` — shell (2 commands)

| Command        | Args                               | Notes                  |
|----------------|-------------------------------------|------------------------|
| `system.run`   | `cmd`, `cwd?`, `env?`, `timeout?`, `admin_token` | Gated by `OPENCLAW_ADMIN_TOKEN` env var; caller must pass matching `admin_token` param |
| `system.which` | `binary`                            | Lookup only, basename-only |

## `ha.*` — Home Assistant control (23 commands)

| Command                   | Args / Notes                           |
|---------------------------|----------------------------------------|
| `ha.list_states`          | All entities and current state         |
| `ha.get_state`            | `entity_id`                            |
| `ha.list_services`        | Service catalog (REST)                 |
| `ha.call_service`         | `domain`, `service`, `target?`, `data?` |
| `ha.list_areas`           | Via WS API                             |
| `ha.list_devices`         | Via WS API                             |
| `ha.list_entity_registry` | Via WS API                             |
| `ha.logbook`              | `entity_id?`, `start?`, `end?` (REST) |
| `ha.history`              | `entity_id?`, `start?`, `end?` (REST) |
| `ha.reload_config`        | `domain`, `admin_token`; gated by `OPENCLAW_ADMIN_TOKEN` |
| `ha.light_turn_on`        | `entity_id` or `area_id` or `device_id` |
| `ha.light_turn_off`       | `entity_id` or `area_id` or `device_id` |
| `ha.list_automations`     | `include_traces?`; filters to `automation.` prefix |
| `ha.check_config`         | Validates HA core config before reload |
| `ha.addon_logs`           | `slug`, `lines?` (1–5000, default 200); Supervisor add-on logs, read-only; trims from a bounded 1 MiB trailing byte window |
| `ha.list_addons`          | List Supervisor add-ons (slug, name, state, version, version_latest, update_available), read-only. `repository` is dropped because for community/private addons it holds an operator-private repo URL |
| `ha.addon_info`           | `slug`; per-addon metadata (slug, name, state, description, version, version_latest, update_available, boot, startup, stage, arch, machine, ingress, ingress_port). **`options` (current option VALUES), `schema` (option field NAMES), and `repository` are dropped at the boundary** — option values are secrets, schema field names can reveal which integrations are configured, and `repository` for non-core addons can leak an operator-private hostname. Supervisor response body is also capped at 1 MiB before parsing. Read-only |
| `ha.addon_stats`          | `slug`; allowlisted utilisation metrics (cpu_percent, memory_usage/limit/percent, network_rx/tx, blk_read/write). Read-only |
| `ha.addon_changelog`      | `slug`; addon changelog markdown, bounded 1 MiB trailing window. Read-only |
| `ha.addon_documentation`  | `slug`; addon documentation markdown, bounded 1 MiB trailing window. Read-only |
| `ha.addon_start`          | `slug`, `admin_token`; Tier B lifecycle command. Requires matching `OPENCLAW_ADMIN_TOKEN`, explicit `addon_lifecycle.allowlist` opt-in, and is always denied for `homeassistant`, `supervisor`, and `core_*` slugs |
| `ha.addon_stop`           | `slug`, `admin_token`; same Tier B gate as `ha.addon_start`; idempotent when already stopped |
| `ha.addon_restart`        | `slug`, `admin_token`; same Tier B gate as `ha.addon_start` |

## Planned (not yet registered)

The following command groups are designed but not yet implemented.
They will be registered in the dispatcher as each phase ships.

- **`ha.config.*`** — domain config editing (automations, scripts,
  scenes, lovelace, blueprints). Detects YAML vs UI storage mode.
  See `HA-CONFIG-EDITING.md`.
- **`docs.*`** — versioned HA docs lookup (`docs.lookup`,
  `docs.search`, `docs.versions`).
- **`ha.supervisor.*`** — Supervisor API wrappers (info, addons,
  snapshots).
- **`assist.*`** — conversation relay commands. Superseded by the
  shipped dual-websocket relay, which uses `chat.send` +
  `sessions.messages.subscribe` over the operator-role gateway
  connection instead of custom command types.
