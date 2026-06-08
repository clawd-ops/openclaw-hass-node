# Status

> **Update this file at every meaningful state change.** It is the
> single thing that tells future-Clawd "where am I". If `PLAN.md` and
> `STATUS.md` disagree, fix whichever is wrong before continuing.

## Where we are (2026-06-08)

**EYES AND HANDS WORK.** `openclaw nodes invoke --node hass --command ping`
round-trips cleanly. Addon installed, paired, connected, all 28 commands
surface in `openclaw nodes describe`, `node.invoke.request` →
`node.invoke.result` works end-to-end. Currently on **2026.6.8a1** (alpha).
The exact version string is enforced by `test_version_sync.py` across
`pyproject.toml`, `addon/config.yaml`, `addon/build.yaml`,
`manifest.json`, and the source-literal fallback in `__init__.py`.

**Install-ready surface:**

- Node command surface complete: `fs.*` (11), `system.*` (2), `ha.*`
  (13), `ping`. All 28 surface on the gateway after pair-approval (via
  `openclaw nodes approve`, NOT `openclaw devices approve` — both
  queues need to be approved, see LESSONS).
- HACS shim wired: `custom_components/openclaw_gateway/` exposes a
  `ConversationEntity` that POSTs to the node's `/v1/conversation`.
- Gateway WS client: Ed25519 handshake, pairing, reconnect loop,
  `node.invoke` dispatcher, device_token persistence with NOT_PAIRED /
  PAIRING_REQUIRED / AUTH_TOKEN_MISMATCH self-heal. All frames shaped
  per the canonical SDK schema at
  `/app/node_modules/openclaw/dist/plugin-sdk/packages/gateway-protocol/src/schema/protocol-schemas.d.ts`
  (canonical-only — no legacy-shape fallbacks per alpha policy).
- Local HTTP API gated by bearer-token middleware when
  `local_api_token` is set; host port mapping removed from the add-on
  config so the API is only reachable inside the Supervisor add-on
  network by default.
- Secret files (`node-key.json`, `device-token`) written at mode
  `0o600` with `O_NOFOLLOW` so a planted symlink can't redirect the
  write. Path-validated unlink before token reset.
- Tests pass with branch coverage gated at 95%, all CI gates green.

**Two holes still open:**

1. **`assist_turn` (the `/v1/conversation` handler) is a placeholder.**
   Reports pairing/connection state but doesn't relay turns. P5.12.
2. **HA-side credentials are not wired.** `ha.list_areas` returns
   `HA_NOT_CONFIGURED: HASS_URL is not set`. Root cause: Rob's
   Supervisor isn't injecting `SUPERVISOR_TOKEN` despite
   `hassio_api: true + homeassistant_api: true + auth_api: true`.
   Workarounds: (a) figure out why Supervisor isn't injecting, (b) add
   `hass_url` + `hass_token` add-on (app) options users can fill in.
   Tracked as next step P-INSTALL.

