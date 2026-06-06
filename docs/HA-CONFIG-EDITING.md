# HA Config Editing — per-domain

> **Rule zero**: never write to files under `/config/.storage/` directly.
> HA owns those files; direct edits risk corruption and silent state
> drift. Use the HA REST or WS config endpoints instead.

Each `ha.config.<domain>.*` command detects the current storage mode and
routes appropriately. Mode detection: presence of the YAML file in
`/config/` (e.g. `automations.yaml`) indicates YAML mode; otherwise UI
mode in `.storage/`.

## Automations

- **YAML mode** — `/config/automations.yaml`
  - Read: `fs.read /config/automations.yaml`
  - Write: proposal-gated `fs.patch`
  - Pickup: `ha.call_service automation reload`
- **UI mode** — `.storage/automation`
  - Read: `GET /api/config/automation/config/<id>`
  - Write: `POST /api/config/automation/config/<id>` (proposal-gated wrapper)
  - List: `GET /api/states` filtered to `automation.*`

## Scripts

- YAML: `/config/scripts.yaml` → reload `script`
- UI: `/api/config/script/config/<id>`

## Scenes

- YAML: `/config/scenes.yaml` → reload `scene`
- UI: `/api/config/scene/config/<id>`

## Dashboards (Lovelace)

- YAML: `/config/ui-lovelace.yaml` + `/config/dashboards/*.yaml`
  - Reload via UI or `homeassistant.reload_core_config`
- UI (storage mode): `.storage/lovelace`, `.storage/lovelace.<dashboard>`
  - Read/write via WS commands:
    - `lovelace/config` (get)
    - `lovelace/config/save` (set; proposal-gated)
    - `lovelace_<dashboard>/config` for per-dashboard

## Blueprints

- Always `/config/blueprints/<domain>/<author>/<slug>.yaml`
- Read/write as files (proposal-gated `fs.patch`).
- After change: `ha.call_service automation reload` (or
  `script reload`) so consumers re-evaluate.

## Helpers (input_*, counters, timers, etc.)

- YAML mode: `/config/configuration.yaml` (or split files)
- UI mode: `/api/config/helpers/...` (varies per helper type)

## Validation before reload

For any YAML change in `/config/`:

1. `ha.call_service homeassistant check_config` (or
   `POST /api/config/core/check_config`).
2. Only proceed if `errors` is empty.
3. Then call the targeted `reload_<domain>` service.

## What we never touch directly

- `.storage/auth*`
- `.storage/core.*` (config entries, area registry, entity registry,
  device registry) — manipulate via WS API only
  (`config/area_registry/...`, etc.).
- `home-assistant_v2.db`
- `*.log`, `home-assistant.log.*`
