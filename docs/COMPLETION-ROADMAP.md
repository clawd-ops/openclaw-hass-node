# Completion Roadmap

**Status:** active planning baseline

**Baseline:** repo `ab03579`; running app `2026.7.23b1`; verification dated
2026-09-11

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
- No agent may approve its own proposal. A proposal identifier is audit metadata,
  not authorization.

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
   `auto_allow` **after principal authorization**. It must not need an additional
   proposal merely because it changes state.
3. Other service calls are classified individually as `auto_allow`,
   `require_approval`, or `deny`; whole-domain assumptions are insufficient for
   domains such as `script`, `scene`, `switch`, `cover`, and `lock`.
4. Generic service calls, dedicated admin/update commands, and convenience
   wrappers must converge on the same policy decision point.
5. Read-only/background principals cannot perform any mutation, including an
   otherwise auto-allowed light action, unless trusted context carries a narrow
   delegation for that exact class of effect.
6. An agent cannot approve its own proposal.

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
4. The node remains a peripheral. The Gateway presents approvals; the node
   accepts only trusted Gateway-owned context or a verifiable, operation-bound
   capability. The exact current Gateway API must be validated before choosing
   the wire format.

## Stop-ship findings

These are the current highest-priority facts. They must not be hidden among the
older issue list.

- Baseline finding: `ha.config.*` accepted nonempty caller-supplied proposal IDs
  without verification. The Phase 0 source repair now fails closed for all 19
  mutations, with zero-HA-request regression coverage. It is not deployed, and
  the trusted verifier / human approval flow remains open.
- Protected `fs.*` refuses correctly, but no accepted-proposal path exists.
- `ha.call_service` has no node-enforced per-service policy and can reach effects
  that dedicated admin commands try to gate.
- The Assist wrapper forwards `service_data`; the node reads `data`, so service
  options can disappear silently.
- The transport can mark a returned `{ok: false}` handler payload as outer
  `ok: true`, and plugin tools can render it as success.
- Dispatcher parameters are not schema-validated. Misspellings and stale names
  silently fall back to unbounded reads or default actions.
- Registered commands and advertised commands are separate manual lists.
- The existing proposal queue has no authenticated human endpoint and includes
  self-approved history.
- Recovery data under `/share/openclaw-backups` and `/share/openclaw-trash` is
  reachable through the generic writable `/share` surface, and documented
  retention/pinning is not implemented.
- Several HA response and process-output paths can buffer unbounded data.
- Health can report `ok: true` without proving HA or both Gateway connections
  are ready; release tagging is not tied to a successfully tested artifact.

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
- [ ] Add an interim node-side effect denylist for generic services that can
  bypass dedicated lifecycle, update, reload, host, shell, or shutdown gates.
  Preserve principal-authorized `light.turn_on`; do not leave the generic P0
  bypass open while the complete policy engine is built.
- [ ] Publish a command/action/caller-path coverage ledger generated from the
  current source. Record parameters, aliases, limits, response schema, policy,
  feature availability, and an acceptance-test ID for each row.
- [ ] Record the exact app, plugin, HACS, Gateway, HA Core, Supervisor, and
  architecture versions used by each live verification.
- [ ] Mark all currently unsafe, unreachable, or unverified operations as such in
  user-facing descriptions. Do not advertise them as working during repair.
- [ ] File or link tracker issues for every untracked stop-ship finding in this
  roadmap and make one parent milestone the progress roll-up.

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
  - [ ] `fs.read` `offset`/`length` and `encoding`.
  - [ ] `system.which` `binary` versus `name`.
  - [ ] `ha.list_states` filter naming and result bounds.
  - [ ] `ha.history` and `ha.logbook` time/entity aliases.
  - [ ] `ha.list_automations` filtering before trace expansion.
  - [ ] `ha.reload_config` domain semantics.
  - [ ] `ha.call_service` `service_data` versus `data`.
  - [ ] URL/path/query percent-encoding.
- [ ] Propagate handler errors through the transport and plugin. Success text is
  emitted only after the inner operation succeeds.
