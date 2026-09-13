# Completion Roadmap

**Status:** active planning baseline

**Baseline:** repo `bc95f95`; running app `2026.7.23b1`; verification dated
2026-09-12. Version string is aligned in both `app/config.yaml` and
`app/node/pyproject.toml`; no released tag has advanced past `2026.7.23b1`.

**Evidence source:** [`VERIFICATION-2026-09-11.md`](VERIFICATION-2026-09-11.md)

This is the dependency-ordered path from the current beta to a release where
every supported capability works as advertised. It is intentionally separate
from `STATUS.md`, `TODO.md`, and `design/PLAN.md` while those files contain
known-stale claims.

## How progress is counted

- A checkbox is complete only when the linked implementation is merged and its
  machine-verifiable acceptance evidence is tied to the exact commit and
  released artifact digests. Narrative in a PR is not sufficient by itself.
- `CODE-PROVEN`, `TEST-PROVEN`, `DISPOSABLE-LIVE`, and `PRODUCTION-LIVE` are
  different evidence levels. Unit coverage alone is not a live verification.
- Correct refusal is a passing result when policy says the operation must be
  refused. Unsupported, deferred, or untested is never counted as working.
- Every command **action** and every caller path is counted. A registered command
  with one broken action is not complete.
- New functionality is not advertised until its end-to-end path is usable.
- Documentation is updated from verified behavior after each phase. It is not
  used to manufacture evidence that the phase is complete.
- Approval comes only from Gateway-authenticated native OpenClaw approval. An
  agent cannot approve its own request, and a proposal identifier is audit
  metadata, not authorization.

## Definition of complete

The project is complete only when all of the following are true:

- [ ] One authoritative contract defines every supported command/action,
  parameters, aliases, bounds, result schema, caller paths, authorization, and
  availability conditions.
- [ ] Dispatcher registration, node advertisement, Assist tool descriptors,
  gateway configuration examples, and reference docs agree with that contract.
- [ ] Unknown or misspelled parameters fail explicitly and cannot broaden a
  request to “all entities”, “all history”, or a default mutation.
- [ ] Transport success, command success, and verified postcondition are reported
  separately and consistently.
- [ ] Every mutation is node-enforced as `auto_allow`, `require_approval`, or
  `deny` using trusted caller context and the exact requested effect.
- [ ] Ordinary permitted `light.turn_on` works without an approval prompt.
- [ ] Approval-required operations reach a real human surface and use an
  operation-bound, expiring, single-use approval that the node verifies.
- [ ] Protected filesystem and HA-native config mutations have tested backup,
  conflict, failure, and recovery behavior.
- [ ] Direct node invocation and Assist-plugin invocation have cross-language
  parity tests and authorized live canary evidence.
- [ ] Installed app, plugin, HACS integration, Gateway, and HA versions match the
  tested compatibility matrix.
- [ ] All supported architectures build and pass smoke tests.
- [x] The legacy Home Assistant MCP path is permanently retired. This was
  completed before this baseline and is not a release gate for the node.
- [ ] `STATUS.md`, `TODO.md`, `INSTALL.md`, `design/PLAN.md`,
  `reference/COMMAND-SURFACE.md`, and `operations/UAT-PLAN.md` match the shipped
  release.
- [ ] CI, independent cross-model review, and the full UAT matrix are clean.

## Decisions already established

1. Authorization is based on the effect, not whether the entry point is named
   `ha.call_service` or a convenience wrapper.
2. Normal permitted light control, specifically `light.turn_on`, is
   `auto_allow` **after principal authorization**. It must not need an approval
   prompt merely because it changes state.
3. Other service calls are classified individually as `auto_allow`,
   `require_approval`, or `deny`; whole-domain assumptions are insufficient for
   domains such as `script`, `scene`, `switch`, `cover`, and `lock`.
4. Generic service calls, dedicated admin/update commands, and convenience
   wrappers must converge on the same policy decision point.
5. Read-only/background principals cannot perform any mutation, including an
   otherwise auto-allowed light action, unless trusted context carries a narrow
   delegation for that exact class of effect.
6. Native approval is Gateway-authenticated, bound to the exact operation,
   consumed once, and resolved from an operator device. The node consumes that
   decision; it does not create a second approval authority.

