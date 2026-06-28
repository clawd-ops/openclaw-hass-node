# OpenClaw Node Add-on Changelog

## 2026.6.28b5

### Fixes
- **Supervisor/HA HTTP errors no longer leak raw HTML into chat.** If an
  upstream hiccup returns an nginx-style HTML error page, the shared HA
  client now returns a concise `HA_HTTP_ERROR` message such as
  `Supervisor returned 504 (HTML error page suppressed)` instead of
  preserving the full document body for agents to paste back into
  Discord or HA Assist.

## 2026.6.28b4

### Fixes
- **HA Assist now surfaces hidden-session tool starts too.** b3 listened
  for gateway `stream="tool"` lifecycle events, but the hidden HA Assist
  path can deliver tool usage as `stream="item"` events with
  `kind="tool"` and a `name`/`title`. The relay now treats those starts
  as active-chat tool usage, so read-only HA tool calls such as listing
  areas or reading entity state produce visible `🔧 Calling X...` lines
  before the final response.

## 2026.6.28b3

### Fixes
- **HA Assist now shows every tool call as it starts.** In the legacy
  no-cap path, the add-on now pushes `🔧 Calling X...` into the active
  Assist chat immediately on each tool-start event. Multi-tool turns
  therefore show each tool usage in sequence instead of only showing one
  silence-gated progress line or going quiet between calls.

### Docs
- **Tracked follow-ups for tool-progress controls and log noise.**
  TODO now includes a future show/hide config option for HA Assist tool
  progress and a cleanup item to move noisy `[relay-diag]` lines from
  INFO to DEBUG.

## 2026.6.28b2

### Fixes
- **HA Assist tool progress restored (regression fix for b1).** On b1 the
  shim advertised `client_caps: ["tool-progress-frames"]` which made the
  addon suppress the legacy `🔧 Calling X...` textual delta in favour of
  structured `tool_progress` frames, but the shim has no ephemeral
  display hook in HA today so the frames were swallowed — HA Assist
  showed `...` for the entire tool-heavy turn. The shim no longer
  advertises the cap; addon emits the legacy textual delta exactly as
  on b6. The frame emission code stays in place for when HA exposes an
  ephemeral hook.

## 2026.6.28b1