- [ ] Add cross-language tests that execute real TypeScript wrapper output
  through the Python dispatcher against a controlled HA/Supervisor stub.

**Exit:** every supported request has deterministic parameters and result
semantics on both direct and Assist paths; unknown input cannot broaden scope.

### Phase 2: Build real authorization and approval

- [ ] Validate the current deployed Gateway approval APIs before adopting or
  replacing the historical `propose_edit` / `resolve_proposal` design.
- [ ] Carry trusted caller/delegation context in a Gateway-owned envelope. Never
  trust `actor`, `role`, or `proposal_id` supplied in ordinary command params.
- [ ] Implement a versioned node-side service policy keyed by `domain.service`,
  with optional target/data constraints and outcomes `auto_allow`,
  `require_approval`, and `deny`.
- [ ] Route `ha.call_service`, light wrappers, reload/update helpers, lifecycle
  commands, and HA-native config actions through the same applicable policy.
- [ ] Implement durable proposal states: `pending`, `approved`, `rejected`,
  `expired`, `applying`, `applied`, and `failed`.
- [ ] Bind approval to node, trusted principal, command/action, canonical params
  digest, concrete target where feasible, policy revision, prior-state/version
  precondition, expiry, and one-time nonce.
- [ ] Require independent authenticated human approval and block self-approval,
  replay, changed-operation reuse, expired approvals, and concurrent double use.
- [ ] Present pending proposals in the add-on ingress UI. Support review,
  approve/reject, expiry, failure detail, and applied result.
- [ ] Keep ingress presentation-only for approval authority. The add-on UI may
  display and relay a decision, but cannot mint or resolve authorization without
  a Gateway-authenticated human action protected against CSRF, wrong-user use,
  self-approval, forgery, and replay.
- [ ] Reconcile the 22 legacy pending proposals. Re-propose still-valid work
  against current state; expire the rest. Do not grandfather old approvals.
- [ ] Wire accepted approvals to protected `fs.*` and every mutating
  `ha.config.*` action, with precondition revalidation immediately before apply.
- [ ] Record domain-appropriate preimages and expose explicit recovery actions.
  Do not promise rollback for irreversible real-world service effects.
- [ ] Detect and bind the installed HA Core version before accepting or applying
  an HA config proposal; invalidate approval if the version changes.
- [ ] Require version-matched docs, relevant breaking-change evidence, config or
  domain validation, and expected prior-state checks before enabling writes.
- [ ] Make approval consumption and mutation replay safe across timeout,
  disconnect, redelivery, crash, and concurrent submit before enabling writes.
- [ ] Define the durable commit/reconciliation boundary for the case where HA
  accepts a side effect and the node dies before recording `applied`. Inject a
  crash at every state transition and prove restart cannot execute the effect
  twice or lose the final operation outcome.
- [ ] Ratify Tier B add-on lifecycle authorization as trusted principal context
  plus node-enforced slug allow/deny and effect policy; remove the unwired plugin
  token claim. Node pairing authenticates the device connection, not the human
  authorizing a lifecycle mutation. Dedicated and generic service paths must
  enforce the same decision.

**Exit:** direct, Assist, alias, replay, spoofing, and generic-service paths
cannot bypass policy; Rob can see and resolve a pending approval end to end.

### Phase 3: Finish command behavior and resilience

- [ ] Add supported `limit`, filter, pagination, or byte/range bounds to every
  advertised collection/read that needs them; explicitly reject unsupported
  bounds rather than ignoring them.
- [ ] Distinguish unknown entities from valid empty history where HA permits it.
- [ ] Make `ha.addon_update` and `ha.update_install` advertisement, gateway
  permission, runtime capability, authorization, and plugin exposure agree.
- [ ] Give app/Core update operations a durable operation ID and receipt that
  survives the process being terminated by its own update. Reconcile exact
  installed version and artifact digest after reconnect; never repeat the effect
  solely because the response was lost.
- [ ] Decide and implement the supported shell path:
  - [ ] prove an end-to-end Gateway/node execution and approval protocol if shell
    remains in scope; or
  - [ ] explicitly de-scope shell and remove `system.run` from every advertised
    surface.
