# HA Config Editing — per-domain

> **Rule zero (HARD)**: HA-managed config is edited through HA-native
> APIs, not the filesystem. `fs.patch` is reserved for files HA has no
> API for. `.storage/` is read-only to the node — writes are refused
> at the dispatcher unless the call carries an explicit
> `--unsafe-storage` flag AND the user accepts the proposal. This is
> not a guideline; it is enforced in the command layer.

## Registered commands (naming convention)

The `ha.config.*` surface is exposed as **one command per HA config
domain**, with an `action` param selecting the operation
(`ha.config.<domain>` + `action`, not
`ha.config.<domain>.<verb>`). This keeps the dispatcher, gateway
allowlist, and connect-surface advertisement compact as the six planned
domains land. Missing / unknown `action` returns `INVALID_PARAM`.
Mutating actions remain proposal-gated inside the handler.

Each `ha.config.<domain>` command goes straight to HA's REST/WS config
endpoint for that domain, regardless of whether the user's current setup
stores it in YAML or `.storage/`. The node does **not** choose between
yaml and storage edits — it asks HA.

## Decision: fs.patch vs ha.config.*

Use `ha.config.<domain>` + `action` (HA-native) for:

- Automations, scripts, scenes
- Lovelace dashboards and views
- Helpers (input_*, counter, timer, schedule, etc.)
- Areas, devices, entity registry, labels, floors
- Integrations / config entries / options flows

Use `fs.patch` (proposal-gated) for:

- `configuration.yaml` top-level (only when the change has no HA API)
- YAML-only integrations (no config flow)
- Packages (`/config/packages/...`)
- `/config/custom_components/...`
- Themes (`/config/themes/...`)
- `/config/blueprints/...` (no REST API exists)
- Custom JS modules, www assets, user-authored yaml the user dropped
  in by hand

If both paths exist for a domain, **prefer HA-native**. The fs path
exists only as a fallback for things HA doesn't expose.

## Automations (HA-native)

Target shape: `ha.config.automation` with `action` in
{`get`, `save`, `delete`}.

- **Enumeration**: use the existing `ha.list_automations` command
  (reads `automation.*` entities from state). HA does not register a
  collection-level automation config route (`GET /api/config/automation/config`
  is not a valid HA endpoint — only the per-id form
  `GET /api/config/automation/config/<id>` exists via HA's shared
  `EditAutomationConfigView`).
- `action=get` → `GET /api/config/automation/config/<id>`
- `action=save` → `POST /api/config/automation/config/<id>`
  (proposal-gated)
- `action=delete` → `DELETE /api/config/automation/config/<id>`
  (proposal-gated)
- After mutation: `ha.call_service automation reload`
- **id validation**: the handler enforces `^[a-z0-9_]+$` (HA `cv.slug`)
  on `id` before building the REST path. Hyphens, dots, uppercase, path
  separators, query characters, and whitespace are all rejected with
  `INVALID_PARAM` before HA is called.

## Scripts (HA-native)

Target shape: `ha.config.script` with `action` in
{`get`, `save`, `delete`}.

