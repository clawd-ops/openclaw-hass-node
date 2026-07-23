# Command Surface

> **Beta.** This file documents the **live** command registry in
> `commands/dispatcher.py`. Commands listed here are registered and
> working. Planned commands that are not yet implemented are listed
> separately at the end.

Commands the node exposes via `node.invoke`. Group prefixes match
OpenClaw conventions where they exist. 46 commands are registered.

Convention for the `ha.config.*` domain: **one command per HA config
domain**, with an `action` parameter selecting the operation. This keeps
the dispatcher, gateway allowlist, and connect-surface advertisement
compact as the six planned domains land. Per-verb commands
(`ha.config.<domain>.<verb>`) are not the convention going forward.

Addon-management commands are tiered by blast radius. Tier A
(read-only) is designated for subagent use; Tier B (lifecycle) is
admin-gated; Tier C (install / uninstall / update / rebuild) is
explicitly out of scope. Full policy + constraints in
[`docs/design/COMMAND-TIERS.md`](../design/COMMAND-TIERS.md).

Note: Tier A is designated as the subagent-safe surface, but the
software-enforced allowlist gate at the node dispatcher is still pending
(TODO #11 — subagent-side enforcement). Until that lands, the restriction
is prompt-instructed via SKILL.md, not hardware-blocked.

## `ping` — liveness

| Command | Args        | Notes  |
|---------|-------------|--------|
| `ping`  | `message?`  | Always available |

## `fs.*` — filesystem (11 commands)

| Command       | Args                                | Notes                     |
|---------------|-------------------------------------|---------------------------|
| `fs.read`     | `path`, `encoding?`, `max_bytes?`   | `encoding` = `utf-8` (default) or `base64`. Returns `{content, size, sha256}`. |
| `fs.list`     | `path`, `hidden?`, `max_entries?`   | Non-recursive directory listing. `hidden?` includes dotfiles when true. |
| `fs.stat`     | `path`                              | File metadata: `kind`, `size`, `mtime`, `ctime`, `mode`, `owner_uid`, `group_gid`, `is_symlink`, `link_target`. |
| `fs.glob`     | `root`, `pattern`, `hidden?`, `max_matches?` | Glob under `root`. Result matches are `root`-relative. |
| `fs.write`    | `path`, `content`, `encoding?`, `proposal_id?`, `actor?`, `agent_bridge?` | Returns `{path, size, sha256}` where `sha256` is of the bytes **just written**. Prior bytes captured to the backup store when the file already existed (including zero-length files). |
| `fs.patch`    | `path`, `patch` (unified diff), `dry_run?`, `proposal_id?`, `actor?`, `agent_bridge?` | Applied by a pure-Python unified-diff engine (no `patch` binary dependency). Hunk `old_count`/`new_count` are enforced against the walked body; truncated hunks, hunk-less patches, and file-header-only patches are rejected with `PATCH_FAILED`. Preserves `\r` in body lines so CRLF/mixed-newline sources round-trip cleanly. Context-free pure-insertion hunks (`old_count=0`, e.g. `@@ -1,0 +2 @@` as emitted by `difflib.unified_diff(..., n=0)`) insert *after* `old_start` per the unified-diff spec; pure-add hunks whose insertion point is past the source end are rejected with `PATCH_FAILED`. |
| `fs.move`     | `src`, `dst`, `proposal_id?`, `actor?`, `agent_bridge?` | Atomic single-filesystem rename. Backup-store history for `src` is prepended to `dst`'s history so `fs.restore version=-1` at the destination returns the pre-move destination snapshot (not the moved-in source bytes). Cross-device moves return `CROSS_DEVICE`. |
| `fs.delete`   | `path`, `proposal_id?`, `actor?`, `agent_bridge?` | Uses `send2trash` (FreeDesktop.org spec) with an OpenClaw-managed trash directory fallback (`OPENCLAW_TRASH_DIR`, default `/share/openclaw-trash`). Fallback entries are named `<basename>.<path-slug>.<timestamp>` so files sharing a basename in different directories remain distinguishable. |
| `fs.restore`  | `path`, exactly one of `version`, `proposal_id`, or `at`, `actor?`, `agent_bridge?` | Restores from the content-addressed **backup store** (not the trash directory). Also purges any lingering OpenClaw fallback-trash entries whose full-path slug matches `path` (basename alone is insufficient); returns `trash_purged` count. |
| `fs.history`  | `path`                              | Content-addressed backup history for `path`. First-write of a fresh file records no entry (there is nothing to restore to). |
| `fs.diff`     | `path`, `from_version`, `to_version?` | `from_version` / `to_version` are 1-indexed positions (`-1` = latest) or a sha256 hex digest. `to_version` omitted compares `from_version` against the current live bytes on disk. |

All filesystem commands are scoped to the add-on's allowed roots
(`/config`, `/share`, `/media` in add-on mode, matching the `map:`
entries in `app/config.yaml`; configurable via `OPENCLAW_ALLOWED_ROOTS`
in standalone mode). Path traversal and symlink escape are blocked by
`safe_fd.py`.

## `system.*` — shell (2 commands)

| Command        | Args                               | Notes                  |
|----------------|-------------------------------------|------------------------|
| `system.run`   | `cmd`, `cwd?`, `env?`, `timeout?`, `admin_token` | Gated by `OPENCLAW_ADMIN_TOKEN` env var; caller must pass matching `admin_token` param |
| `system.which` | `binary`                            | Lookup only, basename-only |

## `ha.*` — Home Assistant control (28 commands)

| Command                   | Args / Notes                           |
|---------------------------|----------------------------------------|
| `ha.list_states`          | All entities and current state         |
| `ha.get_state`            | `entity_id`                            |
| `ha.list_services`        | Service catalog (REST)                 |
| `ha.get_config`           | HA core config (REST `/api/config`)    |
| `ha.list_events`          | Event bus listener summary (REST `/api/events`) |
| `ha.list_config_entries`  | Config entries (REST `/api/config/config_entries/entry`) |
| `ha.core_logs`            | `lines?` (1–5000, default 200); HA core logs via Supervisor |
| `ha.calendar_get_events`  | `entity_id`, `start_date_time`, `end_date_time`; wraps `calendar.get_events?return_response` |
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
| `ha.addon_update`         | `slug`, `admin_token`; same Tier B gate as `ha.addon_start`; updates the add-on to the latest available version (`POST /addons/<slug>/update`) |
| `ha.update_install`       | `entity_id` (required, must be `update.*`), `backup` (optional bool), `version` (optional str), `admin_token`; Tier B admin gate via `OPENCLAW_ADMIN_TOKEN`; installs a pending update via HA's `update.install` service — covers HACS integrations, HA Core, add-ons via the `update.*` entity domain. Distinct from `ha.addon_update` (Supervisor API, slug-based) |

## `ha.config.lovelace` — Lovelace dashboards (1 command)

HA-native WebSocket path for dashboards. See
`docs/reference/HA-CONFIG-EDITING.md` for the fs.patch vs ha.config
policy and the `.storage/` guardrail rationale.

Single command; the `action` param selects the operation. Unknown or
missing `action` returns `INVALID_PARAM`.

| `action`            | Params                                                                                                       | Notes |
|---------------------|--------------------------------------------------------------------------------------------------------------|-------|
| `get`               | `url_path?` (omit → default).                                                                                | WS `lovelace/config` with `{url_path}` in the payload when set. Returns `{url_path, config}`. |
| `save`              | `config` (dict, required), `url_path?`, `proposal_id` (required, non-empty, not `"direct"`).                 | WS `lovelace/config/save`. Proposal-gated. |
| `dashboards_list`   | —                                                                                                            | WS `lovelace/dashboards/list`. Returns `{count, dashboards}`. |
| `resources_list`    | —                                                                                                            | WS `lovelace/resources`. Returns `{count, resources}`. |
| `resources_create`  | `url` (required), `res_type` in {`module`,`css`,`js`,`html`}, `proposal_id` (required, non-empty, not `"direct"`). | WS `lovelace/resources/create`. Proposal-gated. |

Guardrail: attempts to reach lovelace `.storage/` files via `fs.write` /
`fs.patch` are refused with `STORAGE_READONLY` — callers must use the
commands above.

## `ha.config.automation` — Automations (1 command)

HA-native REST path for per-id automation config under
`/api/config/automation/config/<id>`. REST-only — the implementation
MUST NOT fall back to any WS frame. See
`docs/reference/HA-CONFIG-EDITING.md` for the fs.patch vs ha.config
policy.

**Enumeration**: use the existing `ha.list_automations` command
(reads `automation.*` entities from state). HA does not register a
collection-level `/api/config/automation/config` route.

Single command; the `action` param selects the operation. Unknown or
missing `action` returns `INVALID_PARAM`.

| `action`   | Params                                                                                          | Notes |
|------------|-------------------------------------------------------------------------------------------------|-------|
| `get`      | `id` (required, non-empty string).                                                              | `GET /api/config/automation/config/<id>`. Returns `{id, config}`. |
| `save`     | `id` (required), `config` (dict, required), `proposal_id` (required, non-empty, not `"direct"`). | `POST /api/config/automation/config/<id>`. Proposal-gated. |
| `delete`   | `id` (required), `proposal_id` (required, non-empty, not `"direct"`).                           | `DELETE /api/config/automation/config/<id>`. Proposal-gated. |

After a mutation, callers should follow up with
`ha.call_service` `automation` / `reload` to pick up the new config.

## `ha.config.script` — Scripts (1 command)

HA-native REST path for per-id script config under
`/api/config/script/config/<id>`. REST-only — the implementation
MUST NOT fall back to any WS frame. See
`docs/reference/HA-CONFIG-EDITING.md` for the fs.patch vs ha.config
policy.

**Enumeration**: read `script.*` entities from state (e.g. via
`ha.list_states`). HA does not register a collection-level
`/api/config/script/config` route.

Single command; the `action` param selects the operation. Unknown or
missing `action` returns `INVALID_PARAM`.

| `action`   | Params                                                                                          | Notes |
|------------|-------------------------------------------------------------------------------------------------|-------|
| `get`      | `id` (required, non-empty string).                                                              | `GET /api/config/script/config/<id>`. Returns `{id, config}`. |
| `save`     | `id` (required), `config` (dict, required), `proposal_id` (required, non-empty, not `"direct"`). | `POST /api/config/script/config/<id>`. Proposal-gated. |
| `delete`   | `id` (required), `proposal_id` (required, non-empty, not `"direct"`).                           | `DELETE /api/config/script/config/<id>`. Proposal-gated. |

After a mutation, callers should follow up with
`ha.call_service` `script` / `reload` to pick up the new config.

## `ha.config.scene` — Scenes (1 command)

HA-native REST path for per-id scene config under
`/api/config/scene/config/<id>`. REST-only — the implementation
MUST NOT fall back to any WS frame. See
`docs/reference/HA-CONFIG-EDITING.md` for the fs.patch vs ha.config
policy.

**Enumeration**: read `scene.*` entities from state (e.g. via
`ha.list_states`). HA does not register a collection-level
`/api/config/scene/config` route.

Single command; the `action` param selects the operation. Unknown or
missing `action` returns `INVALID_PARAM`.

| `action`   | Params                                                                                          | Notes |
|------------|-------------------------------------------------------------------------------------------------|-------|
| `get`      | `id` (required, non-empty string).                                                              | `GET /api/config/scene/config/<id>`. Returns `{id, config}`. |
| `save`     | `id` (required), `config` (dict, required), `proposal_id` (required, non-empty, not `"direct"`). | `POST /api/config/scene/config/<id>`. Proposal-gated. |
| `delete`   | `id` (required), `proposal_id` (required, non-empty, not `"direct"`).                           | `DELETE /api/config/scene/config/<id>`. Proposal-gated. |

After a mutation, callers should follow up with
`ha.call_service` `scene` / `reload` to pick up the new config.

## `ha.config.helpers` — Helpers (1 command)

HA-native WebSocket path for helper entities
(`input_boolean`, `input_text`, `input_number`, `input_select`,
`input_datetime`, `counter`, `timer`, `schedule`).

Single command; the `action` param selects the operation. `helper_type`
is required for every action. Unknown or missing `action` /
`helper_type` returns `INVALID_PARAM`.

HA's storage-collection websocket surface registers `<helper_type>/list`,
`<helper_type>/create`, `<helper_type>/update`, and `<helper_type>/delete`.
There is **no** `<helper_type>/get` frame — single-item lookup goes
through state and the entity registry, not this command. update/delete
use the item key named `<helper_type>_id` (e.g. `input_boolean_id`),
not `entity_id`.

| `action`   | Params                                                                                                            | Notes |
|------------|-------------------------------------------------------------------------------------------------------------------|-------|
| `list`     | `helper_type` (required).                                                                                         | WS `<helper_type>/list`. Returns `{helper_type, count, helpers}`. |
| `create`   | `helper_type`, `attrs` (dict, required), `proposal_id` (required, non-empty, not `"direct"`).                     | WS `<helper_type>/create` with the `attrs` dict as payload. Proposal-gated. |
| `update`   | `helper_type`, `<helper_type>_id` (required), `attrs` (dict), `proposal_id`.                                      | WS `<helper_type>/update` with `{<helper_type>_id, **attrs}`. Proposal-gated. |
| `delete`   | `helper_type`, `<helper_type>_id` (required), `proposal_id`.                                                      | WS `<helper_type>/delete` with `{<helper_type>_id}`. Proposal-gated. |

## `ha.config.area_registry` — Areas (1 command)

WS `config/area_registry/{list,create,update,delete}`.

| `action`   | Params                                                                                | Notes |
|------------|---------------------------------------------------------------------------------------|-------|
| `list`     | —                                                                                     | Returns `{count, areas}`. |
| `create`   | `name` (required), optional `attrs` (dict), `proposal_id`.                            | Proposal-gated. |
| `update`   | `area_id` (required), `attrs` (dict), `proposal_id`.                                  | Proposal-gated. |
| `delete`   | `area_id` (required), `proposal_id`.                                                  | Proposal-gated. |

## `ha.config.device_registry` — Devices (1 command)

WS `config/device_registry/{list,update}`. HA does not expose create
or delete for devices — they are populated by integrations.

| `action`   | Params                                                                                | Notes |
|------------|---------------------------------------------------------------------------------------|-------|
| `list`     | —                                                                                     | Returns `{count, devices}`. |
| `update`   | `device_id` (required), `attrs` (dict), `proposal_id`.                                | Proposal-gated. |

## `ha.config.entity_registry` — Entities (1 command)

WS `config/entity_registry/{list,get,update,remove}`.

| `action`   | Params                                                                                | Notes |
|------------|---------------------------------------------------------------------------------------|-------|
| `list`     | —                                                                                     | Returns `{count, entities}`. |
| `get`      | `entity_id` (required).                                                               | Returns `{entity_id, entity}`. |
| `update`   | `entity_id`, `attrs` (dict), `proposal_id`.                                           | Proposal-gated. |
| `remove`   | `entity_id`, `proposal_id`.                                                           | Proposal-gated. |

## `ha.config.config_entries` — Integrations (1 command)

WS `config_entries/get_single` (single lookup) and
`config_entries/disable` (both disable and re-enable — HA does not
register a separate `config_entries/enable` frame). Options flows are
served by HTTP flow views (`/api/config/config_entries/options/flow/...`)
and are not yet exposed by this command.

**Convention (soft)**: callers should cite a `docs.lookup` for the
integration before mutating. The handler does not hard-enforce a
`docs_lookup` token, but every mutating action is proposal-gated.

| `action`         | Params                                                                                | Notes |
|------------------|---------------------------------------------------------------------------------------|-------|
| `get`            | `entry_id` (required).                                                                | WS `config_entries/get_single`. Returns `{entry_id, entry}`. |
| `disable`        | `entry_id`, `proposal_id`.                                                            | WS `config_entries/disable` with `{entry_id, disabled_by: "user"}`. Proposal-gated. |
| `enable`         | `entry_id`, `proposal_id`.                                                            | Routes to WS `config_entries/disable` with `{entry_id, disabled_by: null}`. Proposal-gated. HA has no separate `enable` frame. |

## Planned (not yet registered)

The following command groups are designed but not yet implemented.
They will be registered in the dispatcher as each phase ships.

- All six `ha.config.*` domains are now registered (see sections above).
- **`docs.*`** — versioned HA docs lookup (`docs.lookup`,
  `docs.search`, `docs.versions`).
- **`ha.supervisor.*`** — Supervisor API wrappers (info, addons,
  snapshots).
- **`assist.*`** — conversation relay commands. Superseded by the
  shipped dual-websocket relay, which uses `chat.send` +
  `sessions.messages.subscribe` over the operator-role gateway
  connection instead of custom command types.
