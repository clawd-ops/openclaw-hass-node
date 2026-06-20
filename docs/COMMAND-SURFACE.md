# Command Surface

> **Beta.** This file documents the **live** command registry in
> `commands/dispatcher.py`. Commands listed here are registered and
> working. Planned commands that are not yet implemented are listed
> separately at the end.

Commands the node exposes via `node.invoke`. Group prefixes match
OpenClaw conventions where they exist. 28 commands are registered.

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
(`/config`, `/share`, `/ssl`, `/addons`, `/media`, `/backup` in
add-on mode; configurable via `OPENCLAW_ALLOWED_ROOTS` in standalone
mode). Path traversal and symlink escape are blocked by `safe_fd.py`.

## `system.*` — shell (2 commands)

| Command        | Args                               | Notes                  |
|----------------|-------------------------------------|------------------------|
| `system.run`   | `cmd`, `cwd?`, `env?`, `timeout?`, `admin_token` | Gated by `OPENCLAW_ADMIN_TOKEN` env var; caller must pass matching `admin_token` param |
| `system.which` | `binary`                            | Lookup only, basename-only |

## `ha.*` — Home Assistant control (15 commands)

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
  P5.13 dual-WS ChatRelay (shipped 2026-06-08), which uses
  `chat.send` + `sessions.messages.subscribe` over the operator-role
  gateway connection instead of custom command types.