**Install push 2026-06-06/07/08 — 17 PRs (#29–#45) shipped:**

| PR | Fix |
| --- | --- |
| #29 | drop unpublished GHCR image: from config.yaml |
| #30 | move `node/` into `addon/node/` so Supervisor's build context resolves COPY paths |
| #31 | use HA python-on-Alpine base (`ghcr.io/home-assistant/{arch}-base-python:3.13-alpine3.20`) |
| #32 | `client.id = "node-host"` (was rejected enum value) |
| #33 | include `deviceFamily` in connect.client (for v3 signature reconstruct) |
| #34 | thread `pairing_token` into GatewayClient construction |
| #35 | persist gateway-issued `device_token`, prefer over pairing_token |
| #36 | `displayName` from `node_name` config |
| #37 | restructure docs: user-facing README, INSTALL.md, LESSONS.md |
| #38 | self-heal on NOT_PAIRED / PAIRING_REQUIRED |
| #39 | transparent icons + shim entry title from socket host |
| #40 | `/data`-writable fallback for addon-mode detection + bump `__version__` |
| #41 | self-heal also on AUTH_TOKEN_MISMATCH / token_mismatch |
| #42 | docs: two-pair-queue + reloadKind=restart lessons |
| #43 | `node.invoke.result` uses `id` not `invokeId` |
| #44 | canonical `node.invoke.result` shape `{id, nodeId, ok, payload, error}` + drop `limit` from `node.pending.pull` |
| #45 | error as `{code, message}` object, ack as `{ids: [...]}`, point at SDK schema as canonical source |

Every gotcha lives in [`docs/LESSONS.md`](LESSONS.md). User-facing
install walkthrough in [`docs/INSTALL.md`](INSTALL.md) including the
**required** `gateway.nodes.allowCommands` patch on the OpenClaw side
and the dual-queue approval flow.

**Architecture for P5.12 (decided 2026-06-06):** the node calls
`chat.send` over its existing gateway WS connection to inject the
Assist turn into an agent session, and subscribes via
`sessions.messages.subscribe` to receive the reply. The session is
keyed by HA's `conversation_id` for multi-turn threading. The agent
(Clawd) uses `ha.*` tools via the existing `node.invoke` path — no
changes there. Full design + post-mortem of the wrong-direction
approach in `docs/RESEARCH-OPENCLAW-INTEGRATION.md`.

**Next concrete steps (in order):**

1. **P-INSTALL — wire HA credentials.** `ha.*` invokes round-trip
   protocol-wise but error in-payload with `HA_NOT_CONFIGURED: HASS_URL
   is not set`. Either (a) diagnose why Rob's HA Supervisor isn't
   injecting `SUPERVISOR_TOKEN` despite the addon's API flags, OR (b)
   add `hass_url` + `hass_token` as add-on (app) options surfaced through
   `run.sh` env. (b) is the more portable fix and is probably what
   ships. Small change, ~30 LOC + INSTALL doc update.
2. **P5.12 — ChatRelay** (~100 LOC node Python). Add `operator.read`
   to connect-frame scopes; build `ChatRelay` that owns one
   `sessions.messages.subscribe` per active conversation_id, calls
   `chat.send` for each turn, awaits reply with a 30s timeout.
   Rewrite `assist_turn` to use it. Open design questions: fresh
   session per HA conversation_id vs one persistent session per HA
   instance; fixed agent vs configurable in the shim's config flow.
   Best done with Rob present.
3. **Polish / hardening** — visible misses worth fixing before any
   real users land:
   - `node.pending.pull` warning on every connect is cosmetic noise
     (gateway returns `ok: false` with null error); silence or drop.
   - HACS brands PR so the integration list shows the OC icon.
   - The `_PENDING_PULL_LIMIT` constant is dead after #44; remove.
4. **P6.2 — MCP cutover.** Cron `scripts/check-mcp-retirement-readiness.sh`
   against OpenClaw logs. When it prints `RETIREMENT_READY`, drop the
   `homeassistant` + `homeassistant-readonly` MCP server entries from
   gateway config in one PR.
5. **P7 — publish.** Add-on (App) repo metadata, HACS index entry, release
   workflow for GHCR-published per-arch images (lets us put the
   `image:` key back in config.yaml and skip the on-device build).

**What was wrong, kept here so future-me doesn't repeat it:**

P5.2–P5.10 built a parallel Python WS gateway (`gateway/` workspace)
with its own `Brain`, Anthropic + OpenAI providers, a fake
`node.conversation.request` event type, an `InvokeDispatcher`, and an
Ed25519 server-side handshake. **All of it was wrong** — Clawd is
OpenClaw, the brain *is* me, and the existing gateway's `chat.*`
surface already does this work. P5.11 deleted all of it
(-4001 / +209 lines). See `docs/RESEARCH-OPENCLAW-INTEGRATION.md`
for the post-mortem. Upstream OpenClaw doc gap that misled me is
recorded in
`workspace/runtime-audits/2026-06-06-openclaw-node-conversation-relay-doc-gap.md`.

## Discoverability / sponsorship — pre-beta TODOs

- **Funding links.** `.github/FUNDING.yml` and README both live.
  BMC (`buymeacoffee.com/roblandry`) is active now. GitHub Sponsors
  for `roblandry` is staged in FUNDING.yml but waiting on GH staff
  approval (profile shown as Pending 2026-06-08). Cross-account is
  fine: FUNDING.yml names who gets sponsored, not who owns the
  repo, so a `clawd-ops/*` repo can fund a personal account
  directly. The Sponsor button on the repo page will start working
  the moment GitHub approves the profile.
- **Stars badge.** Added (social-style shields.io badge pointing at
  `/stargazers`). Surfaces popularity as the project takes off.
- **Other badges to consider once published:** HACS default badge
  (after HACS index PR lands), CI status, release version, license.

## Doc debt — end-to-end user documentation

Per Rob (2026-06-08): before P7 ships, the repo needs comprehensive
docs so a fresh user can understand the project without reading code
or asking. Build incrementally as we work, not in one pass. Required
coverage:

- **What it is + why** — one-paragraph elevator pitch, then a
  "what this gives you" feature list (28 commands, 13 ha tools, Assist
  conversation surface, etc).
- **Architecture diagram** — HA ↔ addon (node-host) ↔ gateway ↔ Clawd,
  including the dual pair queues (`devices` for auth, `nodes` for
  commands) and the device-token persistence/self-heal loop.
- **Security model** — what the addon can do (Supervisor admin,
  filesystem maps, shell), how the WSS handshake is authenticated
  (Ed25519 v3 signed payload), where the device token lives, what an
  attacker on the LAN can and cannot do, what `allowCommands` gates.
- **Install + pair** — already in `docs/INSTALL.md`; keep it
  user-friendly.
- **Operating** — log format (see invoke ▶/◀ lines), version-bump
  flow (HA Update vs Uninstall/Reinstall and what each preserves),
  how to rotate the pairing token, how to revoke a node.
- **Troubleshooting** — extend the existing table as new failure
  modes show up; cross-link to `docs/LESSONS.md` for the postmortem
  detail.
- **Command + tool reference** — full list of the 28 commands and
  13 ha.* tools, each with arg shape and example invoke.
- **Contributing / version-bump rules** — already in
  `docs/CONTRIBUTING.md`; verify it stays current.

Tracking marker: when each section lands, link it from `README.md`
so the user-facing entry point is a complete table of contents, not
a stub.

## Current phase

**Install-stabilisation push complete (2026.6.7).** Node command surface is round-trippable end-to-end through the gateway (`openclaw nodes invoke …` returns real results). Next code work is **P-INSTALL** (HA credentials) then **P5.12** (ChatRelay). P6.2 cutover waits on the validation-harness streak.

P2 merged on 2026-06-06 (`2c83bfd`, PR #2) via human override.
P3.1 merged on 2026-06-06 (`3542bdd`, PR #3) after Codex cross-review
returned APPROVE-WITH-NITS on re-review #3 via the CLI workaround (see
`docs/PROCESS.md` "Codex CLI fallback"). v1 was BLOCK (10 findings),
v2 REQUEST-CHANGES (NUL-bypass HIGH), v3 APPROVE-WITH-NITS.
P3.2.1 merged on 2026-06-06 (`13687a5`, PR #4) after Codex cross-review:
v1 REQUEST-CHANGES (7 findings), v2 APPROVE (all 8 items resolved). Fixes:
parent-dir fsync for crash-durability, datetime-based `at=` comparison,
ValueError/TypeError catch in `from_json`, cap raised 200→250, docs for
concurrency model, orphan behavior, and case-sensitive FS assumption.
P3.2.2 merged on 2026-06-06 (`bd9ab2a`, PR #5) after Codex cross-review:
v1 REQUEST-CHANGES (2 HIGH + 1 MEDIUM), v2 APPROVE. Fixes: protected-root
gate is now unconditional (agent_bridge=False cannot bypass), post-resolution
symlink/traversal check added, _coerce() treats 64-char hex as sha256 before
int() coercion. 224 tests, 97.29% branch coverage.
P3.2.5 merged on 2026-06-06 (`9c4371f`, PR #8) after Codex cross-review:
v1 APPROVE (micro-prompt: no shell=True, compare_digest for token, MAX_OUTPUT
bounds output, fail-closed admin gate). Pre-emptively fixed timing attack
(hmac.compare_digest) before review. 322 tests total, 40 in test_system_run.py.
P3.2.4 merged on 2026-06-06 (`d2c0eb3`, PR #7) after Codex cross-review:
v1 APPROVE-WITH-NITS (PATCH_FAILED leaked raw stderr to wire; add subprocess
shape test). Fixed: _LOG.error + generic wire message; test_run_patch_subprocess_
command_shape verifies --output, input=encode, no shell. v2 APPROVE. 283 tests,
100% branch coverage on fs_patch.py.
P3.2.3 merged on 2026-06-06 (`2309510`, PR #6) after Codex cross-review:
v1 REQUEST-CHANGES (4 findings: shutil.move non-atomic, EXDEV not mapped,
send2trash error-propagation gap, post-resolution patch target wrong),
v2 APPROVE (micro-targeted prompt to avoid OOM). Fixes: _move_file() helper
using os.replace only (no copy-then-unlink fallback), errno.EXDEV → CROSS_DEVICE,
send2trash non-ImportError propagation test, post-resolution tests patch
fs_write.resolve_safe. 262 tests, 100% branch coverage on fs_move_delete.py.

## Last completed

- 2026-06-05 — Project bootstrapped at `~/.openclaw/projects/openclaw-hass-node/`.
- 2026-06-05 — `PLAN.md`, `STATUS.md`, `COMMAND-SURFACE.md`, `PACKAGING.md` drafted.
- 2026-06-05 — Repo pushed: https://github.com/clawd-ops/openclaw-hass-node
- 2026-06-05 — Issue #1 first round folded in.
- 2026-06-05 — New docs: `HA-CONFIG-EDITING.md`, `PROCESS.md`.
- 2026-06-05 — Issue #1 second round (Rob): backup model rewritten,
  HA-native edits hardened, breaking-change verification made
  mandatory. Resulting changes:
  - `PLAN.md` §1b rewritten: per-file content-addressed backup store
    in `/share/openclaw-backups/`. No git, no per-change Supervisor
    snapshots, no `.bak` sidecars.
  - `PLAN.md` §2b hardened: HA-native APIs are the default; `fs.patch`
    is the exception (yaml-only / custom things / blueprints).
    `.storage/` is read-only at the command dispatcher; writes require
    explicit `unsafe_storage=true` plus user-accepted proposal.
  - `PLAN.md` §2c expanded: `docs.breaking_changes` command,
    mandatory pre-change verification, cross-validated by Codex.
  - `HA-CONFIG-EDITING.md` rewritten around the HA-native-first rule
    and the per-domain API map.
  - New `BACKUPS.md` covers the per-file store format, retention,
    restore flow, and DR.
- 2026-06-06 — P3.1 read-only fs/system PR opened:
  clawd-ops/openclaw-hass-node#3.
- 2026-06-06 — P3.1 MERGED (`3542bdd`). Codex cross-review iterated v1→v3
  via the CLI workaround; landed APPROVE-WITH-NITS. 135 tests, 96.26%
  branch coverage. One non-blocking nit: trailing-slash on regular file
  opens it (not an access bypass; tighten when convenient).

P5.8 merged on 2026-06-06 (`948cb57`, PR #22) after Codex cross-review:
v1 APPROVE. Gateway is now deployable: `python -m openclaw_gateway` boots
via config.load_config (env-driven), persistent DeviceRegistry, serve_forever.
DeviceRegistry gains optional persist_path that loads on construction and
writes atomically via tempfile.replace after every register/approve/revoke.
Malformed entries skipped with log. 505 tests, 96.92% coverage. README
rewritten for current state.

P5.7 merged on 2026-06-06 (`db95a95`, PR #21) after Codex cross-review:
v1 APPROVE. GatewayServer handshake now verifies the Ed25519 signature over
the v3 payload (constants kept in sync with node identity.py). New auth.py
codes: AUTH_MISSING_FIELD, AUTH_NONCE_MISMATCH, AUTH_TIME_SKEW (5min),
AUTH_BAD_SIGNED_AT, AUTH_BAD_SCOPES, AUTH_BAD_ENCODING, AUTH_BAD_SIGNATURE,
AUTH_BAD_PUBLIC_KEY, AUTH_KEY_CHANGED. DeviceRegistry holds PENDING/PAIRED
state, issues 32-byte urlsafe tokens via secrets.token_urlsafe, idempotent
on re-approval so reconnects keep the same token. auto_approve flag drives
trial-mode (pair on first connect) vs production (PAIRING_REQUIRED until
operator calls approve_device). 494 tests, 96.86% coverage.

P5.6 merged on 2026-06-06 (`8548d5c`, PR #20) after Codex cross-review:
v1 APPROVE. GatewayServer (trial mode, no Ed25519 verify yet) + InvokeDispatcher
(gateway mirror of ConversationDispatcher). E2E flow: HA Assist → shim →
node /v1/conversation → ConversationDispatcher → node.conversation.request →
GatewayServer → Brain → tool calls → node.invoke.request → node ha.* handler
→ node.invoke.result → tool_result → brain text → node.conversation.result
→ speech. Brain stops re-wrapping its own BrainError. 470 tests, 96.94%
branch coverage on both packages.

P5.5 merged on 2026-06-06 (`72c3a9b`, PR #19) after Codex cross-review:
v1 APPROVE. New `gateway/` uv workspace member with Claude-backed Brain
(claude-opus-4-7 default, configurable; injectable invoke callback; tool-use
loop bounded at 12 rounds; codes MODEL_CALL_FAILED, PROTOCOL_ERROR,
TOOL_LOOP_OVERRUN) and Anthropic-shaped tool catalog for the 13 ha.*
commands (parity asserted against the node registry). CI updated to lint/
typecheck/test/bandit the gateway alongside the node. 454 tests, 97.66%
coverage. Per Rob: code lives in this repo until/unless we split it out.

P5.4 merged on 2026-06-06 (`d797430`, PR #17) after Codex cross-review:
v1 APPROVE. Wires ConversationDispatcher into GatewayClient: takes optional
runtime=NodeRuntime, on connect creates dispatcher (sends node.conversation.request
frames over the WS) and installs forward as runtime.conversation_forwarder
+ flips gateway_connected=True; _event_loop routes node.conversation.result
events to handle_result; on disconnect/exit cancel_all() fails in-flight callers
with DISCONNECTED. 442 tests, 97% coverage. NodeRuntime TYPE_CHECKING-only
import avoids cycle with http_api.

P5.3 merged on 2026-06-06 (`26ceb46`, PR #16) after Codex cross-review:
v1 APPROVE. New module `conversation_dispatcher.py` with ConversationDispatcher
that correlates conversation request/result frames via asyncio Futures. forward()
generates UUID, sends via injected callback, awaits matching future with timeout;
handle_result completes by id; cancel_all rejects all pending on disconnect.
Codes: TIMEOUT, SEND_FAILED, DISCONNECTED. 8 tests in test_conversation_dispatcher.py.

P5.2 merged on 2026-06-06 (`f4dc8a9`, PR #15) after Codex cross-review:
v1 APPROVE. NodeRuntime gains conversation_forwarder hook and gateway_connected
flag; assist_turn routes through forwarder with 30s asyncio.timeout, degrades
exceptions to stable speech (logs detail). New GET /v1/conversation/info returns
version + pairing + forwarder_registered for shim diagnostics. 5 new tests,
http_api.py back to 100% coverage.

P5.1 merged on 2026-06-06 (`3edef73`, PR #14) after Codex cross-review:
v1 APPROVE. Tightened Assist shim error handling in custom_components/
openclaw_gateway/conversation.py: specific exception types (TimeoutError,
aiohttp.ClientError, ValueError/TypeError) replace bare `except Exception`,
no raw exc detail in user-facing speech, aiohttp.ClientTimeout instead of int.

P4.5 merged on 2026-06-06 (`4a72fef`, PR #13) after Codex cross-review:
v1 APPROVE. Delivered: ha.list_automations (filter on automation. prefix,
optional include_traces fetches WS trace/list per automation with graceful
degradation on failure), ha.check_config (POST /api/config/core/check_config,
pre-reload yaml validation). 422 tests, 97% coverage. ha.* read surface
from COMMAND-SURFACE.md fully covered.

P4.4 merged on 2026-06-06 (`d1a4faf`, PR #12) after Codex cross-review:
v1 APPROVE. Delivered: ha.light_turn_on + ha.light_turn_off with shared
_build_light_target() validator (entity/area/device, entity_id accepts str or
list). 410 tests, 97% coverage. All 9 mcp__homeassistant__* tools now ported.

P4.3 merged on 2026-06-06 (`dadcb21`, PR #11) after Codex cross-review:
v1 APPROVE (3-grep: compare_digest, no subprocess, HA error codes present).
Delivered: ha.logbook (REST), ha.history (REST, flag params), ha.reload_config
(POST, hmac.compare_digest admin gate, fail-closed). 397 tests, 97% coverage.

P4.2 merged on 2026-06-06 (`a4e4811`, PR #10) after Codex cross-review:
v1 APPROVE (lean 3-grep prompt). Delivered: ha_ws_call() WS helper (auth
handshake, url scheme conversion, HAClientError mapping), ha.list_areas /
ha.list_devices / ha.list_entity_registry (WS), ha.list_services (REST).
378 tests, 97% coverage.

P4.1 merged on 2026-06-06 (`60d2d4f`, PR #9) after Codex cross-review:
v1 OOM (exit 137 on full file read), v2 REJECT (grep targeted ha.py only for
codes defined in ha_client.py — false negative), v3 APPROVE (corrected grep
across both files). Delivered: ha_client.py (aiohttp REST wrapper, env-driven,
HAClientError), ha.py (list_states/get_state/call_service), async-aware
dispatcher (dispatch_async + AsyncHandlerError), 358 tests, 97.67% coverage.

## Architectural note (2026-06-06, post-correction)

The brain *is* Clawd in OpenClaw. The HA node is a **standard OpenClaw
node** (Gateway Protocol, `role: "node"`) and relays HA Assist turns
into an agent session using the **existing chat surface** —
`chat.send` + `sessions.messages.subscribe`. There is no parallel
gateway, no invented `node.conversation.*` event types, no TypeScript
plugin work needed.

P5.11 (this PR) deletes the wrong-direction code: the `gateway/`
workspace, the `ConversationDispatcher` invention, the
`conversation_forwarder` hook, and all the runtime-hook wiring on the
gateway WS client. What stays: node command surface (ha.*, fs.*,
system.*), HACS shim, Ed25519 handshake, pairing flow, /v1/conversation
endpoint shape — all were correct.

Real implementation of the chat-surface relay is documented in
`docs/RESEARCH-OPENCLAW-INTEGRATION.md` and queued as P5.12.

## Current task

**P5.12 — Chat-surface relay** (next). Implement what
`docs/RESEARCH-OPENCLAW-INTEGRATION.md` describes: ChatRelay class on
the node that calls `chat.send` and subscribes via
`sessions.messages.subscribe` over the existing gateway WS; rewrite
`assist_turn` to use it. Best done with you available.

~~P5.10 — OpenClaw plugin pair~~ — superseded. Original design at
`docs/RESEARCH-OPENCLAW-INTEGRATION.md`. Two TypeScript plugins:

1. **`ha-assist` Channel plugin** — WS listener that nodes connect to,
   runs the Ed25519 handshake (ports `gateway/.../auth.py`), persists
   the device registry in OpenClaw's state store, turns
   `node.conversation.request` into inbound channel messages keyed by
   `conversationId`, emits `node.conversation.result` on reply.
2. **`ha-tools` Tool plugin** — registers the 13 `ha.*` commands as
   agent tools. Each tool sends `node.invoke.request` on the active
   session's WS, awaits `node.invoke.result`, returns the wire result
   to the agent.

The brain abstraction (`gateway/brain.py`, `providers_*.py`) does NOT
port — OpenClaw already routes models. Reusable from this repo: the
auth payload format, device registry state machine, future-correlation
pattern, and tool catalog shapes.

After P5.10:
- P6.1 validation harness already merged (PR #23, portable in #24).
  Cron it; once ever prints `RETIREMENT_READY` do P6.2.
- P7 — add-on (app) publishing checklist + CI release pipeline.
- `ha.config.*` proposal-gated write surface from COMMAND-SURFACE.md.

## Codex review status

PR #3 cross-review returned BLOCK with 10 findings. Fix mapping:

- BLOCKER `system.which` executed caller-resolved binaries: fixed by
  `4bd79f3` (`system.which` is lookup-only, basename-only, no version
  probe).
- HIGH safe path TOCTOU in downstream fs ops: fixed by `576226e` and
  `05f76a2` (fd-rooted `safe_fd.open_safe_fd`, fd-based read/stat/list/glob).
- HIGH `fs.read` size race: fixed by `576226e` (bounded `os.read` of
  `max_bytes + 1` from the opened fd).
- MED `fs.list` unbounded sort: fixed by `05f76a2` (streaming
  `scandir` with bounded collection before sort).
- MED `fs.glob` unbounded traversal and bad pattern handling: fixed by
  `05f76a2` (`BAD_PATTERN`, fd-rooted bounded walker, hidden filter during walk).
- MED gateway connect advertised wrong commands: fixed by `add3150`
  (advertises exactly `ping`, `fs.*`, `system.which`).
- MED gateway generic command error leaked exception text: fixed by
  `add3150` (generic wire error, full exception only in logs).
- LOW `OUT_OF_BOUNDS` leaked resolved paths: fixed by `add3150`
  (generic exception string and fs wire messages).
- LOW bind mount policy ambiguity: fixed by docs commit for this status
  update (operator-configured bind mounts under allowed roots are trusted).
- LOW test gaps: fixed across `4bd79f3`, `576226e`, `05f76a2`, and
  `add3150`.

## Last P2 completed milestones

- P2.1 — Repo scaffolding: `pyproject.toml` (uv workspace),
  `addon/Dockerfile` + `config.yaml`, `custom_components/openclaw_gateway/`
  stub, GitHub Actions workflow.
- P2.2 — Node entrypoint that detects add-on (app) vs standalone mode and
  opens the gateway WS connection.
- P2.3 — Pairing handshake against the gateway, Ed25519 device identity,
  key persistence under `/data/openclaw/node-key.json`.
- P2.4 — `ping` command end-to-end, command dispatcher, gateway
  invoke/result loop.

## P2 additional scope delivered

- `http_api.py` — local aiohttp HTTP server (port 8099) with `/health`,
  `/commands/ping`, `/v1/commands/{cmd}`, `/ha/snapshot` (read-only HA
  REST proxy), and `/v1/conversation` (Assist placeholder).
- 57 tests, 99.76% branch coverage.

## Next step

Begin P3.2 — proposal-gated writes (`fs.write`, `fs.patch`, `fs.append`)
backed by per-file content-addressed backup store, plus `system.run`
behind the `operator.admin` scope. Cross-review continues to run via the
Codex CLI fallback until OpenClaw's openai/* routing regression is
resolved (see [memory: project_codex_oauth_regression_2026_06_06]).

## Completed P1 research

- **P1.1 (2026-06-05) — Conversation agent registration.** Verdict:
  **Plan A not viable, Plan B required.** HA's conversation registration
  (`async_set_agent` / `ConversationEntity`) is in-process Python only;
  there is no WS/REST/Supervisor path that lets an external process
  register an agent. All precedent ships as `custom_components/`.
  Decision: ship a thin ~150 LOC `custom_components/openclaw_gateway/`
  HACS shim alongside the add-on (app), whose sole job is to register a
  `ConversationEntity` that forwards turns to the add-on (app)'s local
  socket. Full citations in `docs/RESEARCH-CONVERSATION-AGENT.md`.
- **P1.2 (2026-06-05) — agent-bridge connectivity.** Verdict:
  **Gateway brokers.** Node speaks only the gateway WS protocol; emits
  `node.propose` over its existing WS connection and the gateway
  translates to agent-bridge MCP calls. Keeps the node dumb, single
  auth path, one audit trail. See
  `docs/RESEARCH-AGENT-BRIDGE-CONNECTIVITY.md`.

## Open blockers

None.

## Decision log

- 2026-06-05 — Single node per HA. (Rob)
- 2026-06-05 — All `/config` mutations go through agent-bridge. (Rob)
- 2026-06-05 — Add-on (App) first. HACS only as last resort. (Rob)
- 2026-06-05 — Code lives under `~/.openclaw/projects/openclaw-hass-node/`. (Rob)
- 2026-06-05 — Docs in `docs/` are source of truth across compactions. (Rob)
- 2026-06-05 — Deletes use `trash-cli`, recoverable via `fs.restore`. (Rob, issue #1)
- 2026-06-05 — Node must be HA-version-aware via `docs.lookup` against installed version. (Rob, issue #1)
- 2026-06-05 — Build process: Claude generates, OpenAI (Codex) reviews; cross-provider required. (Rob, issue #1)
- 2026-06-05 — Backups: purpose-built per-file content-addressed
  store under `/share/openclaw-backups/`. No git in `/config`. No
  per-change Supervisor snapshots. (Rob, issue #1 round 2)
- 2026-06-05 — HA-native APIs are the default for HA-managed config;
  `fs.patch` is reserved for yaml-only / custom files / blueprints.
  (Rob, issue #1 round 2)
- 2026-06-05 — `.storage/` is read-only to the node. Writes refused
  at the dispatcher unless `unsafe_storage=true` + accepted proposal.
  HARD rule. (Rob, issue #1 round 2)
- 2026-06-05 — Every HA config proposal must verify against the
  running version's breaking changes and include a functional fix
  when impacted. Cross-validated by Codex reviewer. (Rob, issue #1
  round 2)
- 2026-06-05 — Assist conversation agent: ship as add-on (app) **plus**
  thin `custom_components/openclaw_gateway/` HACS shim. Plan A
  (add-on (app) alone) confirmed not viable; see
  `RESEARCH-CONVERSATION-AGENT.md`. (Clawd, P1.1)
- 2026-06-05 — Proposals are gateway-brokered. Node speaks only the
  gateway WS protocol; does not connect to agent-bridge directly. See
  `RESEARCH-AGENT-BRIDGE-CONNECTIVITY.md`. (Clawd, P1.2)
- 2026-06-05 — Language: Python 3.13+ for node and shim. Quality
  gates: `mypy --strict` + `pyright --strict`, Google-style docstrings
  (`ruff` D-rules + `pydoclint`), 100 % branch coverage via pytest,
  `ruff` lint/format, `bandit`, `pip-audit`. All gated in GitHub
  Actions. See `QUALITY.md`. (Rob, issue #1 round 3)
- 2026-06-05 — MCP retirement: node must demonstrably handle every
  call surface the existing MCP servers serve, across every agent
  that uses them, before retirement. Trigger: zero unhandled
  `mcp__homeassistant*` calls for 7 days *and* a written migration
  inventory. No calendar-based default. Cutover is one PR.
  (Rob, P1.3)
- 2026-06-05 — Versioning: date-based `YYYY.M.PATCH` matching the HA
  release the node is tested against (e.g. `2026.6.0`). Patch
  increments for fixes within a HA release. (Clawd recommendation,
  Rob "ok either way", P1.4)
