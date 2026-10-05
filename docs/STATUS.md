# Status

> **Beta.** The latest published beta is
> [`2026.9.13b1`](https://github.com/clawd-ops/openclaw-hass-node/releases/tag/v2026.9.13b1).
> Pairing, connection, selected tool invokes, and HA Assist conversation have
> end-to-end evidence on the beta track, but the full command surface is not yet
> proven. Pre-1.0 breaking changes are still possible.

> **Installed-state notice:** release publication is not deployment evidence.
> The last observed live installation remains `2026.7.23b1` until an operator
> performs the Tier B add-on install and records fresh UAT evidence. Until Phase
> 6 reconciliation is complete, use the
> [completion roadmap](COMPLETION-ROADMAP.md),
> [dated verification](VERIFICATION-2026-09-11.md), and generated
> [command coverage ledger](reference/COMMAND-COVERAGE.md) for current claims.

> **Update this file at every meaningful state change.** It is the
> single thing that tells a future maintainer "where am I". If
> `docs/design/PLAN.md` and `STATUS.md` disagree, fix whichever is wrong before
> continuing.

## Phase 0 containment (released, not yet live-verified)

All 19 mutating actions across the nine `ha.config.*` commands deny
unverified proposal identifiers before any HA request, and at source run only
with a native approval marker. The shared boundary has no caller-controlled
override. Read-only config actions and light control are
unchanged. API-adapter tests explicitly stub the boundary; the independent boundary suite uses real authorization code
and asserts zero HA requests through both handlers and dispatcher.

This is containment only, not a working approval flow. It shipped in
`2026.9.13b1`, but it is not deployed or production-proven in the last observed
live environment. See the [completion roadmap](COMPLETION-ROADMAP.md) for the
remaining work.

The generic `ha.call_service` path also has bounded Phase 0 containment in
`2026.9.13b1` (#287): lifecycle, update, reload, host, shell, and shutdown
effects return `SERVICE_DENIED` before HA I/O, while ordinary operations such
as `light.turn_on` remain available. Input names and aliases are normalized and
validated before the policy decision. This is not the final approval-aware
effect policy and has not been deployed or production-proven. The effect-based
policy that replaces it on `main` is described under
[Merged on main, not yet released](#merged-on-main-not-yet-released).

## Merged on main, not yet released

The changes below are merged on `main` after `2026.9.13b1`. None of them is in
a published beta, installed, or live-verified. Limits and error codes are listed
in the [command surface](reference/COMMAND-SURFACE.md) and the
[coverage ledger](reference/COMMAND-COVERAGE.md); this section states behaviour
and status only.

**Authorization (advances [#275](https://github.com/clawd-ops/openclaw-hass-node/issues/275)
and [#289](https://github.com/clawd-ops/openclaw-hass-node/issues/289); closes neither):**

- Every dispatch carries a caller principal, and the policy is evaluated at the
  dispatcher before any handler runs. A refusal makes no HA request. A call site
  that supplies no principal is treated as the untrusted household `user`.
- The household `user` role is default-deny over the full command registry: only
  an explicit allowed set (read-only commands plus the service-bearing commands
  the effect policy governs) is reachable, so a newly registered command is
  refused until it is classified. The forbidden entries and the generic-service
  restriction cannot be removed by configuration.
- Service calls are classified by effect, and `ha.call_service` and the light
  wrappers share one decision. Household and HA-admin principals may call the
  everyday-control table in `authz.py`: an explicit list of services per domain
  (lights, switches, media, covers, climate, fans, input helpers, vacuum,
  humidifier, water heater, remote, locks, alarm arm/disarm, scenes, scripts,
  buttons). Nothing that edits configuration is listed, and no domain is open.
  Security devices follow HA's own code model: the node passes a
  caller-supplied `code` to HA (a lock or alarm's own code as text) and does not log it and does not return it in any form it can recognise (a code equal to a logged identifier such as a command or service name is not protected). Deny-class
  services (lifecycle, update, reload, host, shell, shutdown) are refused with
  `SERVICE_DENIED` for every caller, including the operator. Any other service
  is refused for a household user and returns `APPROVAL_REQUIRED` for an HA
  admin, because native approval consumption does not exist yet. An operator
  call to an unclassified service is allowed and logged (temporary).
- The Assist plugin resolves per-node policy by the Gateway's canonical node ID
  only. A caller-supplied node name or self-declared display name is not an
  authorization identity.
- Assist final reply (source, unreleased): the reply is the consecutive text
  blocks after the last tool block of the final assistant message (display-only
  blocks such as canvas or thinking never split it),
  so scratch text written before a tool call no longer reaches the speaker.
  Assistant text is no longer streamed token by token: intermediate text cannot
  be told apart from the answer until the turn ends, so Assist receives the
  answer at the end of the turn. Progress lines and keepalives still stream
  (advances #411; live confirmation pending in the next UAT run).
- Assist progress and expiry (source, unreleased): the duplicating
  `mcp__openclaw__` tool prefix is stripped from progress lines, so one tool call
  is one line; other server prefixes are kept. A command invoked after its Assist
  turn ended (within a few minutes) returns one clear `REQUEST_EXPIRED` result
  with no HA request; an unknown or forged hint still gets `PERMISSION_DENIED`
  (live confirmation of #412 and #413 pending in the next UAT run).
- Same-agent hardening (advances #275): the per-turn authorization block now
  limits OpenClaw-side use to conversation, web search and memory search for
  `user` and `admin`, fences itself with fixed markers that are stripped from
  user text, and states that tool, entity, web and memory content is data. This
  is a soft, prompt-level control. A per-role default agent
  (`identity.user_role_agent_id`, `identity.admin_role_agent_id`) sits between
  `user_agent_map` and `default_agent_id`; a restricted agent makes the limits
  hard. See [Prompt-level versus enforced](design/AUTHORIZATION-MODEL.md#prompt-level-versus-enforced).

**Command behaviour:**

- `ha.call_service` and the light wrappers fetch state after a successful call
  when HA returns no changed states and the caller named concrete entity IDs.
  `changed_states_complete` reports whether every targeted entity was returned.
  Area and device targets are not expanded.
- `ha.list_states`, `ha.history`, `ha.logbook`, the registry, device, service,
  config-entry and automation list commands validate their parameters and
  refuse unknown keys and bad values with `INVALID_PARAM` before any HA request.
  Every other `ha.*` command (the remaining reads, the Supervisor reads, the
  light, reload and update commands and the add-on lifecycle commands) does the
  same for its own accepted keys (source, unreleased); the admin-gated ones
  evaluate their gate first. `system.run`, `system.run.prepare` and
  `system.execApprovals.get`/`set` are out of scope by design: their params are
  the Gateway's forwarded envelope. This is tracked under umbrella
  [#349](https://github.com/clawd-ops/openclaw-hass-node/issues/349) and
  [#288](https://github.com/clawd-ops/openclaw-hass-node/issues/288).
  All eleven `fs.*` commands also refuse unknown keys and null optional keys
  with `INVALID_PARAM` before any filesystem or backup-store access (source,
  unreleased; `fs.diff` `to_version` accepts `null`). The nine `ha.config.*`
  commands (per action), `system.which` and `ping` do the same before any HA
  request or approval-gate evaluation (source, unreleased; `lovelace` `url_path`
  accepts `null`).
  `ha.history` reports an unknown entity as `HA_NOT_FOUND` instead of an empty
  history.
- A timeout on the Supervisor lifecycle POST (`ha.addon_start`, `ha.addon_stop`,
  `ha.addon_restart`, `ha.addon_update`) or on the immediate state read after it
  returns `OUTCOME_UNKNOWN`, not a caller error: the action may or may not have
  happened. The POST is never retried. Callers must not retry automatically and
  should ask the user or operator. `ha.addon_info` reports a current snapshot,
  not action history, so after a restart it cannot show whether the action
  completed, and after an update only a changed version does. Addressed in
  source for [#323](https://github.com/clawd-ops/openclaw-hass-node/issues/323)
  (unreleased).
- A timeout on `ha.update_install` returns `OUTCOME_UNKNOWN` ("the update may
  still be running; check the update entity's state; do not retry") and is never
  retried, because HA's `update.install` blocks until the install ends. The
  plugin appends one sentence to `OUTCOME_UNKNOWN`, `COMMAND_ERROR` and
  `TRANSPORT_ERROR` tool errors telling the model not to try a different command
  for the same change (advances #409, #410; unreleased).
- The registry, device, service, and config-entry list commands accept filters;
  filters are applied after the fetch, so they bound the response a caller
  handles, not the payload HA sends. The HA WebSocket message ceiling is raised
  to 16 MiB. It is a ceiling, not a bound on growth.
- Assist shows a curated remedy sentence for caller-fixable failures on both the
  streaming and non-streaming paths. Uncurated node text never reaches the
  Assist reply.
- When an agent resolves (per-user map, then `default_agent_id`), Assist sends an
  agent-qualified session key (`agent:<agentId>:ha-assist:<id>`) on both the
  streaming and non-streaming paths. On a multi-agent gateway with no resolvable
  agent the turn is refused before any session RPC, naming
  `identity.default_agent_id`. On a single-agent gateway the bare
  `ha-assist:<id>` key is used and the gateway resolves the owner. If the agent
  inventory cannot be read (for example `agents.list` fails), the node also
  sends the bare key; a multi-agent gateway may then reject the turn at
  `sessions.create`.
- The `ha_*` Assist tool wrappers are offered only in Assist sessions (the plugin
  factory returns null unless the session key is `ha-assist:`-shaped,
  [#414](https://github.com/clawd-ops/openclaw-hass-node/issues/414)). Other
  sessions (main, chat, cron, sub-agent) use the core `nodes` tool and its
  approval hook.
- Approvals are role-aware ([#405](https://github.com/clawd-ops/openclaw-hass-node/issues/405),
  [#406](https://github.com/clawd-ops/openclaw-hass-node/issues/406)). A verified HA admin or
  super_admin in a live Assist turn needs no approval for config creates and updates and
  for add-on start and restart (super_admin also add-on stop). Anything agent-initiated,
  and every destructive call, still prompts. The node enforces this from its own turn
  registry; admin add-on stop now prompts instead of being refused. HA restart and stop
  stay denied for every caller (no approval path exists for them yet). See
  `docs/design/AUTHORIZATION-MODEL.md`.
- A non-destructive approval prompt also offers "allow always" ([#407](https://github.com/clawd-ops/openclaw-hass-node/issues/407)):
  the same session, command and action skip the prompt for one hour, until the session
  ends, or until a restart. Destructive calls never offer it; nothing is persisted.
- The multi-agent gateway fix for [#347](https://github.com/clawd-ops/openclaw-hass-node/issues/347)
  is merged at source ([#351](https://github.com/clawd-ops/openclaw-hass-node/pull/351),
  [#366](https://github.com/clawd-ops/openclaw-hass-node/pull/366),
  [#380](https://github.com/clawd-ops/openclaw-hass-node/pull/380)). Evidence:
  the key is qualified in `chat_relay.py` and pinned by
  `test_session_key_is_qualified_only_when_an_agent_resolves`; the startup
  error for several agents with no `default_agent_id` is in `authz.py` and
  pinned by `test_unset_default_is_an_error_only_on_a_multi_agent_gateway`. It
  is unreleased and has no live evidence yet (UAT G7); the issue stays open
  until that evidence is recorded.

**Bounds (advances [#291](https://github.com/clawd-ops/openclaw-hass-node/issues/291)):**

- Gateway ingress: inbound frame size, `paramsJSON` size, nesting and member
  count, and the serialized `node.invoke.result` size are bounded
  (`REQUEST_TOO_LARGE` / `RESULT_TOO_LARGE`).
- `fs.write` content and `fs.patch` text and result are capped and refused
  before any backup snapshot or write.
- `system.run` argv and environment are capped and refused before spawn
  (`ARGV_TOO_LARGE` / `ENV_TOO_LARGE`). On timeout the node kills the whole
  process group; output held by a detached descendant is abandoned after a
  bounded drain and the payload carries `outputIncomplete: true`.
- `system.run` output is still captured in full and truncated after the command
  finishes. Streaming capture with a kill at the cap is not implemented.

**Dependencies and docs:** the locked `urllib3` is 2.8.0 (PYSEC-2026-4175,
-4176, -4177), and the command ledger includes the 2026-09-13 mutation-surface evidence.

**What this does not deliver.** Gateway-forwarded invokes and the local HTTP API
are constructed as operator calls. Assist turns relayed by this node do carry
their resolved principal to the dispatcher through the node-owned turn registry,
but direct `node.invoke` remains operator by default, and the household and
HA-admin gates apply only where a non-operator principal is supplied. The remaining open work is listed under
[Open blockers](#open-blockers).

## Where we are

**Coverage ledger foundation (#268, delivered by merged PR #269):** a deterministic generator
now reconciles 57 dispatcher commands, 57 node advertisements, 31 Assist wrapper
registrations, and 31 action variants into 88 explicit rows. The Assist
registration contract is executable by the plugin and records each tool,
descriptor, factory, node command, accepted tool key, and emitted node-key
mapping. Machine-readable and human-readable artifacts separate evidence method
from behavioral outcome, preserve multiple caller observations, distinguish
curated behavioral-test IDs from source mentions, and render accepted source
keys, aliases, defaults/bounds, field provenance, semantic/error notes,
authorization class, capability conditions, and explicit unavailable reasons.
The check fails on missing or stale command/action/caller coverage, source/action
parameter drift, unacknowledged Assist mapping drift, and stale generated
artifacts. It also refuses a declared row outcome that its highest-ranked live
evidence uniformly contradicts (the ratified evidence-method conflict rule; a
live pass never erases a recorded code-level defect, and cross-caller
disagreement stays a valid `partial`). It also fails when a cited in-repo evidence
document no longer matches its recorded sha256 (`evidence_hashes`), so stale
evidence announces itself. The `ha.reload_config` domain mismatch is
resolved in `2026.9.13b1`: `domain` is optional, only `core` is supported,
and any other value is refused with `UNSUPPORTED` before any HA request instead
of silently reloading core config. Per-domain reload stays unimplemented
pending the effect policy.
This inventory does not enable commands or resolve the other defects it
records.

A failing live observation whose fix has since merged is marked "fix merged, re-probe owed"
in the ledger (derived from `fixed_in_pr`, advances issue 365); its recorded outcome is unchanged.

**Additional repair shipped in `2026.9.13b1` (#266):** generic service calls normalize
`service_data` to canonical `data` without dropping payloads and reject alias
conflicts before HA I/O. Inner handler failures now fail the gateway invoke;
all Assist tools share an error guard that also handles older node envelopes
and SDK `details.nodeError` rejections. Other authoritative gateway rejections
retain their code and retryability separately from local/socket failures.
The cross-language regression suite runs
the real TypeScript wrapper through Python transport/dispatch with mocked HA
I/O and checks the exact nested response rendered by the tool. This is not
deployed and does not implement per-service approval policy.
See [the command contract](reference/COMMAND-SURFACE.md#service-payload-and-result-contract-unreleased-266).

The latest published beta is
[`2026.9.13b1`](https://github.com/clawd-ops/openclaw-hass-node/releases/tag/v2026.9.13b1).
The last observed live installation remains `2026.7.23b1`; the following is the
released `2026.9.13b1` source and artifact surface, not a claim of live UAT:

- **Dual websocket pair.** One `role: node` connection for
  `node.invoke.*`, one `role: operator` connection for the
  conversation relay (`chat.send` + `sessions.messages.subscribe`).
  Independent reconnect loops; one connection failing doesn't take
  the other down. Device is paired as dual-role via the
  `openclaw qr` bootstrap-token flow.
- **HA Assist streams.** Conversation turns route HA Assist → HACS
  integration → node's `/v1/conversation` → operator-role WS → agent
  session, with token-delta streaming back into HA. Mid-turn
  tool-named progress lines (e.g. `🔧 Calling weather...`) surface
  in the conversation UI while the agent is still working.
- **57 commands** registered in the dispatcher and advertised by the node:
  - `ha.*` (40): list/get states, call service, list areas/devices/
    services/entity-registry, config, events, config entries, core logs,
    calendar events, logbook, history, reload config,
    light turn on/off, list automations, check config, the
    Tier A read-only addon surface (`list_addons`, `addon_info`,
    `addon_stats`, `addon_logs`, `addon_changelog`,
    `addon_documentation`, `supervisor_info`), Tier B addon lifecycle
    (`addon_start`, `addon_stop`, `addon_restart`, `addon_update`) and
    `update_install`, authenticated
    by the paired session, with `homeassistant`, `supervisor` and `core_*` always
    denied (the add-on allowlist was removed under #408), and each needing a native approval, and the nine
    `ha.config.*` domain-config editors: `lovelace`, `automation`,
    `script`, `scene`, `helpers`, `area_registry`, `device_registry`,
    `entity_registry`, `config_entries`. Every `ha.config.*` mutation is
    fail-closed in this source revision: every mutation returns
    `PROPOSAL_REQUIRED` without an HA request unless the call carries a valid
    native OpenClaw approval marker, which the plugin hook mints after an operator
    approves that exact call. A caller-supplied proposal ID cannot authorize anything.
  - `fs.*` (11): read/list/stat/glob, write/restore/history/diff,
    move/delete, patch.
  - `system.*` (5): `system.run` (bound in #258 to the Gateway-forwarded
    canonical `systemRunPlan`; reachable only through `exec host=node` after
    an operator approves the plan, direct `nodes.invoke system.run` remains
    refused by the Gateway), `system.which` (basename-only lookup), and the
    native exec-approval protocol methods delivered by #274:
    `system.run.prepare`, `system.execApprovals.get`, and
    `system.execApprovals.set`. See
    [Authorization model](design/AUTHORIZATION-MODEL.md).
  - `ping`.
- **Local HTTP API is fail-closed.** When `local_api_token` is unset
  every non-public path returns `401 NO_TOKEN_CONFIGURED`; when set,
  every non-public path requires `Authorization: Bearer <token>`
  (compared with `hmac.compare_digest`). Public paths are `/health`,
  `/v1/health`, `/v1/conversation/info` (HA addon probes + integration
  config-flow discovery), and health redacts identity details to
  counts/booleans rather than exposing HA UUIDs, agent mappings, or
  lifecycle policy. No host port mapping; the API is only reachable
  inside the Supervisor add-on network by default.
- **HTTP command surface is allowlisted** to `ping` and
  `system.which` as defense in depth — the bearer token gates
  access, the allowlist gates blast radius. The full surface
  remains available over the gateway WS path under operator
  authorization.
- **Secret files** (`node-key.json`, `device-token`) written at
  mode `0o600` with `O_NOFOLLOW`. Path-validated unlink before
  token reset.
- **Tests pass with branch coverage gated at 95%**; all CI gates
  green (ruff check + format, mypy strict, pytest coverage,
  bandit, pip-audit, app-smoke).

## What's not shipped yet

Open work lives in [`TODO.md`](TODO.md). Status-relevant items:

- **Protected filesystem, native config mutations, and Tier B admin commands
  need native approval**: without a marker they return `PROPOSAL_REQUIRED`. The
  plugin hook gates every mutating `ha.config.*` action, the `fs.*` write
  commands, and the Tier B tools (add-on start/stop/restart/update,
  `ha.reload_config`, `ha.update_install`), whether reached through the plugin's
  `ha_*` tools or the core `nodes` tool; an operator-level caller bypassing the
  tool hook can forge a marker (see the authorization model). The former
  secret-based gate and the plugin's per-node policy config are removed. See
  TODO item #20 and
  [#289](https://github.com/clawd-ops/openclaw-hass-node/issues/289).
- **Assist-principal propagation** follows the selected D4 default: one agent
  plus a soft prompt-level block, with a separate agent configurable per user.
  On the wrapper path the dispatcher receives the resolved caller of the Assist
  turn. The full OpenClaw tool surface is not covered, and a Gateway-forwarded
  invoke without the plugin hint is still an operator call. The plugin-carried
  session-key hint is a lookup hint only: an ambiguous hint (matching more than
  one active turn, case-insensitively) is refused, but a direct operator-level `node.invoke` caller could supply a
  guessed key. Accepted limitation for now, tracked on [#275](https://github.com/clawd-ops/openclaw-hass-node/issues/275).
- **HACS brand icon** is the default; upstream PR pending. TODO #21.
- **GHCR per-arch image / HACS index entry** not published yet; Supervisor builds locally on-device. TODO #22.
- **Legacy Home Assistant MCP cutover is complete and permanently closed.** It
  is not a release gate. Remaining caller-policy and parity work is tracked as
  node authorization/contract work, not MCP retirement.

Release-cut itself is automated: `.github/workflows/release-on-version-bump.yml` tags + cuts the GitHub release on every `main` version bump — see [`operations/RELEASE.md`](operations/RELEASE.md).

## Discoverability / sponsorship

- **Funding links.** `.github/FUNDING.yml` and README both live.
  BMC (`buymeacoffee.com/roblandry`) is active.
- **Stars badge.** Added (shields.io social-style badge pointing at
  `/stargazers`).
- **Other badges to consider once published:** HACS default badge
  (after HACS index PR lands), CI status, release version, license.

## Open blockers

The stop-ship findings and external proof gates are tracked in the
[completion roadmap](COMPLETION-ROADMAP.md). The product is not release-
candidate ready. Still open:

- **[#275](https://github.com/clawd-ops/openclaw-hass-node/issues/275) and
  [#289](https://github.com/clawd-ops/openclaw-hass-node/issues/289) are not
  closed.** Real WebSocket invokes without the plugin hint are operator calls and
  direct `node.invoke` is operator-default. Assist-principal propagation covers
  the wrapper path only; the full OpenClaw tool surface is not covered by the
  selected D4 default (same agent plus a soft prompt-level block).
  Native approval consumption, operation binding, and replay protection are not
  implemented.
- **[#403](https://github.com/clawd-ops/openclaw-hass-node/issues/403)** is
  partly advanced: unknown parameters, `.storage/` writes and bad add-on slugs are
  refused before the approval prompt. Malformed parameter values are still
  refused only after a prompt. The add-on allowlist is gone (#408), so a slug
  miss can no longer occur.
- **[#288](https://github.com/clawd-ops/openclaw-hass-node/issues/288)**
  (executable command contract and strict unknown-key refusal) is deferred. No
  complete accepted-key authority exists yet.
- **[#291](https://github.com/clawd-ops/openclaw-hass-node/issues/291)** is
  partly advanced. Still open: streaming output capture with a kill at the cap,
  invoke queue bound and concurrency limits (the queue and concurrency work is
  not merged; PR #377 was closed unmerged), HA REST and WebSocket response
  byte caps with truncation metadata, correct acknowledgement correlation and
  at-most-once redelivery, liveness versus readiness, and audit counters.
- **[#338](https://github.com/clawd-ops/openclaw-hass-node/issues/338):**
  `ha.reload_config` and `ha.update_install` now use native approval like the
  other Tier B commands; they still need a live approval probe against a
  release.
- **Release evidence.** A new beta has not been cut. Live UAT and a Tier B
  install of that beta are required before any release-candidate claim.

## Decision log

- 2026-06-05 — Single node per HA. (owner)
- **Historical, superseded 2026-09-11:** 2026-06-05 — all `/config` mutations
  were designed to go through agent-bridge. Native OpenClaw approval is now the
  sole authority; see the current decision below. (owner)
- 2026-06-05 — Add-on (App) first. HACS only as last resort. (owner)
- 2026-06-05 — Code lives under `~/.openclaw/projects/openclaw-hass-node/`. (owner)
- 2026-06-05 — Docs in `docs/` are source of truth across compactions. (owner)
- 2026-06-05 — Deletes use `trash-cli`, recoverable via `fs.restore`. (owner, issue #1)
- 2026-06-05 — Node must be HA-version-aware via `docs.lookup` against installed version. (owner, issue #1)
- 2026-06-05 — Build process: Claude generates, OpenAI (Codex) reviews; cross-provider required. (owner, issue #1)
- 2026-06-05 — Backups: purpose-built per-file content-addressed
  store under `/share/openclaw-backups/`. No git in `/config`. No
  per-change Supervisor snapshots. (owner, issue #1 round 2)
- 2026-06-05 — HA-native APIs are the default for HA-managed config;
  `fs.patch` is reserved for yaml-only / custom files / blueprints.
  (owner, issue #1 round 2)
- 2026-06-05 — `.storage/` is read-only to the node. The implemented
  command layer refuses writes unconditionally; no caller parameter or
  proposal overrides it. HARD rule. (owner, issue #1 round 2; reconciled to
  implementation 2026-09-11)
- 2026-06-05 — Every approved HA config mutation must verify against the
  running version's breaking changes and include a functional fix
  when impacted. Cross-validated by Codex reviewer. (owner, issue #1
  round 2)
- 2026-06-05 — Assist conversation agent: ship as add-on (app) **plus**
  thin `custom_components/openclaw_hass_node_assist/` HACS integration. Plan A
  (add-on (app) alone) confirmed not viable; see
  `docs/research/CONVERSATION-AGENT.md`. (agent)
- **Historical, superseded 2026-09-11:** 2026-06-05 — proposals were designed
  as gateway-brokered through agent-bridge. The ratified model instead uses
  native OpenClaw approvals; the node does not build a custom proposal
  lifecycle or connect to agent-bridge. See
  `docs/research/AGENT-BRIDGE-CONNECTIVITY.md` for the historical snapshot and
  [`design/AUTHORIZATION-MODEL.md`](design/AUTHORIZATION-MODEL.md) for the
  current decision.
- 2026-06-05 — Language: Python 3.13+ for node and integration. Quality
  gates: `mypy --strict` + `pyright --strict`, Google-style docstrings
  (`ruff` D-rules + `pydoclint`), branch coverage gated at 95% via
  pytest, `ruff` lint/format, `bandit`, `pip-audit`. All gated in
  GitHub Actions. See `docs/operations/QUALITY.md`. (owner, issue #1 round 3)
- 2026-06-05 — MCP retirement: node must demonstrably handle every
  call surface the existing MCP servers serve, across every agent
  that uses them, before retirement. Trigger: zero unhandled
  `mcp__homeassistant*` calls for 7 days *and* a written migration
  inventory. No calendar-based default. Cutover is one PR.
  (owner; superseded 2026-09-11 after confirming the MCP path had already been
  permanently sunset and closed.)
- 2026-06-05 — Versioning: date-based `YYYY.M.PATCH` matching the HA
  release the node is tested against (e.g. `2026.6.0`). Patch
  increments for fixes within a HA release. (agent recommendation,
  the owner "ok either way")
- 2026-06-08 — Conversation relay runs on two parallel gateway WS
  connections (`role: node` for invokes, `role: operator` for chat),
  not a single node-role connection. Gateway role policy is binary
  per-method; `chat.send` is operator-scope. Device paired as
  dual-role via the `openclaw qr` bootstrap-token flow. (agent, after
  the single-connection ChatRelay failed verification.)
