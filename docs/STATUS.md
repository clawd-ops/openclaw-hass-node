# Status

> **Beta.** Pairing, connection, selected tool invokes, and HA Assist
> conversation have end-to-end evidence on the beta track. The full command
> surface is not yet proven and includes known unreachable or broken paths.
> Publishing infrastructure is still settling and pre-1.0 breaking changes are
> still possible.

> **Reality notice:** the historical release narrative below still contains
> known-stale versions, availability, and policy claims. Until Phase 6
> reconciliation is complete, use the
> [completion roadmap](COMPLETION-ROADMAP.md),
> [dated verification](VERIFICATION-2026-09-11.md), and generated
> [command coverage ledger](reference/COMMAND-COVERAGE.md) for current claims.

> **Update this file at every meaningful state change.** It is the
> single thing that tells a future maintainer "where am I". If
> `docs/design/PLAN.md` and `STATUS.md` disagree, fix whichever is wrong before
> continuing.

## Phase 0 containment (unreleased source change)

All 19 mutating actions across the nine `ha.config.*` commands now deny
unverified proposal identifiers before any HA request. The shared boundary has
no caller-controlled override. Read-only config actions and light control are
unchanged. API-adapter tests explicitly stub the boundary to retain dormant
adapter coverage; the independent boundary suite uses real authorization code
and asserts zero HA requests through both handlers and dispatcher.

This is containment only, not a working approval flow or a deployed fix.
See [the completion roadmap](COMPLETION-ROADMAP.md) for the remaining work.
Older release and non-config claims below still await the wider reconciliation.

## Where we are

**Additional unreleased coverage foundation (#268):** a deterministic generator
now reconciles 53 dispatcher commands, 51 node advertisements, 30 Assist wrapper
registrations, and 31 action variants into 84 explicit rows. The Assist
registration contract is executable by the plugin and records each tool,
descriptor, factory, node command, accepted tool key, and emitted node-key
mapping. Machine-readable and human-readable artifacts separate evidence method
from behavioral outcome, preserve multiple caller observations, distinguish
curated behavioral-test IDs from source mentions, and render accepted source
keys, aliases, defaults/bounds, field provenance, semantic/error notes,
authorization class, capability conditions, and explicit unavailable reasons.
The check fails on missing or stale command/action/caller coverage, source/action
parameter drift, unacknowledged Assist mapping drift, and stale generated
artifacts. The lifecycle `admin_token` mismatch is resolved in the current
unreleased source: lifecycle wrappers require `allowAdminOps` and the node's
slug policy without injecting another token. The separate
`ha.reload_config` domain mismatch remains tracked; this inventory does not
enable commands or resolve the other defects it records.

**Additional unreleased source repair (#266):** generic service calls normalize
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

Currently on **2026.6.20b7** in the shipped release; `main` is
`Unreleased → 2026.6.20b8` carrying the merged identity-routing hardening
(PR #167) and this docs-reconciliation pass. The node ships:

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
- **56 commands** registered in the dispatcher:
  - `ha.*` (39): list/get states, call service, list areas/devices/
    services/entity-registry, config, events, config entries, core logs,
    calendar events, logbook, history, reload config,
    light turn on/off, list automations, check config, the
    Tier A read-only addon surface (`list_addons`, `addon_info`,
    `addon_stats`, `addon_logs`, `addon_changelog`,
    `addon_documentation`), Tier B addon lifecycle
    (`addon_start`, `addon_stop`, `addon_restart`, `addon_update`) authenticated
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
  - `system.*` (5): `system.run` (currently unreachable, see
    [Authorization model](design/AUTHORIZATION-MODEL.md)), `system.which`
    (basename-only lookup), and the native exec-approval protocol methods
    delivered by #274: `system.run.prepare`, `system.execApprovals.get`,
    and `system.execApprovals.set`. `system.run` itself is not yet re-gated
    onto that contract; that is tracked as #258.
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
  `PROPOSAL_REQUIRED` in this source revision; the trusted approval verifier and
  agent-bridge UI round-trip are not wired. See TODO item #20.
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
[completion roadmap](COMPLETION-ROADMAP.md). The product is not complete: trusted
approval, node-enforced effect policy, strict parameter contracts, bounded
responses, recovery isolation, packaging, live compatibility evidence, and
release/UAT gates remain open.

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
- 2026-06-05 — `.storage/` is read-only to the node. The implemented
  command layer refuses writes unconditionally; no caller parameter or
  proposal overrides it. HARD rule. (Rob, issue #1 round 2; reconciled to
  implementation 2026-09-11)
- 2026-06-05 — Every HA config proposal must verify against the
  running version's breaking changes and include a functional fix
  when impacted. Cross-validated by Codex reviewer. (Rob, issue #1
  round 2)
- 2026-06-05 — Assist conversation agent: ship as add-on (app) **plus**
  thin `custom_components/openclaw_hass_node_assist/` HACS integration. Plan A
  (add-on (app) alone) confirmed not viable; see
  `docs/research/CONVERSATION-AGENT.md`. (agent)
- 2026-06-05 — Proposals are gateway-brokered. Node speaks only the
  gateway WS protocol; does not connect to agent-bridge directly. See
  `docs/research/AGENT-BRIDGE-CONNECTIVITY.md`. (agent)
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
