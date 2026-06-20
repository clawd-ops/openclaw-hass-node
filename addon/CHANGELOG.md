# OpenClaw Node Add-on Changelog

## Unreleased

### Features
- **`ha.addon_info` command (read-only) — explicitly secrets-safe.** Returns per-addon metadata via `GET http://supervisor/addons/<slug>/info` with a strict allowlist (`slug, name, state, description, version, version_latest, update_available, boot, startup, stage, arch, machine, ingress, ingress_port`). The Supervisor response also contains the addon's current `options` (config VALUES — passwords, API keys, URLs), `schema` (option field NAMES), and `repository` (addon source — a repo URL for community/private addons, which can leak operator-private hostnames). **All three are dropped at the boundary.** Option values are the obvious leak vector, schema field names alone reveal which integrations are configured (e.g. an `mqtt_password` schema field implies MQTT auth is active), and `repository` is dropped entirely rather than allowlisting the well-known `core`/`local` values because that set drifts with Supervisor releases. Similarly dropped: `hostname`, `ip_address`, capability flags (`homeassistant_api`/`hassio_api`/`privileged`/`audio`/`video`/`gpio`/`usb`/etc.), discovery wiring, and raw URLs. Supervisor response body is also capped at 1 MiB before parsing so a misbehaving Supervisor (or a giant `long_description`) cannot drive the node OOM. Rationale lives in a long comment block on `_ADDON_INFO_FIELDS` in `commands/ha.py`. Tests assert each forbidden field is absent — that invariant is the whole point of the command.
- **`ha.addon_stats` command (read-only).** Returns allowlisted runtime metrics from `GET http://supervisor/addons/<slug>/stats`: CPU percent, memory usage/limit/percent, network rx/tx, block read/write. Any future Supervisor field is dropped by default so a release that adds a sensitive field cannot leak through.
- **`ha.addon_changelog` command (read-only).** Markdown body from `GET http://supervisor/addons/<slug>/changelog`, bounded to a 1 MiB trailing window via `supervisor_get_text`.
- **`ha.addon_documentation` command (read-only).** Markdown body from `GET http://supervisor/addons/<slug>/documentation`, bounded to a 1 MiB trailing window.
- **`ha.list_addons` command (read-only).** Companion to `ha.addon_logs` — the slug-required form is useless without a discovery path. Hits `GET http://supervisor/addons` via a new `supervisor_get_json` helper and returns a fixed, non-sensitive subset per add-on: `slug`, `name`, `state`, `version`, `version_latest`, `update_available`. All other Supervisor fields (notably `options`/`boot`/`repository`) are dropped at the boundary so the read-only surface cannot leak per-addon configuration. Same read-only-by-construction guarantee as `ha.addon_logs`. Tests cover happy path, field filtering, malformed-entry tolerance, three `HA_BAD_RESPONSE` shapes, and `SUPERVISOR_UNAVAILABLE`.
- **`ha.addon_logs` command (read-only).** Fetches the most recent Supervisor add-on log lines via `GET http://supervisor/addons/<slug>/logs` and returns them through the node's existing command dispatcher. Step toward sunsetting subagent reliance on the `mcp__homeassistant__*` MCP server and the long-lived `HASS_TOKEN`; the supervisor-token path keeps the call privileged-but-read-only by construction (Supervisor's `/addons/<slug>/logs` is GET-only). Params: `slug` (required, validated as `[a-z0-9_-]{1,128}`) and `lines` (optional, clamped 1–5000, default 200). Returns `{ok, slug, lines, log}` from a bounded 1 MiB trailing byte window before line trimming. New `supervisor_get_text` helper in `ha_client` issues the request without ever combining the supervisor token with a non-supervisor URL. Tests cover slug validation, lines clamping, tail-trim correctness, bounded byte retention, and the four error paths (`SUPERVISOR_UNAVAILABLE`, `HA_AUTH`, `HA_NOT_FOUND`, `HA_HTTP_ERROR`).

## 2026.6.19b2 (2026-06-20) — Slow-turn progress for HA Assist streaming

### Fixes
- **Slow-turn progress keeps HA Assist's stream alive.** HA Assist enforces a ~30s read timeout on the conversation stream. On tool-heavy turns the gateway can stay silent for tens of seconds between `chat.send` ack and the first assistant delta while it runs tool calls (calendar lookups, file reads, web searches). HA closed the stream during that gap and the user saw "node not responding" even though the real reply landed later — short conversational turns worked fine, "what is my schedule" / "how do the tanks look" did not. `ChatRelay.stream_turn` now emits one visible `Working on it...` progress delta after `_STREAM_FIRST_KEEPALIVE_S` (8s) of silence, then transport-only keepalive frames every `_STREAM_KEEPALIVE_INTERVAL_S` (15s) thereafter. The HACS shim now uses a read-gap timeout instead of a fixed 35s total request timeout, and streaming relay turns can run up to 180s. Real assistant deltas reset the timer so fast turns never see progress noise. New tests: `test_stream_turn_emits_keepalive_during_silent_gap`, `test_stream_turn_no_keepalive_on_fast_turn`, and `test_assist_turn_stream_emits_keepalive_frames`.

### Known issues / follow-ups
- **#128 follow-up turn-boundary race** — first identified during this release cycle, still on a follow-up branch. The original #128 fix closed the pre-`chat.send`-ack stale-trailer window; the post-ack window (a `runId`-less `session.message` arriving from the prior turn's broker queue after the new turn's ack but before its real terminal) remains open. Cleanly closing it requires either gateway-side coordination to put `runId` on `session.message` events, or a follow-up-turn detection signal that doesn't depend on a captured terminal. Tracked separately; not blocking this beta.

