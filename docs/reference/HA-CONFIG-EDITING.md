# HA Config Editing — per-domain

> **Rule zero (HARD)**: HA-managed config is edited through HA-native
> APIs, not the filesystem. `fs.patch` is reserved for files HA has no
> API for. `.storage/` is read-only to the node — writes are refused
> at the dispatcher unless the call carries an explicit
> `--unsafe-storage` flag AND the user accepts the proposal. This is
> not a guideline; it is enforced in the command layer.

Each `ha.config.<domain>.*` command goes straight to HA's REST/WS
config endpoint for that domain, regardless of whether the user's
current setup stores it in YAML or `.storage/`. The node does **not**
choose between yaml and storage edits — it asks HA.

## Decision: fs.patch vs ha.config.*

Use `ha.config.<domain>.*` (HA-native) for:

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

- List: `GET /api/config/automation/config` or WS
  `config/automation/list`
- Get: `GET /api/config/automation/config/<id>`
- Set: `POST /api/config/automation/config/<id>` (proposal-gated)
- Delete: `DELETE /api/config/automation/config/<id>` (proposal-gated)
- After mutation: `ha.call_service automation reload`

## Scripts (HA-native)

- Same shape as automations under `/api/config/script/config/<id>`.
- Reload: `script reload`.

## Scenes (HA-native)

- `/api/config/scene/config/<id>`. Reload: `scene reload`.

## Dashboards / Lovelace (HA-native WS)

Registered commands (see `docs/reference/COMMAND-SURFACE.md`):

- `ha.config.lovelace.get` — WS `lovelace/config` with an optional
  `url_path` payload field (omit for the default dashboard).
- `ha.config.lovelace.save` — WS `lovelace/config/save`. **Proposal-gated**:
  caller must pass a non-empty `proposal_id` naming an agent-bridge
  proposal. `proposal_id="direct"` is refused so every mutation is
  traceable to a review record.
- `ha.config.lovelace.dashboards_list` — WS `lovelace/dashboards/list`.
- `ha.config.lovelace.resources_list` — WS `lovelace/resources`.
- `ha.config.lovelace.resources_create` — WS `lovelace/resources/create`,
  proposal-gated (same rules as `save`). `res_type` must be one of
  `module`, `css`, `js`, `html`.

**Why HA-native, not fs.patch on `.storage/lovelace*`.** HA owns the
lovelace `.storage/` files at runtime and rewrites them without warning.
Direct filesystem writes race with HA, skip the WS-level validation, and
would be silently reverted on the next Frontend action. The
`STORAGE_READONLY` refusal in `fs.write` / `fs.patch` still applies — any
path-based mutation under `/config/.storage/` is refused at the
dispatcher and callers are redirected here.

## Helpers (HA-native)

- Read via state + entity registry.
- Mutate via WS `config/<helper_type>/create|update|delete`
  (e.g. `config/input_boolean/create`).

## Area / device / entity registry (HA-native WS)

- `config/area_registry/{list,create,update,delete}`
- `config/device_registry/{list,update}`
- `config/entity_registry/{list,get,update,remove}`

## Integrations / config entries (HA-native WS)

- `config_entries/get`, `config_entries/options/flow/...`,
  `config_entries/disable|enable`.
- Always cite a `docs.lookup` for the integration before mutating.

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
