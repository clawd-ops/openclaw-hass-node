# Command Surface

> **Beta and not yet canonical.** This narrative contains known parameter,
> availability, and policy drift. Use the generated
> [command coverage ledger](COMMAND-COVERAGE.md) for the source-reconciled
> registry/action/caller inventory and
> [the dated verification](../VERIFICATION-2026-09-11.md) for observed defects.
> Registration is not proof that a command is advertised, reachable, or working.

Commands intended for the `node.invoke` surface. Group prefixes match OpenClaw
conventions where they exist. The dispatcher registers 56 commands
(`ping` + `fs.*` × 11 + `system.*` × 5 + `ha.*` × 39) and the node advertises
the same 56. The four exec-approval methods (`system.run`,
`system.run.prepare`, `system.execApprovals.get`, `system.execApprovals.set`)
are advertised, and `_INTENTIONALLY_UNADVERTISED` in `gateway_ws.py` is
empty; the `test_advertised_matches_registry` drift gate keeps advertisement
and registration in sync (see #260).
Direct `nodes.invoke system.run` and `nodes.invoke system.run.prepare` are
refused by the Gateway; both commands are only reachable through the
OpenClaw exec tool with `host=node`, which prepares a canonical
`systemRunPlan`, prompts an operator, and forwards the approved plan to the
node. The generated ledger records advertisement and availability separately.

Convention for the `ha.config.*` domain: **one command per HA config
domain**, with an `action` parameter selecting the operation. This keeps
the dispatcher, gateway allowlist, and connect-surface advertisement
compact as the nine domains land. Per-verb commands
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
| `fs.read`     | `path`, `encoding?`, `max_bytes?`, `offset?`, `length?` | `encoding` = `utf-8` (default) or `binary` (base64-encoded bytes). `offset` / `length` are **byte** offsets against the raw file, independent of `encoding`; a ranged read that splits a multi-byte UTF-8 sequence returns `DECODE_ERROR` (use `encoding=binary` for arbitrary byte ranges). `offset` must be a non-negative integer (bool rejected); `length` must be a positive integer when supplied. `offset > file_size` fails closed with `OFFSET_BEYOND_EOF` — never returns unrelated bytes as success; `offset == file_size` is valid and returns an empty slice with `eof=true`. When `length` is supplied, `max_bytes` is the safety ceiling: `length > max_bytes` returns `TOO_LARGE` up-front; otherwise exactly up to `length` bytes are returned. When `length` is omitted the handler reads to EOF from `offset`, still bounded by `max_bytes`. Returns `{content, size, file_size, offset, length, eof, sha256}`; `size`, `length`, and `sha256` describe the returned slice, not the whole file. |
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

## `system.*` — shell and exec approvals (5 commands)

| Command        | Args                               | Notes                  |
|----------------|-------------------------------------|------------------------|
| `system.run`   | `command` (argv list), `systemRunPlan`, `runId`, `approved?`, `approvalDecision?`, `approvalSource?`, `cwd?`, `rawCommand?`, `env?`, `timeoutMs?`, `agentId?`, `sessionKey?`, `proposalId?` | Executes an operator-approved plan. Reached only through the OpenClaw exec tool with `host=node` after `system.run.prepare` produces a canonical `systemRunPlan` and an operator approves. The Gateway rejects direct `nodes.invoke system.run` and rejects a forward whose `command`/`rawCommand`/`cwd`/`agentId`/`sessionKey` mutates between prepare and forward. The node fails closed unless the forward carries `systemRunPlan`, a non-empty `runId`, and one of `approved=true` / `approvalDecision` in `{allow-once,allow-always}` / `approvalSource`; it re-runs the prepare-time validators, re-resolves `cwd` under the allowed roots (or the HA `/config` hierarchy in add-on mode), cross-checks argv / cwd / commandText / agentId / sessionKey against the stored plan, and rejects env keys matching `TOKEN`/`SECRET`/`KEY`/`PASS`/`CREDENTIAL`/`AUTH`/`PWD`. Timeout is read from `timeoutMs` (native wire); the successful payload uses `success`/`exitCode`/`timedOut`. `proposalId` is accepted as audit metadata only (it is not part of the Gateway's forward whitelist). There is no `admin_token`; `OPENCLAW_ADMIN_TOKEN` was inert and has been removed. |
| `system.run.prepare` | `command` (argv list), `cwd?`, `rawCommand?`, `env?`, `agentId?`, `sessionKey?` | Prepares the canonical `systemRunPlan` an operator will see; executes nothing. See [Authorization model](../design/AUTHORIZATION-MODEL.md). |
| `system.execApprovals.get` | — | Returns the node's persisted exec-approval snapshot (`path`, `exists`, `hash`, redacted `file`). |
| `system.execApprovals.set` | `file`, `baseHash?` | Replaces the exec-approval document under an atomic file lock with hash-based concurrency check. |
| `system.which` | `binary`                            | Lookup only, basename-only |

## `ha.*` — Home Assistant control (39 commands)

Includes the base observability/control surface (28), Tier B addon
lifecycle including update (1 extra), and the nine `ha.config.*`
domain-config editors (9). Sections for each `ha.config.*` command
follow the base surface below.


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
| `ha.call_service`         | `domain`, `service`, `target?`, `data?`; `service_data?` compatibility alias. Interim node policy denies lifecycle, update, reload, host, shell, and shutdown effects before HA I/O (see below) |
| `ha.list_areas`           | Via WS API                             |
| `ha.list_devices`         | Via WS API                             |
| `ha.list_entity_registry` | Via WS API                             |
| `ha.logbook`              | `entity_id?`, `start_time?`, `end_time?` (REST). Values are percent-encoded, so both `Z` and `+00:00` offsets work and an entity cannot inject query parameters. The Assist tool accepts `start`/`end` and maps them, but a direct invoke must use `start_time`/`end_time` or the bound is silently dropped |
| `ha.history`              | `entity_ids?` (list), `start_time?`, `end_time?`, `minimal_response?`, `no_attributes?`, `significant_changes_only?` (REST). Values are percent-encoded, so both `Z` and `+00:00` offsets work and an entity cannot inject query parameters. **Known defect:** an unknown entity returns `{ok: true, count: 0}`, indistinguishable from real empty history. The Assist tool accepts `entity_id`/`start`/`end` and maps them, but a direct invoke must use the node names or the filter and bounds are silently dropped |
| `ha.reload_config`        | `domain?` (only `core`, omitting is equivalent), `admin_token`; gated by `OPENCLAW_ADMIN_TOKEN`. Calls `homeassistant.reload_core_config` and returns `{ok: true, domain: "core"}`. Per-domain reload is **not implemented**: any other `domain` is rejected with `UNSUPPORTED` rather than silently reloading core config. `ha.call_service` is not a bypass — `*.reload` is on the interim denylist. **Unverified authorization:** the admin-token gate is not the ratified model and moves to operator approval (see [`design/AUTHORIZATION-MODEL.md`](../design/AUTHORIZATION-MODEL.md)) |
| `ha.light_turn_on`        | `entity_id` or `area_id` or `device_id` |
| `ha.light_turn_off`       | `entity_id` or `area_id` or `device_id` |
| `ha.list_automations`     | `include_traces?`, `entity_filter?` (fnmatch glob scoped to `automation.`, max 256 chars), `state_filter?` (exact match, max 256 chars); unknown params rejected; narrowing applied before trace lookup |
| `ha.check_config`         | Validates HA core config before reload |
| `ha.addon_logs`           | `slug`, `lines?` (1–5000, default 200); Supervisor add-on logs, read-only; trims from a bounded 1 MiB trailing byte window |
| `ha.list_addons`          | List Supervisor add-ons (slug, name, state, version, version_latest, update_available), read-only. `repository` is dropped because for community/private addons it holds an operator-private repo URL |
| `ha.addon_info`           | `slug`; per-addon metadata (slug, name, state, description, version, version_latest, update_available, boot, startup, stage, arch, machine, ingress, ingress_port). **`options` (current option VALUES), `schema` (option field NAMES), and `repository` are dropped at the boundary** — option values are secrets, schema field names can reveal which integrations are configured, and `repository` for non-core addons can leak an operator-private hostname. Supervisor response body is also capped at 1 MiB before parsing. Read-only |
| `ha.addon_stats`          | `slug`; allowlisted utilisation metrics (cpu_percent, memory_usage/limit/percent, network_rx/tx, blk_read/write). Read-only |
| `ha.addon_changelog`      | `slug`; addon changelog markdown, bounded 1 MiB trailing window. Read-only |
| `ha.addon_documentation`  | `slug`; addon documentation markdown, bounded 1 MiB trailing window. Read-only |
| `ha.addon_start`          | `slug`; Tier B lifecycle command. Requires `allowAdminOps` + explicit `addon_lifecycle.allowlist` opt-in, authenticated via pairing session (no admin token). Always denied for `homeassistant`, `supervisor`, and `core_*` slugs |
| `ha.addon_stop`           | `slug`; same Tier B lifecycle gate as `ha.addon_start`; idempotent when already stopped |
| `ha.addon_restart`        | `slug`; same Tier B lifecycle gate as `ha.addon_start` |
| `ha.addon_update`         | `slug`; same Tier B lifecycle gate as `ha.addon_start`; updates the add-on to the latest available version (`POST /addons/<slug>/update`) |
| `ha.update_install`       | `entity_id` (required, must be `update.*`), `backup` (optional bool), `version` (optional str), `admin_token`; Tier B admin gate via `OPENCLAW_ADMIN_TOKEN`; installs a pending update via HA's `update.install` service — covers HACS integrations, HA Core, add-ons via the `update.*` entity domain. Distinct from `ha.addon_update` (Supervisor API, slug-based) |

## Service payload and result contract (unreleased, #266)

Both direct `ha.call_service` and Assist `ha_call_service` use **`data`** as
the canonical service payload object. Existing Assist callers may continue to
send `service_data`; the wrapper normalizes it to `data`, and the node accepts
the alias for older wrappers/direct callers. If both fields are present they
must contain equal JSON values (object key order and signed zero are immaterial;
booleans remain distinct from numbers), otherwise `INVALID_PARAM` is returned before
any HA request. Explicit null, arrays, and scalar payloads are invalid; omit
the field or send `{}` for no service data. Nested values and brightness options
are preserved. `target` is flattened into HA's REST body as before and takes
precedence over same-named fields in `data`. This repair does not introduce
the final approval-aware per-service policy or additional light-control
approvals.

### Interim privileged-service containment (#287)

`ha.call_service` accepts only the documented parameters and canonical
lowercase `domain` / `service` names of at most 64 characters. Unknown keys,
malformed names, and
conflicting `data` / `service_data` aliases return `INVALID_PARAM` before Home
Assistant I/O. Leading and trailing whitespace is normalized consistently.

Until the complete effect-policy and operator-approval work lands, the node
returns `SERVICE_DENIED` for these generic paths:

| Denied key or family | Dedicated or future path |
| --- | --- |
| `hassio.addon_start` / `stop` / `restart` / `update` and `hassio.app_start` / `stop` / `restart` / `update` | Dedicated `ha.addon_start` / `stop` / `restart` / `update` commands with node slug policy |
| `hassio.host_reboot`, `host_shutdown`, `host_update`, `supervisor_update` | Future host/Supervisor lifecycle and update approval paths |
| `hassio.addon_stdin`, `app_stdin` | Future approval-aware service effect policy |
| `hassio.mount_reload` | Dedicated reload/configuration policy path |
| `update.*` | `ha.update_install` with operator approval |
| `shell_command.*` | `system.run` with native exec approval |
| `python_script.*`, `command_line.*` | Future approval-aware service effect policy |
| `homeassistant.restart`, `homeassistant.stop` | Future lifecycle/shutdown approval path |
| Any `reload` or `reload_*` service | Dedicated reload/configuration policy path |

Ordinary principal-authorized operations remain available. In particular,
`light.turn_on` continues to call Home Assistant directly. This denylist is
bounded Phase 0 containment and does not duplicate or replace OpenClaw's native
approval lifecycle.

A service response that is not a changed-state list fails with `HA_BAD_RESPONSE`
instead of claiming an empty successful change. This does not roll back an HA
operation already sent; transport/response failures must not be retried blindly.

The node's `node.invoke.result` envelope now sets `ok: false` for handler
failures, retaining the handler payload and projecting its error code/message
into `error: {code, message}`. The plugin rejects both these failures and older
nodes' `ok: true` envelopes containing inner `ok: false`. Gateway SDK rejections
with `details.nodeError` retain the node error, too. The shared `HaCommandError`
reports `source: node` for node validation/refusal and `source: ha` for `HA_*`
errors. Authoritative SDK `GatewayClientRequestError` (or its protocol base)
rejections without `details.nodeError` use `source: gateway`, preserving the
gateway error code/message, details, and supplied `retryable` / `retryAfterMs`
metadata. Authorization, schema, and command-policy refusals are not transport
failures. Local/socket failures use `source: transport` / `TRANSPORT_ERROR`;
missing retryability is unknown, not permission to retry. A nested node error
takes precedence over its gateway wrapper. Malformed results fail with
`INVALID_RESULT`; successful legacy
payloads such as `ping` need not contain `ok`. No success text is rendered for
a rejected operation. These changes are source-only until this PR is released.

## HA config mutation availability

**Interim source behavior:** all mutating `ha.config.*` actions return
`PROPOSAL_REQUIRED` without making a Home Assistant request. The trusted
approval verifier and human approval round-trip are not implemented.
A `proposal_id`, even one described as approved by the caller, is not
authorization. There is no caller flag or admin-token override.

The mutation rows below retain the dormant API-adapter inputs for future
implementation; they do not advertise currently executable mutations.
Read-only `get` / `list` actions remain available. This restriction does not
change light control or the separate generic-service policy work.

## `ha.config.lovelace` — Lovelace dashboards (1 command)

HA-native WebSocket path for dashboards. See
`docs/reference/HA-CONFIG-EDITING.md` for the fs.patch vs ha.config
policy and the `.storage/` guardrail rationale.

Single command; the `action` param selects the operation. Unknown or
missing `action` returns `INVALID_PARAM`.

| `action`            | Params                                                                                                       | Notes |
|---------------------|--------------------------------------------------------------------------------------------------------------|-------|
| `get`               | `url_path?` (omit → default).                                                                                | WS `lovelace/config` with `{url_path}` in the payload when set. Returns `{url_path, config}`. |
| `save`              | `config` (dict, required), `url_path?`, `proposal_id` (audit metadata only).                 | WS `lovelace/config/save`. **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |
| `dashboards_list`   | —                                                                                                            | WS `lovelace/dashboards/list`. Returns `{count, dashboards}`. |
| `resources_list`    | —                                                                                                            | WS `lovelace/resources`. Returns `{count, resources}`. |
| `resources_create`  | `url` (required), `res_type` in {`module`,`css`,`js`,`html`}, `proposal_id` (audit metadata only). | WS `lovelace/resources/create`. **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |

Guardrail: attempts to reach lovelace `.storage/` files via `fs.write` /
`fs.patch` are refused with `STORAGE_READONLY`. Native config reads remain
available; native mutations are currently unavailable as described above.

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
| `get`      | `id` (required, HA slug: `^[a-z0-9_]+$`).                                                              | `GET /api/config/automation/config/<id>`. Returns `{id, config}`. |
| `save`     | `id` (required, HA slug: `^[a-z0-9_]+$`), `config` (dict, required), `proposal_id` (audit metadata only). | `POST /api/config/automation/config/<id>`. **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |
| `delete`   | `id` (required, HA slug: `^[a-z0-9_]+$`), `proposal_id` (audit metadata only).                           | `DELETE /api/config/automation/config/<id>`. **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |

Future approved-mutation adapters will need to follow up with
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
| `get`      | `id` (required, HA slug: `^[a-z0-9_]+$`).                                                              | `GET /api/config/script/config/<id>`. Returns `{id, config}`. |
| `save`     | `id` (required, HA slug: `^[a-z0-9_]+$`), `config` (dict, required), `proposal_id` (audit metadata only). | `POST /api/config/script/config/<id>`. **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |
| `delete`   | `id` (required, HA slug: `^[a-z0-9_]+$`), `proposal_id` (audit metadata only).                           | `DELETE /api/config/script/config/<id>`. **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |

Future approved-mutation adapters will need to follow up with
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
| `get`      | `id` (required, HA slug: `^[a-z0-9_]+$`).                                                              | `GET /api/config/scene/config/<id>`. Returns `{id, config}`. |
| `save`     | `id` (required, HA slug: `^[a-z0-9_]+$`), `config` (dict, required), `proposal_id` (audit metadata only). | `POST /api/config/scene/config/<id>`. **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |
| `delete`   | `id` (required, HA slug: `^[a-z0-9_]+$`), `proposal_id` (audit metadata only).                           | `DELETE /api/config/scene/config/<id>`. **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |

Future approved-mutation adapters will need to follow up with
`ha.call_service` `scene` / `reload` to pick up the new config.

## `ha.config.helpers` — Helpers (1 command)

HA-native WebSocket path for helper entities
(`input_boolean`, `input_text`, `input_number`, `input_select`,
`input_datetime`, `counter`, `timer`, `schedule`).

Single command; the `action` param selects the operation. `helper_type`
is required for the read-only `list` action. Missing / unknown `action`
returns `INVALID_PARAM`; missing `helper_type` on `list` returns
`MISSING_PARAM`, and an unsupported type returns `INVALID_PARAM`.
Mutation denial happens before payload validation.

HA's storage-collection websocket surface registers `<helper_type>/list`,
`<helper_type>/create`, `<helper_type>/update`, and `<helper_type>/delete`.
There is **no** `<helper_type>/get` frame — single-item lookup goes
through state and the entity registry, not this command. update/delete
use the item key named `<helper_type>_id` (e.g. `input_boolean_id`),
not `entity_id`.

| `action`   | Params                                                                                                            | Notes |
|------------|-------------------------------------------------------------------------------------------------------------------|-------|
| `list`     | `helper_type` (required).                                                                                         | WS `<helper_type>/list`. Returns `{helper_type, count, helpers}`. |
| `create`   | `helper_type`, `attrs` (dict, required), `proposal_id` (audit metadata only).                     | WS `<helper_type>/create` with the `attrs` dict as payload. **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |
| `update`   | `helper_type`, `<helper_type>_id` (required), `attrs` (dict), `proposal_id`.                                      | WS `<helper_type>/update` with `{<helper_type>_id, **attrs}`. **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |
| `delete`   | `helper_type`, `<helper_type>_id` (required), `proposal_id`.                                                      | WS `<helper_type>/delete` with `{<helper_type>_id}`. **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |

## `ha.config.area_registry` — Areas (1 command)

WS `config/area_registry/{list,create,update,delete}`.

| `action`   | Params                                                                                | Notes |
|------------|---------------------------------------------------------------------------------------|-------|
| `list`     | —                                                                                     | Returns `{count, areas}`. |
| `create`   | `name` (required), optional `attrs` (dict), `proposal_id`.                            | **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |
| `update`   | `area_id` (required), `attrs` (dict), `proposal_id`.                                  | **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |
| `delete`   | `area_id` (required), `proposal_id`.                                                  | **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |

## `ha.config.device_registry` — Devices (1 command)

WS `config/device_registry/{list,update}`. HA does not expose create
or delete for devices — they are populated by integrations.

| `action`   | Params                                                                                | Notes |
|------------|---------------------------------------------------------------------------------------|-------|
| `list`     | —                                                                                     | Returns `{count, devices}`. |
| `update`   | `device_id` (required), `attrs` (dict), `proposal_id`.                                | **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |

## `ha.config.entity_registry` — Entities (1 command)

WS `config/entity_registry/{list,get,update,remove}`.

| `action`   | Params                                                                                | Notes |
|------------|---------------------------------------------------------------------------------------|-------|
| `list`     | —                                                                                     | Returns `{count, entities}`. |
| `get`      | `entity_id` (required).                                                               | Returns `{entity_id, entity}`. |
| `update`   | `entity_id`, `attrs` (dict), `proposal_id`.                                           | **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |
| `remove`   | `entity_id`, `proposal_id`.                                                           | **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |

## `ha.config.config_entries` — Integrations (1 command)

WS `config_entries/get_single` (single lookup) and
`config_entries/disable` (both disable and re-enable — HA does not
register a separate `config_entries/enable` frame). Options flows are
served by HTTP flow views (`/api/config/config_entries/options/flow/...`)
and are not yet exposed by this command.

Version-matched documentation checks remain planned along with the trusted
approval bridge. All mutating actions are currently unavailable; no
`docs_lookup` token or `proposal_id` can enable them.

| `action`         | Params                                                                                | Notes |
|------------------|---------------------------------------------------------------------------------------|-------|
| `get`            | `entry_id` (required).                                                                | WS `config_entries/get_single`. Returns `{entry_id, entry}`. |
| `disable`        | `entry_id`, `proposal_id`.                                                            | WS `config_entries/disable` with `{entry_id, disabled_by: "user"}`. **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** |
| `enable`         | `entry_id`, `proposal_id`.                                                            | Routes to WS `config_entries/disable` with `{entry_id, disabled_by: null}`. **Unavailable: `PROPOSAL_REQUIRED`; no HA request.** HA has no separate `enable` frame. |

## Planned (not yet registered)

The following command groups are designed but not yet implemented.
They will be registered in the dispatcher as each phase ships.

- All nine `ha.config.*` domains are now registered (see sections above).
- **`docs.*`** — versioned HA docs lookup (`docs.lookup`,
  `docs.search`, `docs.versions`).
- **`ha.supervisor.*`** — Supervisor API wrappers (info, addons,
  snapshots).
- **`assist.*`** — conversation relay commands. Superseded by the
  shipped dual-websocket relay, which uses `chat.send` +
  `sessions.messages.subscribe` over the operator-role gateway
  connection instead of custom command types.