## Proposed design choices to confirm in implementation review

These are the current recommended defaults, not facts inferred from the broken
implementation:

1. Unknown services default to `require_approval` for a trusted interactive
   human principal and `deny` for background/read-only principals until a rule
   explicitly classifies them.
2. Deny rules outrank approval rules, which outrank auto-allow rules.
3. Direct writes to `/config/.storage/` remain unconditionally prohibited, in
   line with the project hard rule and the current safer implementation. If an
   exceptional repair path is ever required, it must be a separately designed
   offline recovery operation, not a generic command flag.
4. The node remains a peripheral. The Gateway owns approval state and presents
   decisions to operator devices; the node accepts only trusted Gateway-owned,
   operation-bound context.

## Retained node-side gaps and trackers

This is the single current list of retained node-side gaps. Merged source work
remains listed when release-tied evidence is still outstanding.

| Retained gap | Tracker |
|---|---|
| Trusted caller propagation and dispatcher-level principal enforcement. The cross-surface ceiling still has two documented alternatives; neither is selected here. | [#275](https://github.com/clawd-ops/openclaw-hass-node/issues/275) |
| Replace the merged interim generic-service denylist with the final per-effect policy while preserving ordinary principal-authorized operations. | [#287](https://github.com/clawd-ops/openclaw-hass-node/issues/287), [#289](https://github.com/clawd-ops/openclaw-hass-node/issues/289) |
| Complete the executable cross-layer command contract and strict dispatcher validation. | [#288](https://github.com/clawd-ops/openclaw-hass-node/issues/288) |
| Consume native approvals for structured HA and filesystem mutations, enforce the policy at the node, and wire accepted decisions to protected writes. | [#289](https://github.com/clawd-ops/openclaw-hass-node/issues/289) |
| Enforce protected recovery storage, precondition/version checks, retention, and recovery behavior. | [#290](https://github.com/clawd-ops/openclaw-hass-node/issues/290) |
| Bound requests, responses, queues, concurrency, and process output; distinguish readiness from liveness. | [#291](https://github.com/clawd-ops/openclaw-hass-node/issues/291) |
| Produce reproducible plugin, HACS, and multi-architecture artifacts. | [#292](https://github.com/clawd-ops/openclaw-hass-node/issues/292) |
| Run exact-artifact, exact-node live validation and bind release evidence to the tested artifacts. | [#293](https://github.com/clawd-ops/openclaw-hass-node/issues/293) |

[#263](https://github.com/clawd-ops/openclaw-hass-node/issues/263) remains the
open progress roll-up. All seven phase checkboxes on that issue remain open;
merged bounded repairs do not complete a phase without the required released-
artifact evidence.

No stop-ship finding is currently untracked.

## Delivery phases

### Phase 0: Contain unsafe behavior and establish the ledger

**Execution status:** IN PROGRESS. The first bounded repair makes mutating
`ha.config.*` commands fail closed against fabricated or unverified proposal
identifiers. The complete approval verifier remains a later Phase 2 deliverable.

- [x] Implement source containment: every mutating `ha.config.*` action fails
  closed until a real approval verifier is available. Arbitrary proposal
  strings and spoofed approval flags cannot authorize an HA request.
  Evidence: `app/node/tests/test_ha_config_mutation_boundary.py`, covering all
  19 actions through direct handlers and the dispatcher, plus every helper
  namespace. Existing API adapters are preserved behind the shared boundary;
  their isolated unit tests explicitly stub it and are not authorization proof.
- [ ] Complete independent review, PR checks and operator-approved deployment
  of the config containment repair. The real approval bridge remains Phase 2.
- [x] Add an interim node-side effect denylist for generic services that can
  bypass dedicated lifecycle, update, reload, host, shell, or shutdown gates.
  Preserve principal-authorized `light.turn_on`; do not leave the generic P0
  bypass open while the complete policy engine is built. Source containment is
  implemented under #287; deployment/release evidence remains a later gate.
- [x] Publish a command/action/caller-path coverage ledger generated from the
  current source. Record parameters, aliases, limits, response schema, policy,
  feature availability, and an acceptance-test ID for each row. Foundation work
  is delivered by merged PR #269 under #268: 56 registered commands,
  56 advertised commands, 30 executable Assist registrations, and 31 action variants
  produce 87 deterministic rows in
  [`reference/COMMAND-COVERAGE.md`](reference/COMMAND-COVERAGE.md) and
  `reference/command-coverage.json`. The generated rows distinguish evidence
  method from outcome per caller, separate curated acceptance-test IDs from
  source mentions, and expose field provenance. Missing or stale
  command/action/caller coverage, exact per-action parameter drift, unregistered
  or removed Assist paths, and unacknowledged wrapper/node mapping mismatches
  fail the check. Known mismatches remain visible with issue/reason metadata.
  Policy/semantic fields remain explicitly manual and are not executable
  runtime schemas; strict validation remains in later Phase 1 slices.
- [ ] Record the exact app, plugin, HACS, Gateway, HA Core, Supervisor, and
  architecture versions used by each live verification. The recording artifact
  now exists at [`COMPATIBILITY-MATRIX.md`](COMPATIBILITY-MATRIX.md) with the
  required field set, per-field source provenance (`observed-live`,
  `repo-declared`, `not recorded`), the current installed environment observed
  2026-09-12, and the 2026-09-11 verification environment with its unrecorded
  fields marked rather than reconstructed. The item stays unchecked: the
  architecture the artifact actually runs on and the loaded plugin/integration
  versions are not observable through the node's read-only surface, so no
  environment is yet completely recorded.
- [ ] Mark all currently unsafe, unreachable, or unverified operations as such in
  user-facing descriptions. Do not advertise them as working during repair.
- [ ] File or link tracker issues for every untracked stop-ship finding in this
  roadmap and make one parent milestone the progress roll-up. Filing half is
  done: [retained node-side gaps and trackers](#retained-node-side-gaps-and-trackers)
  maps every stop-ship finding to #275 and #287 through #293, and no finding is
  untracked. The roll-up half is not: #263 is a roll-up *issue*, and the
  repository milestone list is empty.
  As this item is currently worded, #263 cannot satisfy the milestone half, so the
  item stays unchecked. Closing it needs an operator decision: either create the
  milestone and attach the owning issues, or revise this wording to accept a
  roll-up issue.

**Exit:** no unverified string can authorize an HA config mutation, and every
completion claim has a row in the coverage ledger.

### Phase 1: Create one executable command contract

- [ ] Define one machine-readable specification for command/action names,
  canonical parameters, accepted aliases, types, bounds, defaults, result/error
  schemas, authorization class, and capability conditions.
- [ ] Validate strictly at the node boundary and reject unknown keys. Normalize
  aliases before policy evaluation; reject conflicting aliases.
- [ ] Derive dispatcher registration and node advertisement from the same
  contract, with explicit reasons for unavailable capabilities.
- [ ] Generate or contract-test the TypeScript descriptors, wrapper mappings,
  gateway allowlist examples, and command reference against that source.
- [ ] Fix all known contract drift:
  - [x] `fs.read` `offset`/`length` and `encoding` (#257, branch `fix/257-fs-read-offset-length`).
  - [ ] `system.which` `binary` versus `name`.
  - [ ] `ha.list_states` filter naming and result bounds.
  - [ ] `ha.history` and `ha.logbook` time/entity aliases.
  - [x] `ha.list_automations` filtering before trace expansion (#259).
  - [ ] `ha.reload_config` domain semantics.
  - [ ] `ha.call_service` `service_data` versus `data`. PR #267 merged at
    `2f44a65`: node normalizes `service_data` → `data` at the boundary,
    accepts the legacy alias, and rejects conflicting duplicates before HA
    I/O. Covered by `tests/test_ha_commands.py` and the cross-language
    `tests/contracts/invoke_fixture.py`. Not yet in a released artifact
    (`2026.7.23b1` still ships the pre-fix behavior); release-tie
    verification stays with the Phase 6 packaging gate.
  - [ ] URL/path/query percent-encoding.
- [ ] Propagate handler errors through the transport and plugin. Success text is
  emitted only after the inner operation succeeds. PR #267 merged at `2f44a65`:
  node projects inner failure at the boundary and the plugin rejects legacy
  inner failures (including SDK `details.nodeError`). CI accepted the change.
  Broader coverage across every command family and release-tie verification
  remain open, so the item stays unchecked. Node refusals, HA failures,
  gateway authorization/schema/policy rejections, transport errors, and
  malformed results are distinguished for the covered paths; gateway
  retryability is preserved.
- [ ] Add cross-language tests that execute real TypeScript wrapper output
  through the Python dispatcher against a controlled HA/Supervisor stub.
  First fixture merged with PR #267 at `2f44a65`
  (`app/node/tests/contracts/invoke_fixture.py`) covers service payloads,
  refusals, HA failures, and legacy envelopes. It runs actual wrapper output
  and Python gateway result frames and asserts the exact nested
  changed-state payload in the returned tool text; broader command-family
  coverage across the 87-row ledger is still open.

**Exit:** every supported request has deterministic parameters and result
semantics on both direct and Assist paths; unknown input cannot broaden scope.

### Phase 2: Build real authorization and approval

The ratified model is defined in
[`design/AUTHORIZATION-MODEL.md`](design/AUTHORIZATION-MODEL.md). Native
OpenClaw owns Gateway authentication, operation binding, single-use approval
consumption, durable approval state, and resolution from operator devices. This
repository consumes that authority; it does not build a parallel lifecycle,
store, or approval application.

- [ ] Carry trusted caller/delegation context in a Gateway-owned envelope. Never
  trust `actor`, `role`, or `proposal_id` supplied in ordinary command params.
- [ ] Enforce the computed principal ceiling at the dispatcher before any
  handler runs. Keep the two unresolved cross-surface alternatives in
  [#275](https://github.com/clawd-ops/openclaw-hass-node/issues/275) without
  selecting one in implementation or documentation.
- [ ] Implement a versioned node-side service policy keyed by `domain.service`,
  with optional target/data constraints and outcomes `auto_allow`,
  `require_approval`, and `deny`.
- [ ] Route `ha.call_service`, light wrappers, reload/update helpers, lifecycle
  commands, and HA-native config actions through the same applicable policy.
- [ ] Integrate the plugin permission request path for structured HA and
  filesystem mutations. Require a Gateway-authenticated decision bound to the
  node, trusted principal, exact command/action and canonical parameters,
  policy revision, expiry, and prior-state/version preconditions. Denial,
  timeout, mutation, replay, and concurrent reuse must fail closed.
- [ ] Consume each `allow-once` decision exactly once and refuse self-approval.
  Approval prompts and decisions remain on native operator surfaces.
- [ ] Wire accepted native approvals to protected `fs.*` and every mutating
  `ha.config.*` action, with precondition revalidation immediately before apply.
- [ ] Record domain-appropriate preimages and expose explicit recovery actions.
  Do not promise rollback for irreversible real-world service effects.
- [ ] Detect and bind the installed HA Core version before accepting or applying
  an HA config mutation; invalidate approval if the version changes.
- [ ] Require version-matched docs, relevant breaking-change evidence, config or
  domain validation, and expected prior-state checks before enabling writes.
- [ ] Make approval consumption and mutation replay safe across timeout,
  disconnect, redelivery, crash, and concurrent submit before enabling writes.
- [ ] Define the durable commit/reconciliation boundary for the case where HA
  accepts a side effect and the node dies before recording `applied`. Inject a
  crash at every state transition and prove restart cannot execute the effect
  twice or lose the final operation outcome.
- [x] Ratify Tier B add-on lifecycle authorization as trusted principal context
  plus node-enforced slug allow/deny and effect policy. Resolved in #270:
  lifecycle ops (addon_start/stop/restart/update) require only `allowAdminOps`
  with pairing-session authentication and slug policy. #270 left admin ops
  (reload_config, update_install) on `adminToken`; the ratified authorization
  model removes the add-on admin token entirely in favor of OpenClaw's native
  approval APIs, so those move to operator approval. See
  `design/AUTHORIZATION-MODEL.md`. Dedicated and generic service paths must
  still converge on the same decision (Phase 2 remainder).

**Exit:** direct, Assist, alias, replay, spoofing, and generic-service paths
cannot bypass policy; an operator can resolve a native approval end to end from
an operator device, and the node consumes it exactly once.

### Phase 3: Finish command behavior and resilience

- [ ] Add supported `limit`, filter, pagination, or byte/range bounds to every
  advertised collection/read that needs them; explicitly reject unsupported
  bounds rather than ignoring them.
- [ ] Distinguish unknown entities from valid empty history where HA permits it.
- [ ] Make `ha.addon_update` and `ha.update_install` advertisement, gateway
  permission, runtime capability, authorization, and plugin exposure agree.
  Source is merged on `main` but not yet in a released artifact (per the
  "How progress is counted" rule); tick belongs to the release-tie gate.
  Delivered by #260 via PR #284 at `762dc86`: both commands are now advertised
  in `_NODE_COMMANDS`, `_INTENTIONALLY_UNADVERTISED` is the single documented
  exemption list, and a drift gate (`test_command_coverage_ledger`,
  `test_gateway_ws`) fails CI on any future registered-vs-advertised mismatch.
  Plugin/Assist and admin-gated authorization paths were already wired; the
  new gate binds them to the advertised surface. Live update-install execution
  evidence stays with the Phase 3 durable-receipt item below. Confirmed by
  observation on 2026-09-12: the installed `2026.7.23b1` node advertises 51
  commands and does not include `ha.addon_update` or `ha.update_install`, while
  source advertises 56. See
  [`COMPATIBILITY-MATRIX.md`](COMPATIBILITY-MATRIX.md).
- [ ] Give app/Core update operations a durable operation ID and receipt that
  survives the process being terminated by its own update. Reconcile exact
  installed version and artifact digest after reconnect; never repeat the effect
  solely because the response was lost.
- [x] Decide and implement the supported shell path. Shell remains in scope
  via OpenClaw's native exec-approval contract: `system.run.prepare` produces
  the canonical `systemRunPlan`, the operator approves, and the Gateway
  forwards `system.run` bound to that plan. The node re-validates the
  forwarded plan (argv, rawCommand, cwd within allowed roots, credential-
  shaped env keys rejected) and executes. Direct `nodes.invoke system.run` is
  refused by the Gateway rather than renamed to evade it. Delivered in #258.
  Live operator allow/deny cycle observation remains as a UAT gate rather
  than a source change.
- [ ] Repair pending-invoke acknowledgement so interleaved frames are not dropped
  and reconnect/replay cannot execute a mutation twice.
- [ ] Add HA API feature/version detection and explicit unsupported responses for
  endpoints absent in a supported HA version.
- [ ] Advertise HA Core version and refresh it after updates; Phase 2 must already
  detect and bind it for write authorization.
- [ ] Implement `docs.lookup(topic, version=current)` with bounded, integrity-
  checked cache behavior.
- [ ] Implement `docs.breaking_changes(version=current, since=?, domain=?)`.
- [ ] Keep version-matched approval evidence and pre-apply validation enforced
  from Phase 2; add post-apply verification to each mutation transaction where
  HA exposes the required operation.
- [ ] Reserve backup/trash internal roots from generic filesystem mutation and
  fail closed if recovery metadata is missing, corrupt, full, or unwritable.
- [ ] Implement the documented backup quota, garbage collection, and pinning;
  test low-disk and interrupted-capture behavior.
- [ ] Bound encoded HA REST/WS response bytes and add cursor/truncation metadata
  where collections can exceed a safe response. Stream process output and kill
  at the configured cap instead of truncating after full buffering.
- [ ] Bound request frames, JSON decoding, `fs.write`/patch content, service data,
  argv/env, queued invokes, concurrent HA requests, and final result
  serialization before allocating unbounded memory or performing side effects.
- [ ] Add separate liveness and readiness signals for HA REST/WS, Supervisor,
  node connection, chat connection, pairing, version, and command parity.
- [ ] Add structured, secret-safe audit events and counters for command errors,
  reconnects, redeliveries, truncations, approval age, and approval outcomes.

**Exit:** every supported functional path has success, refusal, failure,
reconnect, and recovery evidence.

### Phase 4: Complete Assist and human-facing UX

- [ ] Finish add-on ingress configuration. Any approval view is
  presentation-only: it may display native Gateway state or relay the
  operator's decision to the native approval API, but it cannot mint, store, or
  resolve authority independently.
- [ ] Add the option to show or hide tool progress in HA Assist.
- [ ] Verify tool-progress and final-answer rendering on real HA clients.
- [ ] Implement durable Assist transcript/resume state across disconnects and
  add-on restarts.
- [ ] Verify user mapping, agent routing, signature expiry, reconnect, concurrent
  turns, cancellation, timeout, and stale-frame isolation end to end.
- [ ] Reconcile all identity claims with actual invoke-time enforcement. Prompt
  disclaimers are not counted as a security boundary.
- [ ] Minimize and justify every Gateway scope requested by each connection.
  Prove the add-on credential cannot reach unrelated write, approval, secret, or
  session APIs; split credentials/connections where that materially reduces
  authority.

**Exit:** Rob can configure, observe, approve, resume, and diagnose the system
from the supported UI without approvals disappearing into an unreachable queue.

### Phase 5: Packaging, automation, and distribution

- [ ] Add the plugin build step and publishable artifact; test install/upgrade
  against the supported OpenClaw SDK range.
- [ ] Publish GHCR images for every advertised architecture and prove Supervisor
  installation from the published artifacts.
- [ ] Finish HACS brand/icon acceptance and validate the store presentation.
- [ ] Generate the command/protocol documentation site from the authoritative
  contract.
- [ ] Finish issue triage automation and proactive GitHub event handling with the
  real bridge-layer enforcement described in `AGENTS.md`.
- [ ] Track the external Gateway sandbox dependency separately and do not claim
  reviewer isolation until it is software-enforced.
- [ ] Add explicit compatibility and migration policy for app options, plugin
  config, command contract, HA APIs, and Gateway APIs.
- [ ] Validate every published plugin configuration example against the shipped
  manifest in CI. Remove or migrate the stale `allowServices`,
  `allowReadEntities`, and `allowCalendars` options with actionable errors.
- [ ] Build from locked dependencies and digest-pinned bases; emit provenance and
  artifact digests so the tested image is the released image.
- [ ] Make release creation consume successful CI artifacts and refuse tagging
  when required CI, compatibility, or UAT gates are absent or failing.
- [ ] Add a real HACS integration harness and include required plugin installation
  in UAT before testing Assist behavior.
- [ ] Preserve date-based release/version synchronization where required, while
  versioning the plugin and command contract independently when their compatibility
  requires it.

**Exit:** a clean install and upgrade use published artifacts, supported versions,
and enforced automation rather than local-only build state.

### Phase 6: Verification, migration, and release gate

- [ ] Pure contract/security suite covers every command action, policy outcome,
  boundary value, alias, unknown key, and no-side-effect refusal.
- [ ] Cross-language suite covers descriptor → wrapper → policy → Gateway frame →
  Python dispatcher → HA/Supervisor stub → semantic result.
- [ ] Disposable HA suite covers native config save/delete/recovery, proposal
  expiry/replay/races, filesystem backup/restore, lifecycle, update, reconnect,
  and partial failures.
- [ ] Authorized production canary covers narrow reads first, then explicit known
  light targets and separately approved mutations with before/after evidence.
- [ ] Test both advertised architectures; record artifact digests.
- [x] Keep the retired Home Assistant MCP path closed. The historical retirement
  checker and seven-day window are not part of this release gate.
- [ ] Reconcile all canonical docs from verified behavior and remove stale
  counts, versions, names, policies, architectures, and paths.
- [ ] Run full CI, security checks, clean cross-model review, upgrade test,
  rollback rehearsal, and post-install capability comparison.
- [ ] Protect required check names and bind release/UAT evidence to exact commit,
  clean checkout, timestamp, image/plugin digests, versions, architecture, caller
  path, expected result, observed postcondition, and authenticated approver.
- [ ] Cut the release only after the coverage ledger has no `failed`,
  `unsupported-but-advertised`, or `not tested` rows.

**Exit:** the installed release, not merely the branch or PR, satisfies the
definition of complete.

## Tracker crosswalk

| Existing tracker | Roadmap phase | Current interpretation |
|---|---:|---|
| [#257](https://github.com/clawd-ops/openclaw-hass-node/issues/257) | 1 | Resolved at source by PR #278 at `bf63142`: `fs.read` honors `offset`/`length` with validated byte-range semantics. |
| [#258](https://github.com/clawd-ops/openclaw-hass-node/issues/258) | 3 | Resolved by PRs #274 and #277 at `222ce44`/`6680114`: `system.run` bound to the native exec-approval contract; live allow/deny canary still owed as a UAT gate. |
| [#259](https://github.com/clawd-ops/openclaw-hass-node/issues/259) | 1 | Resolved at source by PR #281 at `bfb8a95`: `ha.list_automations` server-side filtering with fail-closed params. |
| [#260](https://github.com/clawd-ops/openclaw-hass-node/issues/260) | 1, 3 | Advertisement + drift gate resolved by PR #284 at `762dc86`; live update-path canary and durable receipt still outstanding under Phase 3. |
| [#261](https://github.com/clawd-ops/openclaw-hass-node/issues/261) | 1, 6 | Skill/docs contract corrected by PR #283 at `bc95f95`: skill now describes the real `ping` + `fs.*` + `system.*` + `ha.*` surface and states the node does **not** advertise `file.fetch` / `dir.list` / `dir.fetch`; `allowReadPaths` does not apply here. Acceptance direction 1 (implement compatible `file.fetch`/`dir.*` handlers with policy tests) is deliberately deferred; this repo takes direction 2. |
| [#262](https://github.com/clawd-ops/openclaw-hass-node/issues/262) | 2 | ~~Ratify one lifecycle policy and remove contradictory token claims.~~ Resolved in #270: lifecycle/admin split implemented and tested. Remaining: converge dedicated and generic service paths on the same policy decision. |
| TODO 7 | 5 | Issue triage automation. |
| TODO 11 | Closed | Home Assistant MCP retirement is complete; residual caller-policy work belongs to phase 2 and does not reopen it. |
| TODO 12 | 5 | Generated documentation. |
| TODO 13 | 5 | Proactive GitHub events; includes external Gateway dependency. |
| TODO 17 | 0 | Replace stale issue list with the tracker crosswalk. |
| [#279](https://github.com/clawd-ops/openclaw-hass-node/issues/279) | 5 | TypeScript API documentation tracker; complete and not an authorization deliverable. |
| [#285](https://github.com/clawd-ops/openclaw-hass-node/issues/285) | Post-release | Filesystem namespace convergence remains explicit post-release design work. |
| TODO 20 | 2 | Wire native OpenClaw approvals and node-enforced effect policy; implementation is tracked by #275 and #289. |
| TODO 21 | 5 | HACS branding. |
| TODO 22 | 5 | GHCR publishing and HACS index. |
| TODO 23 | 3 | Version-rooted docs and breaking-change checks. |
| TODO 27 | 4 | Ingress configuration UI. Any approval view is presentation-only and uses the native Gateway authority. |
| TODO 32 | 4 | Tool-progress option. |
| TODO 35 | 4, 6 | Real-client tool-progress verification. |
| TODO 36 | 1, 3, 6 | Script migration command gaps and live install verification. |
| TODO 37 | 5 | Plugin build and publish. |
| TODO 38 | 4 | Durable transcript/resume UI. |

## Evidence artifacts to keep in the repo

- `docs/VERIFICATION-2026-09-11.md`: discovery evidence at the baseline above.
- This roadmap: dependency order and completion checklist.
- Generated command/action coverage ledger:
  [`reference/COMMAND-COVERAGE.md`](reference/COMMAND-COVERAGE.md) and
  `reference/command-coverage.json`, with one row per command/action and explicit
  state for each caller path.
- Compatibility matrix: tested app/plugin/HACS/Gateway/HA/Supervisor versions and
  architectures, at [`COMPATIBILITY-MATRIX.md`](COMPATIBILITY-MATRIX.md). One row
  set per live verification, with per-field source provenance and explicit
  `not recorded` markers.
- UAT result packet per release candidate: exact artifact digests, environment,
  tests, expected refusals, observed postconditions, and unresolved rows.
- Native approval threat model and node-consumption protocol: trusted fields,
  operation binding, expiry, replay prevention, crash recovery, and audit
  retention.

## Cross-model review rule

Roadmap and implementation reviews deliberately use different model families.
One model builds the design; another searches for omissions, unsafe assumptions,
stale evidence, and tests that can pass without proving the claimed outcome.
Disagreements are resolved against code, tests, live behavior, and explicit user
decisions. Neither model's confidence is evidence.