- [ ] Do not rename `system.run` merely to evade the Gateway's reserved-command
  safety boundary.
- [ ] Repair pending-invoke acknowledgement so interleaved frames are not dropped
  and reconnect/replay cannot execute a mutation twice.
- [ ] Add HA API feature/version detection and explicit unsupported responses for
  endpoints absent in a supported HA version.
- [ ] Advertise HA Core version and refresh it after updates; Phase 2 must already
  detect and bind it for write authorization.
- [ ] Implement `docs.lookup(topic, version=current)` with bounded, integrity-
  checked cache behavior.
- [ ] Implement `docs.breaking_changes(version=current, since=?, domain=?)`.
- [ ] Keep version-matched proposal evidence and pre-apply validation enforced
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
  reconnects, redeliveries, truncations, proposal age, and approval outcomes.

**Exit:** every supported functional path has success, refusal, failure,
reconnect, and recovery evidence.

### Phase 4: Complete Assist and human-facing UX

- [ ] Finish the add-on ingress configuration and proposal UI without creating a
  separate approval application.
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
| [#257](https://github.com/clawd-ops/openclaw-hass-node/issues/257) | 1 | Part of strict schemas and bounded reads; credential exposure raises priority. |
| [#258](https://github.com/clawd-ops/openclaw-hass-node/issues/258) | 3 | Decide supported shell route; do not evade the reserved command. |
| [#259](https://github.com/clawd-ops/openclaw-hass-node/issues/259) | 1 | Part of filtering and strict schemas. |
| [#260](https://github.com/clawd-ops/openclaw-hass-node/issues/260) | 1, 3 | Generate advertisement from contract, then live-test update path. |
| [#261](https://github.com/clawd-ops/openclaw-hass-node/issues/261) | 1, 6 | Fix skill/docs contract and verify intended file-transfer boundary. |
| [#262](https://github.com/clawd-ops/openclaw-hass-node/issues/262) | 2 | Ratify one lifecycle policy and remove contradictory token claims. |
| TODO 7 | 5 | Issue triage automation. |
| TODO 11 | Closed | Home Assistant MCP retirement is complete; residual caller-policy work belongs to phase 2 and does not reopen it. |
| TODO 12 | 5 | Generated documentation. |
| TODO 13 | 5 | Proactive GitHub events; includes external Gateway dependency. |
| TODO 17 | 0 | Replace stale issue list with the tracker crosswalk. |
| TODO 20 | 2 | Real approval lifecycle and UI. |
| TODO 21 | 5 | HACS branding. |
| TODO 22 | 5 | GHCR publishing and HACS index. |
| TODO 23 | 3 | Version-rooted docs and breaking-change checks. |
| TODO 27 | 2, 4 | Shared ingress configuration and approval UI. |
| TODO 32 | 4 | Tool-progress option. |
| TODO 35 | 4, 6 | Real-client tool-progress verification. |
| TODO 36 | 1, 3, 6 | Script migration command gaps and live install verification. |
| TODO 37 | 5 | Plugin build and publish. |
| TODO 38 | 4 | Durable transcript/resume UI. |

## Evidence artifacts to keep in the repo

- `docs/VERIFICATION-2026-09-11.md`: discovery evidence at the baseline above.
- This roadmap: dependency order and completion checklist.
- Generated command/action coverage ledger: one row per supported caller path.
- Compatibility matrix: tested app/plugin/HACS/Gateway/HA/Supervisor versions and
  architectures.
- UAT result packet per release candidate: exact artifact digests, environment,
  tests, expected refusals, observed postconditions, and unresolved rows.
- Proposal threat model and protocol: trusted fields, signature/capability format,
  state machine, expiry, replay prevention, crash recovery, and audit retention.

## Cross-model review rule

Roadmap and implementation reviews deliberately use different model families.
One model builds the design; another searches for omissions, unsafe assumptions,
stale evidence, and tests that can pass without proving the claimed outcome.
Disagreements are resolved against code, tests, live behavior, and explicit user
decisions. Neither model's confidence is evidence.
