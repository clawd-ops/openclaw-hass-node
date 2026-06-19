# OpenClaw Node Add-on Changelog

## 2026.6.8a11 (2026-06-19)

### Bug fixes
- **#108 — node.pending.pull schema drift (#112):** The post-pair drain step was sending `{maxItems: 50}` and parsing an `{items, hasMore}` envelope, but the canonical gateway schema is `node.pending.pull` with EMPTY params and a `{nodeId, actions: [...]}` response, where each action is a flat invoke envelope. Live gateway rejected the addon's request with `INVALID_REQUEST: unexpected property "maxItems"`. Now matches canonical: empty params, single-shot, `actions[]` parsed directly. Also normalizes `paramsJSON: null` (legitimate for no-param commands like `ping`) to `"{}"` so the action isn't silently lost to `INVALID_PARAMS` after ack.

## 2026.6.8a10 (2026-06-19)

### Bug fixes
- **#109 — SUPERVISOR_TOKEN now reliably injected (#110):** Enabled `hassio_api: true` in the addon manifest. HA Supervisor reportedly injects `SUPERVISOR_TOKEN` for `homeassistant_api: true` alone per the docs, but three consecutive fresh installs (2026-06-08, -06-09, -06-19) came up without it, leaving `ha.*` commands stuck at `HA_NOT_CONFIGURED`. Enabling `hassio_api` makes the token land deterministically. No `hassio_role` is set, so privilege stays minimal (default role grants Supervisor `info` endpoints only — no addon/backup/host mutation; the node code does not use Supervisor REST at all today).
- **`reset_pairing` schema reverted to `bool?` (#106):** The a9 change to `str?` broke add-on installs whose options store had `reset_pairing: false` (a bool); HA's options-schema validation runs against the SAVED value, so the Configuration tab refused to save until users manually typed a string. Schema is back to `bool?` (UI is a simple toggle: false=no-op, true=token-wipe). Identity-mode wipe is reachable for power users via `OPENCLAW_RESET_PAIRING=identity` in the addon container env.

### Migration
- Upgrading from a8 (or earlier): Configuration tab loads cleanly again; no action required.
- Upgrading from a9: if you saved a string value (`"none"`, `"token"`, `"identity"`) under a9's transient schema, the value is invalid under `bool?` and the Configuration tab will refuse to save until you change the field. Toggle it to `false` once.

## 2026.6.8a9 (2026-06-19)

### Bug fixes
- **#102 — `reset_pairing` modes (#104):** The previous `reset_pairing: true` wiped both the device token AND the Ed25519 device identity, which invalidated any bootstrap previously issued by the gateway and produced `AUTH_TOKEN_MISMATCH` on the next pair. `reset_pairing` is now a string mode:
  - `none` (default) — no-op.
  - `token` — wipe only the device token; keep identity. Re-pair the SAME device record with a fresh bootstrap. This is the safe recovery path. Legacy boolean `true` resolves to this.
  - `identity` — wipe both. Becomes a brand-new device. You MUST generate a fresh setup-code AFTER this wipe (the log warns explicitly).
  Existing add-on installs with `reset_pairing: false` continue to load unchanged.
- **#103 — Exponential backoff on `AUTH_RATE_LIMITED` (#104):** The reconnect loop was a flat 5 s, so after the gateway rate-limited us we kept extending the rate-limit window. Now uses exponential backoff (30 s → 300 s) specifically on `AUTH_RATE_LIMITED` / `recommendedNextStep=wait_then_retry`. Backoff resets on the next successful auth.

## 2026.6.8a8 (2026-06-19)

### Bug fixes
- **#98 part 1 — `reset_pairing` add-on option (#99):** Add a `reset_pairing: bool` toggle. When `true`, on startup the node deletes the persisted `device_token` and device identity under `/data/openclaw`, then re-pairs with the bootstrap token from `pairing_token`. Set back to `false` after the node reconnects successfully. This is the user-facing escape hatch from `AUTH_TOKEN_MISMATCH` reconnect loops caused by stale persisted device tokens.
- **#98 part 2 — `node.pending.pull` response correlation (#100):** The drain step after a successful pair was reading the next WebSocket frame as the response, but the gateway may interleave events (e.g. `connect.approved`, queued `node.invoke.request`) before the actual response. That misread produced the `node.pending.pull failed: None` log line and tore down the otherwise-healthy session. Responses are now correlated by request id; events are dropped silently and the real response is matched.

### Dependencies
- `cryptography` bumped to `>=48.0.1` for GHSA-537c-gmf6-5ccf.

### Known issues
- **#98 part 3 (still open):** Dual-role reconnect may still send a stale device token from one role briefly after the other role pairs cleanly, until the file-reload cycle on the next reconnect catches up. With the pending.pull fix the timing window narrows significantly. If you still see one role looping `AUTH_TOKEN_MISMATCH` after a successful pair, toggle `reset_pairing: true` once to force a clean state.
