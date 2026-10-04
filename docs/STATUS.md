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

All 19 mutating actions across the nine `ha.config.*` commands now deny
unverified proposal identifiers before any HA request. The shared boundary has
no caller-controlled override. Read-only config actions and light control are
unchanged. API-adapter tests explicitly stub the boundary to retain dormant
adapter coverage; the independent boundary suite uses real authorization code
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
  wrappers share one decision. `light.turn_on` and `light.turn_off` are the only
  services auto-allowed for household and HA-admin principals. Deny-class
  services (lifecycle, update, reload, host, shell, shutdown) are refused with
  `SERVICE_DENIED` for every caller, including the operator. Any other service
  is refused for a household user and returns `APPROVAL_REQUIRED` for an HA
  admin, because native approval consumption does not exist yet. An operator
  call to an unclassified service is allowed and logged (temporary).
- The Assist plugin resolves per-node policy by the Gateway's canonical node ID
  only. A caller-supplied node name or self-declared display name is not an
  authorization identity.
- The node logs a startup warning when `addon_lifecycle.allowlist` is populated,
  reminding the operator that the Gateway also needs `allowAdminOps` for this
  node.

**Command behaviour:**

- `ha.call_service` and the light wrappers fetch state after a successful call
  when HA returns no changed states and the caller named concrete entity IDs.
  `changed_states_complete` reports whether every targeted entity was returned.
  Area and device targets are not expanded.
- `ha.list_states`, `ha.history`, `ha.logbook`, the registry, device, service,
  config-entry and automation list commands validate their parameters and
  refuse unknown keys and bad values with `INVALID_PARAM` before any HA request.
  Other read handlers (for example `ha.list_areas`, `ha.get_config`,
  `ha.list_events`, `ha.list_addons`, `ha.supervisor_info`) still ignore
  unknown keys and make the HA request anyway; `ha.core_logs` checks only
  `lines`. This is tracked under umbrella
  [#349](https://github.com/clawd-ops/openclaw-hass-node/issues/349) and
  [#288](https://github.com/clawd-ops/openclaw-hass-node/issues/288).
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
- The registry, device, service, and config-entry list commands accept filters;
  filters are applied after the fetch, so they bound the response a caller
  handles, not the payload HA sends. The HA WebSocket message ceiling is raised
  to 16 MiB. It is a ceiling, not a bound on growth.
- Assist shows a curated remedy sentence for caller-fixable failures on both the
  streaming and non-streaming paths. Uncurated node text never reaches the
  Assist reply.

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
-4176, -4177), the install guide documents the `allowAdminOps`
step, and the command ledger includes the 2026-09-13 mutation-surface evidence.

**What this does not deliver.** Gateway-forwarded invokes and the local HTTP API
are constructed as operator calls, and the Assist principal is not yet carried
to the dispatcher. Direct `node.invoke` is therefore operator by default, and the
household and HA-admin gates apply only where a non-operator principal is
supplied. The remaining open work is listed under
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
artifacts. The lifecycle `admin_token` mismatch is resolved in `2026.9.13b1`:
lifecycle wrappers require `allowAdminOps` and the node's slug policy without
injecting another token. The separate `ha.reload_config` domain mismatch is
also resolved in `2026.9.13b1`: `domain` is optional, only `core` is supported,
and any other value is refused with `UNSUPPORTED` before any HA request instead
of silently reloading core config. Per-domain reload stays unimplemented
pending the effect policy.
This inventory does not enable commands or resolve the other defects it
records.

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
    by the paired session and constrained by an explicit slug allowlist, with no
    separate lifecycle admin token, and the nine
    `ha.config.*` domain-config editors: `lovelace`, `automation`,
    `script`, `scene`, `helpers`, `area_registry`, `device_registry`,
    `entity_registry`, `config_entries`. Every `ha.config.*` mutation is
    fail-closed in this source revision: every mutation returns
    `PROPOSAL_REQUIRED` without an HA request. A caller-supplied proposal ID
    cannot authorize it; the trusted verifier and human round-trip are absent.
  - `fs.*` (11): read/list/stat/glob, write/restore/history/diff,
    move/delete, patch.
  - `system.*` (5): `system.run` (bound in #258 to the Gateway-forwarded
    canonical `systemRunPlan`; reachable only through `exec host=node` after
    an operator approves the plan, direct `nodes.invoke system.run` remains
    refused by the Gateway), `system.which` (basename-only lookup), and the
    native exec-approval protocol methods delivered by #274:
    `system.run.prepare`, `system.execApprovals.get`, and
    `system.execApprovals.set`. There is no add-on admin token; the inert
    `OPENCLAW_ADMIN_TOKEN` gate and `_admin_token_ok` helper have been
    removed from `commands/system_run.py`. See
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

- **Protected filesystem and all native config mutations are unavailable** with
  `PROPOSAL_REQUIRED` in this source revision; native plugin approvals are not
  yet wired to those protected mutations. See TODO item #20 and
  [#289](https://github.com/clawd-ops/openclaw-hass-node/issues/289).
- **Assist-principal propagation** to the dispatcher is pending a design
  decision. Until it lands, every Gateway-forwarded invoke is an operator call.
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
  closed.** Real WebSocket invokes are operator calls. Assist-principal
  propagation is pending a design decision, direct `node.invoke` is
  operator-default, and the cross-surface ceiling is an operator choice.
  Native approval consumption, operation binding, and replay protection are not
  implemented.
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
  `ha.reload_config` and `ha.update_install` still carry the inert admin-token
  gate and are unreachable until they move to operator approval.
- **Release evidence.** A new beta has not been cut. Live UAT and a Tier B
  install of that beta are required before any release-candidate claim.

## Decision log

- 2026-06-05 — Single node per HA. (Rob)
- **Historical, superseded 2026-09-11:** 2026-06-05 — all `/config` mutations
  were designed to go through agent-bridge. Native OpenClaw approval is now the
  sole authority; see the current decision below. (Rob)
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
- 2026-06-05 — `.storage/` is read-only to the node. The implemented
  command layer refuses writes unconditionally; no caller parameter or
  proposal overrides it. HARD rule. (Rob, issue #1 round 2; reconciled to
  implementation 2026-09-11)
- 2026-06-05 — Every approved HA config mutation must verify against the
  running version's breaking changes and include a functional fix
  when impacted. Cross-validated by Codex reviewer. (Rob, issue #1
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
  GitHub Actions. See `docs/operations/QUALITY.md`. (Rob, issue #1 round 3)
- 2026-06-05 — MCP retirement: node must demonstrably handle every
  call surface the existing MCP servers serve, across every agent
  that uses them, before retirement. Trigger: zero unhandled
  `mcp__homeassistant*` calls for 7 days *and* a written migration
  inventory. No calendar-based default. Cutover is one PR.
  (Rob; superseded 2026-09-11 after confirming the MCP path had already been
  permanently sunset and closed.)
- 2026-06-05 — Versioning: date-based `YYYY.M.PATCH` matching the HA
  release the node is tested against (e.g. `2026.6.0`). Patch
  increments for fixes within a HA release. (agent recommendation,
  Rob "ok either way")
- 2026-06-08 — Conversation relay runs on two parallel gateway WS
  connections (`role: node` for invokes, `role: operator` for chat),
  not a single node-role connection. Gateway role policy is binary
  per-method; `chat.send` is operator-scope. Device paired as
  dual-role via the `openclaw qr` bootstrap-token flow. (agent, after
  the single-connection ChatRelay failed verification.)