- **Enumeration**: read `script.*` entities from state (e.g. via
  `ha.list_states`). HA does not register a collection-level script
  config route (`GET /api/config/script/config` is not a valid HA
  endpoint — only the per-id form `GET /api/config/script/config/<id>`
  exists via HA's shared `EditScriptConfigView`).
- `action=get` → `GET /api/config/script/config/<id>`
- `action=save` → `POST /api/config/script/config/<id>`
  (proposal-gated)
- `action=delete` → `DELETE /api/config/script/config/<id>`
  (proposal-gated)
- After mutation: `ha.call_service script reload`
- **id validation**: same slug rule as automations (`^[a-z0-9_]+$`).

## Scenes (HA-native)

Target shape: `ha.config.scene` with `action` in
{`get`, `save`, `delete`}.

- **Enumeration**: read `scene.*` entities from state (e.g. via
  `ha.list_states`). HA does not register a collection-level scene
  config route (only the per-id form via HA's shared
  `EditSceneConfigView`).
- `action=get` → `GET /api/config/scene/config/<id>`
- `action=save` → `POST /api/config/scene/config/<id>` (proposal-gated)
- `action=delete` → `DELETE /api/config/scene/config/<id>` (proposal-gated)
- After mutation: `ha.call_service scene reload`
- **id validation**: same slug rule as automations (`^[a-z0-9_]+$`).

## Dashboards / Lovelace (HA-native WS)

Registered as a single command; see
`docs/reference/COMMAND-SURFACE.md` for the full args table.

`ha.config.lovelace` with `action` in {`get`, `save`,
`dashboards_list`, `resources_list`, `resources_create`}. Actions:

- `get` — WS `lovelace/config` with an optional `url_path` payload
  field (omit for the default dashboard).
- `save` — WS `lovelace/config/save`. **Proposal-gated**: caller must
  pass a non-empty `proposal_id` naming an agent-bridge proposal.
  `proposal_id="direct"` is refused so every mutation is traceable to a
  review record.
- `dashboards_list` — WS `lovelace/dashboards/list`.
- `resources_list` — WS `lovelace/resources`.
- `resources_create` — WS `lovelace/resources/create`, proposal-gated
  (same rules as `save`). `res_type` must be one of `module`, `css`,
  `js`, `html`.

**Why HA-native, not fs.patch on `.storage/lovelace*`.** HA owns the
lovelace `.storage/` files at runtime and rewrites them without warning.
Direct filesystem writes race with HA, skip the WS-level validation, and
would be silently reverted on the next Frontend action. The
`STORAGE_READONLY` refusal in `fs.write` / `fs.patch` still applies — any
path-based mutation under `/config/.storage/` is refused at the
dispatcher and callers are redirected here.

## Helpers (HA-native)

Registered as `ha.config.helpers` with `action` in
{`list`, `create`, `update`, `delete`} plus a required `helper_type`
param (`input_boolean`, `input_text`, `input_number`, `input_select`,
`input_datetime`, `counter`, `timer`, `schedule`). See
`COMMAND-SURFACE.md` for the full args table.

- `list` → WS `<helper_type>/list`
- `create` / `update` / `delete` → WS
  `<helper_type>/{create,update,delete}`, proposal-gated
- HA's storage-collection surface has no `<helper_type>/get` frame;
  single-item lookup goes through state and the entity registry
- update/delete require the item key `<helper_type>_id` (e.g.
  `input_boolean_id`), not `entity_id`

## Area / device / entity registry (HA-native WS)

Registered as one command per registry. See `COMMAND-SURFACE.md` for
per-action args.

- `ha.config.area_registry` → `config/area_registry/{list,create,update,delete}`
  (mutations proposal-gated)
- `ha.config.device_registry` → `config/device_registry/{list,update}`
  (HA does not expose create/delete — devices are integration-populated)
- `ha.config.entity_registry` → `config/entity_registry/{list,get,update,remove}`

## Integrations / config entries (HA-native WS)

Registered as `ha.config.config_entries` with `action` in
{`get`, `disable`, `enable`}. See `COMMAND-SURFACE.md` for per-action
args. Mutating actions are proposal-gated. HA has no separate
`config_entries/enable` frame — `enable` routes through
`config_entries/disable` with `disabled_by=null`. Options flow support
would need the HTTP flow views under
`/api/config/config_entries/options/flow/...` and is not yet exposed.

**Convention (soft)**: callers should cite a `docs.lookup` for the
integration before mutating. The handler does not hard-enforce a
`docs_lookup` token — that check remains a caller-side discipline
enforced via prompt / proposal review.

## Blueprints (fs)

- `/config/blueprints/<domain>/<author>/<slug>.yaml`
- Proposal-gated `fs.patch`. No HA API for blueprint authoring.
- After change: `automation reload` (or `script reload`).

## `configuration.yaml` and packages (fs)

- Proposal-gated `fs.patch`.
- Pre-flight: `ha.check_config`.
- If valid, call the narrowest reload service that picks up the
  change; if none applies, surface that to the user — do not silently
  request a full HA restart.

## Custom components (fs)

- `/config/custom_components/<name>/` — proposal-gated `fs.patch`.
- After change: HA restart required for code reloads. Surface this in
  the proposal description; do not auto-restart.

## Validation, every time

Before applying any edit (HA-native or fs):

1. `docs.lookup <domain>` at the running HA core version.
2. `docs.breaking_changes` for the running version (and any versions
   since the touched domain was last edited).
3. For yaml edits: `ha.check_config`. Only proceed if errors empty.
4. For HA-native edits: use the API's own validation response.
5. If breaking change affects the edit, the proposal must include the
   functional fix and cite the breaking-change entry.

## What we never touch directly (HARD rule)

- Anything under `/config/.storage/` — read only.
- `home-assistant_v2.db`
- `*.log`, `home-assistant.log.*`
- Files HA writes during runtime (`.uuid`, ephemeral caches).

The only way to write `.storage/` is calling the command with
`unsafe_storage=true` AND an accepted proposal whose body says so
explicitly. Default refusal otherwise.