### Features
- **Multi-tool labeling in HA Assist slow-turn progress (TODO #29).**
  Additive `tool_progress` NDJSON frames (`phase=start|end`, `name`,
  optional `id`, monotonic `seq`) emitted alongside the stream when the
  HACS shim advertises `client_caps: ["tool-progress-frames"]` on the
  request. Each new tool call updates the progress label as it fires
  instead of the label sticking on the first tool of the turn. Legacy
  textual `🔧 Calling X...` deltas are preserved when the capability is
  not advertised (no dual emit when it is). Shim swallows + debug-logs
  the frames for now; ChatLog has no ephemeral hook today, so this
  prepares the path for when HA exposes one. Race fix: an id-less
  `end` cannot clear an active tool that has an id.

### Changes
- **Tier B addon lifecycle no longer requires `OPENCLAW_ADMIN_TOKEN`.**
  Authorization for `ha.addon_start`, `ha.addon_stop`, and
  `ha.addon_restart` is now: the pairing-session bearer
  (`local_api_token`) authenticates the request, and the target slug
  must be present in `addon_lifecycle.allowlist` (with the existing
  `core_*` and denylist guards still applied). There is no fallback
  to the admin-token gate — the env var is simply not consulted for
  Tier B. The `ha.reload_config` admin gate is unchanged.
- **Identity user config now takes HA usernames.** At startup the add-on
  resolves configured names in `identity.super_admins` and
  `identity.user_agent_map` through HA WebSocket `config/auth/list`, then keeps
  the resolved HA user IDs in memory for signed Assist actor policy and
  routing checks. Unknown names log a warning and are ignored for that
  run, failing closed to the lower `admin` / `user` role or default
  agent route.
- **`hass_url` and `hass_token` moved to the end of the options form,
  past the `identity` and `addon_lifecycle` nested blocks.** Mitigates
  a browser password-manager autofill class of bug where a
  `[text][password]` adjacency was getting populated with `(saved
  username, saved password)` for an unrelated site and persisted into
  the add-on options on save. Schema and runtime semantics unchanged;
  only the rendering order in the HA Configuration tab moves.

### Tooling
- **`scripts/bump-version.py` now refuses prerelease counters above 9.**
  For `YYYY.M.D{a|b|rc}n` versions, attempting to bump to `b10+` /
  `a10+` / `rc10+` exits non-zero with a hint to roll the calendar
  portion forward and reset the counter to `1` instead. Rule lives in
  user memory as `feedback_beta_cap_b9`; concrete prior failure was
  `2026.6.20` getting stuck for a week and reaching `b11`, which
  triggered a GitHub `/releases` UI ordering quirk.

### Docs
- **New `addon/DOCS.md` (rendered as the add-on's Documentation tab).**
  Covers every option in `addon/config.yaml` (`gateway_url`,
  `pairing_token`, `node_name`, `local_api_token`, `reset_pairing`, the
  `identity` block, the `addon_lifecycle` block, and `hass_url` /
  `hass_token` at the bottom) with purpose, example, default, and
  security implications, plus the Tier A vs Tier B authorization model.
- **DOCS: identity user configuration now documents HA usernames, not
  raw HA user ID lookup.**
- **DOCS: browser-autofill gotcha for `hass_url` / `hass_token`** —
  explains the [text][password] adjacency hazard, the schema
  mitigation above, and how to recover if autofill still strikes.

## 2026.6.20b11 (2026-06-27) — Tool-progress chunk lands on a new line after preamble text

### Fixes
- **Tool-progress line ("🔧 Calling Bash...") now starts on a new line
  when the model has already emitted preamble text** before the first
  tool call. Previously the chunk concatenated directly to the
  preceding assistant delta in the HA Assist transcript
  (e.g. `"Sure, running a few.🔧 Calling Bash..."`). The relay now
  tracks whether any user-visible chunk has been yielded this turn and
  prepends a leading `\n` to the progress chunk when so. Caught
  end-to-end by Rob on b10 in an Assist turn.

### Known limitation (unchanged this release)
- **Multi-tool turns still only label the first tool.** When the model
  makes several sequential tool calls in one turn, only the first one
  surfaces a "🔧 Calling X..." line. The remaining ones run silently.
  Tracked under `docs/TODO.md` item #2 "Out of scope this round"; a v2
  with proper ephemeral status frames + HACS shim change is the
  documented path.

## 2026.6.20b10 (2026-06-27) — Strip unreachable dict-shape branch + drop deprecated armv7 arch

### Fixes
- **Dropped deprecated `armv7` from the addon `arch:` list.** HA Supervisor
  deprecated 32-bit ARM support; Rob's Supervisor logged
  `App config 'arch' uses deprecated values ['armv7']. Please report this
  to the maintainer of OpenClaw Node`. Now `arch: [amd64, aarch64]`. The
  addon never had a working 32-bit build path, so no installer is affected.

### Internal
- Removed the `isinstance(user_agent_map, dict)` branch + warning in
  `addon/run.sh` that b9 added. The b7/b8 manifests never passed
  Supervisor schema validation, so no operator's `user_agent_map`
  ever became a saved dict-shape value — the branch is unreachable.
  Per the pre-1.0 no-back-compat rule, dropping it instead of carrying
  dead code with a misleading warning.

### Full `addon/config.yaml` schema audit (this release)
Audited the whole manifest against the HA developer docs
(`https://developers.home-assistant.io/docs/apps/configuration/`):

- **`map:`** modernized from the older colon shorthand
  (`- config:rw`) to the docs-primary dict form
  (`- type: config\n  read_only: false`). Same runtime behavior;
  matches the canonical reference.
- All option `schema:` validators (`url`, `password`, `str?`,
  `password?`, `bool?`, `- str`, `- {ha_user_id: str, agent_id: str}`)
  are in the documented validator set: `str`, `bool`, `int`, `float`,
  `email`, `url`, `password`, `port`, `match(REGEX)`,
  `list(val1|val2|...)`, `device`, and nested objects (up to 2 levels).
- `hassio_role: manager` and `panel_icon: mdi:robot-happy` confirmed
  valid against docs.

## 2026.6.20b9 (2026-06-27) — Supervisor manifest re-parse fix (`user_agent_map` schema)

### Fixes
- **Addon would not appear as an Update past b6 since b7 shipped.** Root
  cause: the `identity.user_agent_map` option used schema validator
  `dict?`, which is not in HA Supervisor's allowed addon-schema validator
  set (`str`, `int`, `float`, `bool`, `password`, `email`, `port`, `url`,
  `match(...)`, lists, nested objects). On every git pull Supervisor
  re-parsed the manifest, the new b7/b8 manifest failed schema
  validation silently, and Supervisor kept its cached b6 record (Update
  prompt missing, icon disappeared, `App fcccfbbd_openclaw_hass_node
  does not exist in the store` on Changelog open).
- **Switched `user_agent_map` to the HA-canonical list-of-objects shape:**
  `[{ha_user_id: str, agent_id: str}, ...]`. Supervisor renders a proper
  list editor in the UI; `run.sh` flattens the list to a dict before
  exporting `OPENCLAW_IDENTITY_USER_AGENT_MAP` so the internal
  `dict[str, str]` contract in `config.py` / `IdentityConfig` is
  unchanged. Pre-1.0 alpha: no back-compat fallback for the old dict
  shape.

### Operator note
After updating, existing operators who had populated `user_agent_map`
will need to re-enter their entries under the new list-of-objects
shape in the addon Configuration tab. Empty `user_agent_map` (the
default) requires no action.

## 2026.6.20b8 (2026-06-27) — Identity-routing hardening + docs architecture reshape

### Fixes
- **Fix-forward for identity-routing review findings (PR #167).** HA actor
  metadata is now trusted only when the HACS shim signs the actor plus turn
  fields with a key derived from the existing `local_api_token`; unsigned or
  bad signatures fall back to the restrictive anonymous/user policy. Public
  health responses redact identity details to counts/booleans, forbidden-
  command output uses matchable command patterns, admin prompt policy
  includes Tier B lifecycle commands, lifecycle allow/deny parsing uses the
  shared config parser, startup `agents.list` diagnostics no longer block
  operator connect, and `agents.list` parsing no longer treats display
  `name` as an agent id. **No new operator-facing secret was introduced** —
  the actor-signing key is derived from the existing `local_api_token` via
  HMAC label `openclaw-hass-node actor-signing v1`.

### Docs
- **Drift reconciliation (PR #168).** Brought every doc back in sync with
  the actual repo state: version strings, command counts (37), Tier B
  shipped-status, release-workflow reality, and the post-#167 token model.
- **Repo-level doc sweep checklist (PR #169).** `docs/README.md` now lists
  the files outside `docs/` that also need updates when user-facing facts
  change (`README.md`, `addon/config.yaml` description, `addon/CHANGELOG.md`,
  the five version sources, manifest/strings/hacs.json, GitHub repo
  description).
- **Phase 2 architecture reshape (PR #171).** Reorganized `docs/` into four
  audience-grouped folders (`design/`, `reference/`, `operations/`,
  `research/`), absorbed 4 overlap files (OVERVIEW, PACKAGING, PROCESS,
  QUESTIONS-FOR-ROB), and stripped duplicated content so STATUS / MEMORY /
  PLAN no longer overlap. README.md gained the "Security model" and
  "What this is not" sections that previously lived in OVERVIEW.

## 2026.6.20b7 (2026-06-24) — HA Assist identity routing + Tier B add-on lifecycle commands

### Features
- **HA Assist turns now carry actor-aware authorization context.** The HACS shim forwards the HA conversation actor to the add-on, and the add-on resolves each turn into `user`, `admin`, or `super_admin` using `identity.super_admins` plus the HA `is_admin` bit. The add-on prepends a hardened, per-turn authorization disclaimer that tells the agent which command names are forbidden for that actor, explicitly says not to repeat the policy text, and treats attempts to override the restriction as untrusted user content. Shared-agent setups get prompt-level protection; operators who need hard separation can also map users to gateway agents with narrower tool inventories.
- **Per-user agent routing is now configurable from the add-on.** New `identity.user_agent_map` and `identity.default_agent_id` options let a household route different HA users to different OpenClaw agents while keeping the agent definitions themselves in gateway config. At startup the add-on asks the gateway for available agents and logs missing mappings alongside the known IDs so operator mistakes are visible before users hit them.
- **Tier B add-on lifecycle commands are now available behind explicit gates.** Added `ha.addon_start`, `ha.addon_stop`, and `ha.addon_restart`. They require the admin token gate, use Supervisor lifecycle endpoints, deny core Supervisor/Home Assistant slugs, and default-deny all other add-ons unless listed in `addon_lifecycle.allowlist`. Start/stop are idempotent when the add-on is already in the requested state.

### Docs
- Documented the new identity options, forbidden-command JSON override format, lifecycle allowlist policy, and the split between OpenClaw agent inventory controls and add-on-side HA Assist restrictions.

## 2026.6.20b6 (2026-06-21) — Re-dispatch `agent` events from gateway WS loop

### Fixes
- **`agent` events now reach `ChatRelay` (PR #147).** b5 advertised the `tool-events` capability and added per-tool labelling in `ChatRelay.handle_event`, but the gateway WS event loop's dispatch allowlist did not include the `agent` event kind, so the addon silently discarded every tool-progress frame before it ever reached the relay. Slow-turn deltas continued to show the generic `Working on it...` placeholder instead of the tool name. The dispatch filter now includes `agent` alongside the existing `session.*` / `chat*` paths; non-tool `agent` shapes are safe because `ChatRelay.handle_event` only mutates `_active_tool` when `payload.stream == "tool"` and no-ops otherwise. New regression test exercises `_event_loop`'s event-name filter end-to-end with an `agent` frame fed through the websocket iterator (the b5 tests stubbed `handle_event` directly, which is what missed this).

## 2026.6.20b5 (2026-06-20) — Tool-named slow-turn progress + tool-event stale-trailer filter

### Features
- **Slow-turn progress now names the tool being called (TODO #2).** The operator-role WS handshake now advertises the `tool-events` capability, which opts this connection in to the gateway's per-tool `agent`/`session.tool` broadcasts (`/app/dist/server-chat-DVXWYmKw.js:889,903`; cap check at `/app/dist/agent-DnsoYp5b.js:1684`). `ChatRelay` records the most recent tool name per session from those events and uses it in the visible slow-turn keepalive delta (`f"🔧 Calling {tool_name}...\n\n"`) instead of the generic `Working on it...` placeholder. Falls back to the generic placeholder when no tool has started — e.g. the model is just thinking. The 8s silence threshold still gates visibility so fast turns stay quiet; multi-tool turns currently only label the FIRST visible delta (the one that fires at the 8s mark). New regression tests: `test_stream_turn_uses_tool_name_in_silent_gap_progress`, `test_handle_event_tool_start_and_end_update_active_tool`. The HACS shim is unchanged — the tool-named delta uses the same `{"delta": "..."}` frame format and is appended to the saved assistant reply the same way the prior placeholder was.

### Fixes
- **Tool-event capture honours the runId staleness filter.** GPT-5.5 review on PR #143 caught that the new tool-event branch updated `_active_tool` before the existing active-run/runId stale-event filter; a delayed prior-run tool event could relabel the current turn's persisted HA progress delta. The branch now applies the same staleness rules used for assistant events (pending ack drops, mismatched runId drops, runId-less drops until same-run evidence). New regression test: `test_handle_event_tool_event_filtered_by_run_id`.

## 2026.6.20b4 (2026-06-20) — Tier A Supervisor access fix (hassio_role: manager) + pairing retry-after

### Fixes
- **Tier A addon commands now actually reach Supervisor (#138).** b3 shipped `ha.list_addons`, `ha.addon_info`, `ha.addon_stats`, `ha.addon_changelog`, and `ha.addon_documentation` but Supervisor returned `403: Forbidden` to all of them in production. Root cause: the addon was running with the default Supervisor role, which only grants access to `/info`. The Tier A endpoints under `/addons/<slug>/...` require the `manager` role. `addon/config.yaml` now sets `hassio_api: true` + `hassio_role: manager`. `manager` is narrower than `admin` and covers addon-management endpoints without granting host or core mutation surface. Lifecycle mutations (Tier B start/stop/restart) still require a separate `OPENCLAW_ADMIN_TOKEN` gate.
- **Pairing rate-limit responses now surface `retry_at_utc`.** `PairingError` propagates `retry_after_ms` from the gateway and the WS layer formats an ISO-8601 UTC timestamp so operators can see when the next attempt will be accepted instead of guessing from a bare millisecond count. No behaviour change on success paths.

### Docs
- `docs/PACKAGING.md` and `docs/PLAN.md` updated to describe the new role requirement.
- `docs/TODO.md` added as the canonical operational punch list (PR #139); supersedes the three handoff docs and the workspace mirror.

## 2026.6.20b3 (2026-06-20) — Tier A read-only addon command surface + post-ack streaming race close

### Features
- **`ha.addon_info` command (read-only) — explicitly secrets-safe.** Returns per-addon metadata via `GET http://supervisor/addons/<slug>/info` with a strict allowlist (`slug, name, state, description, version, version_latest, update_available, boot, startup, stage, arch, machine, ingress, ingress_port`). The Supervisor response also contains the addon's current `options` (config VALUES — passwords, API keys, URLs), `schema` (option field NAMES), and `repository` (addon source — a repo URL for community/private addons, which can leak operator-private hostnames). **All three are dropped at the boundary.** Option values are the obvious leak vector, schema field names alone reveal which integrations are configured (e.g. an `mqtt_password` schema field implies MQTT auth is active), and `repository` is dropped entirely rather than allowlisting the well-known `core`/`local` values because that set drifts with Supervisor releases. Similarly dropped: `hostname`, `ip_address`, capability flags (`homeassistant_api`/`hassio_api`/`privileged`/`audio`/`video`/`gpio`/`usb`/etc.), discovery wiring, and raw URLs. Supervisor response body is also capped at 1 MiB before parsing so a misbehaving Supervisor (or a giant `long_description`) cannot drive the node OOM. Rationale lives in a long comment block on `_ADDON_INFO_FIELDS` in `commands/ha.py`. Tests assert each forbidden field is absent — that invariant is the whole point of the command.
- **`ha.addon_stats` command (read-only).** Returns allowlisted runtime metrics from `GET http://supervisor/addons/<slug>/stats`: CPU percent, memory usage/limit/percent, network rx/tx, block read/write. Any future Supervisor field is dropped by default so a release that adds a sensitive field cannot leak through.
- **`ha.addon_changelog` command (read-only).** Markdown body from `GET http://supervisor/addons/<slug>/changelog`, bounded to a 1 MiB trailing window via `supervisor_get_text`.
- **`ha.addon_documentation` command (read-only).** Markdown body from `GET http://supervisor/addons/<slug>/documentation`, bounded to a 1 MiB trailing window.
- **`ha.list_addons` command (read-only).** Companion to `ha.addon_logs` — the slug-required form is useless without a discovery path. Hits `GET http://supervisor/addons` via a new `supervisor_get_json` helper and returns a fixed, non-sensitive subset per add-on: `slug`, `name`, `state`, `version`, `version_latest`, `update_available`. All other Supervisor fields (notably `options`/`boot`/`repository`) are dropped at the boundary so the read-only surface cannot leak per-addon configuration. Same read-only-by-construction guarantee as `ha.addon_logs`. Tests cover happy path, field filtering, malformed-entry tolerance, three `HA_BAD_RESPONSE` shapes, and `SUPERVISOR_UNAVAILABLE`.
- **`ha.addon_logs` command (read-only).** Fetches the most recent Supervisor add-on log lines via `GET http://supervisor/addons/<slug>/logs` and returns them through the node's existing command dispatcher. Step toward sunsetting subagent reliance on the `mcp__homeassistant__*` MCP server and the long-lived `HASS_TOKEN`; the supervisor-token path keeps the call privileged-but-read-only by construction (Supervisor's `/addons/<slug>/logs` is GET-only). Params: `slug` (required, validated as `[a-z0-9_-]{1,128}`) and `lines` (optional, clamped 1–5000, default 200). Returns `{ok, slug, lines, log}` from a bounded 1 MiB trailing byte window before line trimming. New `supervisor_get_text` helper in `ha_client` issues the request without ever combining the supervisor token with a non-supervisor URL. Tests cover slug validation, lines clamping, tail-trim correctness, bounded byte retention, and the four error paths (`SUPERVISOR_UNAVAILABLE`, `HA_AUTH`, `HA_NOT_FOUND`, `HA_HTTP_ERROR`).

### Fixes
- **#128 — Assist follow-up turns now stream deltas correctly.** After 2026.6.19b1 shipped streaming, turn 1 of an HA Assist conversation streamed token-by-token but turn 2 (a follow-up like "that was perfect") dumped the full reply as one chunk and sometimes appeared to echo prior-turn text. Root cause: `ChatRelay._stream_turn_locked` installed the per-turn delta queue and cleared the prior `_active_run_id` BEFORE awaiting `chat.send`. During that await window, late trailer events from the prior turn (with the prior `runId`, or no `runId`) passed the in-progress-turn guard, landed in turn N+1's queue, and triggered the terminal-without-deltas single-chunk fallback. The fix: send `chat.send` first, capture the new `runId` from the ack, then install per-turn state atomically (no `await` between). The receive-side `runId` filter is also tightened so empty-`runId` chat-family events are treated as stale when an active run is tracked, and a `chat` terminal arriving without preceding deltas now logs a `[relay-diag]` warning so the degradation is visible. Mirror fix applied to the non-streaming `relay_turn` path. New regression tests: `test_turn_boundary_stale_event_does_not_leak_into_next_turn` and `test_stream_turn_two_turns_both_stream_deltas`.
- **#129 follow-up — close the post-ack / pre-terminal race for streaming Assist turns.** After the #128 fix above, the post-ack window remained open: after turn N's `chat.send` ack installed a real `runId` but before turn N's first same-run event arrived, a delayed prior-turn runId-less `session.message` could still set `_terminal_assistant_text` and wake the waiter, returning the prior turn's reply for the new turn. `ChatRelay.handle_event` now tracks a per-session `_seen_same_run_event` flag (reset at turn start, set when an event tagged with the active `runId` is observed) and drops runId-less `session.message` events on streaming turns until that evidence exists. Non-streaming `relay_turn` consumers (deferred-reply flow with a single runId-less `session.message` terminal) are unaffected. New regression test: `test_stream_turn_post_ack_runid_less_session_message_is_filtered`.

## 2026.6.19b2 (2026-06-20) — Slow-turn progress for HA Assist streaming

### Fixes
- **Slow-turn progress keeps HA Assist's stream alive.** HA Assist enforces a ~30s read timeout on the conversation stream. On tool-heavy turns the gateway can stay silent for tens of seconds between `chat.send` ack and the first assistant delta while it runs tool calls (calendar lookups, file reads, web searches). HA closed the stream during that gap and the user saw "node not responding" even though the real reply landed later — short conversational turns worked fine, "what is my schedule" / "how do the tanks look" did not. `ChatRelay.stream_turn` now emits one visible `Working on it...` progress delta after `_STREAM_FIRST_KEEPALIVE_S` (8s) of silence, then transport-only keepalive frames every `_STREAM_KEEPALIVE_INTERVAL_S` (15s) thereafter. The HACS shim now uses a read-gap timeout instead of a fixed 35s total request timeout, and streaming relay turns can run up to 180s. Real assistant deltas reset the timer so fast turns never see progress noise. New tests: `test_stream_turn_emits_keepalive_during_silent_gap`, `test_stream_turn_no_keepalive_on_fast_turn`, and `test_assist_turn_stream_emits_keepalive_frames`.

### Known issues / follow-ups
- **#128 / #129 follow-up turn-boundary race** — closed by the #128 and #129 fixes shipping in the next release (see Unreleased above).

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