## 2026.6.19b1 (2026-06-20) — HA Assist streaming + beta promotion

### Features
- **#118 follow-on — HA Assist streaming relay.** The node now exposes `/v1/conversation/stream` as an NDJSON stream of delta frames (`{"delta": "..."}` chunks terminated by `{"done": true}` or `{"error": "<code>"}`). `ChatRelay` forwards each gateway `state='delta'` chat event straight through as a frame instead of buffering until `state='final'`. The HACS shim sets `_attr_supports_streaming = True` and feeds the frames into HA's `chat_log.async_add_delta_content_stream`, so HA Assist renders the reply token-by-token in the UI instead of dumping the whole turn at the end. The buffered `/v1/conversation` endpoint remains for non-streaming callers; behaviour there is unchanged.

### Project
- **Promoted alpha → beta.** Pair, connect, tool invokes, and Assist conversation (now streaming) all work end-to-end. Versioning moves from the `aN` track (`2026.6.8a1` … `2026.6.8a16`) to the `bN` track starting at `2026.6.19b1`. The no-back-compat pre-1.0 rule still applies. HACS package name updated to `OpenClaw Gateway (Beta)`; docs (`README.md`, `docs/INSTALL.md`, `docs/OVERVIEW.md`, `docs/STATUS.md`, `docs/PACKAGING.md`, `docs/UAT-PLAN.md`, `docs/COMMAND-SURFACE.md`, `docs/RELEASE.md`, `docs/CONTRIBUTING.md`, `docs/PLAN.md`) updated to match.

## 2026.6.8a16 (2026-06-20)

### Bug fixes
- **#118 part 2 — Assist replies no longer truncate mid-stream (#124).** After the a15 canonical-key fix, replies were arriving truncated: short answers like "Hey" survived, but longer ones came back as "I'm Cl" or "Not" instead of "I'm Clawd 🦀..." or "Not yet...". Cause: the gateway streams the assistant reply as `state='delta'` chunks followed by a single `state='final'` event with the COMPLETE text; the relay was firing the reply waiter on the first delta and returning whatever fragment was in chunk 1. The relay now only fires on terminal events (`state='final'` chat events or `session.message` events); deltas accumulate into a running buffer that's only returned as a best-effort partial when the turn times out. A separate terminal-text dict (caught by Codex review) prevents a delta-before-ack race from short-circuiting the fast-path return.

## 2026.6.8a15 (2026-06-20)

### Bug fixes
- **#118 — HA Assist conversation agent now actually replies (#122).** Root cause: the gateway emits session/chat events under a CANONICAL sessionKey (e.g. `agent:clawd:ha-assist:01kvh...` lowercased) returned in the `sessions.messages.subscribe` response. The addon was sending the raw key (`ha-assist:01KVH...`) and ignoring the canonical one the gateway gave back, so every event was filtered out as "wrong sessionKey" — producing NO_REPLY even when the agent had answered. Addon now captures the canonical key from the subscribe response and keys all internal state by it. Verified live against the v2026.6.8a14 diagnostic logs.
- `@roblandry` added as repo + HACS integration codeowner.

## 2026.6.8a14 (2026-06-20)

### Diagnostics
- Live INFO-level `[relay-diag]` logging in `ChatRelay.handle_event` for the #118 NO_REPLY investigation. No PII / message content — only structural metadata (event name, sessionKey, role, runId, state, subscription state). Will be downgraded to DEBUG in a15+.

## 2026.6.8a13 (2026-06-20)

### Bug fixes
- **#109 — `SUPERVISOR_TOKEN` now actually injected (#116).** The real root cause: `run.sh` used `#!/usr/bin/env sh`, which bypasses the s6-overlay `with-contenv` wrapper that exposes Supervisor's injected env to the script. `hassio_api`/`homeassistant_api` flags set the token in the container env but `with-contenv` is what brings it through to the entrypoint. Shebang changed to `#!/usr/bin/with-contenv sh`. After this release, `ha.list_states` and the rest of `ha.*` work out of the box on a clean install — no long-lived-token workaround needed. Added a startup log line `[run.sh] SUPERVISOR_TOKEN injected (len=…)` or a stderr WARNING when missing, so any future regression of this class is visible at first glance in the addon log.

### Docs
- Standalone-Docker references in README, INSTALL, and PACKAGING rewritten to call out that the Docker image is HA-only during alpha (the with-contenv shebang is only available in the HA base-python image). Dev-host standalone (`python -m openclaw_node`) is unaffected.

## 2026.6.8a12 (2026-06-19)

### Bug fixes
- **#98 part 3 — per-role device-token persistence (#114).** Both gateway WS connections (node + operator) used to share a single `device-token` file. The gateway issues distinct tokens per role on a dual-role pairing, so last-writer-wins clobbered one role's token; on restart that role looped `AUTH_TOKEN_MISMATCH`. Each role now persists to its own `device-token.<role>` file. The legacy `device-token` is auto-migrated to `device-token.node` on first start of a12 (symlinks refused, file relanded at mode 0o600). Also parses `hello-ok.auth.deviceTokens` (the dual-role bootstrap map) so a node-role bootstrap can seed the operator-role token file too. Role names are allowlisted to prevent filename-injection attacks via forged `deviceTokens` keys.

### Migration
- Upgrading from a8–a11 with a working pair: legacy `/data/openclaw/device-token` is moved to `/data/openclaw/device-token.node` on first start; operator gets a fresh token on its next successful connect via the new dual-role parsing. No user action required.

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
