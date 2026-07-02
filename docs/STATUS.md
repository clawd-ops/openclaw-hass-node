# Status

> **Beta.** Pair, connect, tool invokes, and HA Assist conversation
> all work end-to-end on the current beta track. Publishing
> infrastructure is still settling and pre-1.0 breaking changes are
> still possible.

> **Update this file at every meaningful state change.** It is the
> single thing that tells a future maintainer "where am I". If
> `docs/design/PLAN.md` and `STATUS.md` disagree, fix whichever is wrong before
> continuing.

## Where we are

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
- **42 commands** registered in the dispatcher:
  - `ha.*` (28): list/get states, call service, list areas/devices/
    services/entity-registry, config, events, config entries, core logs,
    calendar events, logbook, history, reload config,
    light turn on/off, list automations, check config, and the
    Tier A read-only addon surface (`list_addons`, `addon_info`,
    `addon_stats`, `addon_logs`, `addon_changelog`,
    `addon_documentation`) plus Tier B addon lifecycle
    (`addon_start`, `addon_stop`, `addon_restart`) behind
    `OPENCLAW_ADMIN_TOKEN` and an explicit slug allowlist.
  - `fs.*` (11): read/list/stat/glob, write/restore/history/diff,
    move/delete, patch.
  - `system.*` (2): `system.run` (admin-token-gated), `system.which`
    (basename-only lookup).
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

- **Writes are `PROPOSAL_REQUIRED`** today; the agent-bridge UI round-trip is not wired. See TODO item #20.
- **HACS brand icon** is the default; upstream PR pending. TODO #21.
- **GHCR per-arch image / HACS index entry** not published yet; Supervisor builds locally on-device. TODO #22.
- **MCP cutover** still in flight. TODO #11.

Release-cut itself is automated: `.github/workflows/release-on-version-bump.yml` tags + cuts the GitHub release on every `main` version bump — see [`operations/RELEASE.md`](operations/RELEASE.md).

## Discoverability / sponsorship

- **Funding links.** `.github/FUNDING.yml` and README both live.
  BMC (`buymeacoffee.com/roblandry`) is active.
- **Stars badge.** Added (shields.io social-style badge pointing at
  `/stargazers`).
- **Other badges to consider once published:** HACS default badge
  (after HACS index PR lands), CI status, release version, license.

## Open blockers

None. The pipeline is live; remaining work is incremental.

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
  thin `custom_components/openclaw_hass_node_assist/` HACS integration. Plan A
  (add-on (app) alone) confirmed not viable; see
  `docs/research/CONVERSATION-AGENT.md`. (Clawd)
- 2026-06-05 — Proposals are gateway-brokered. Node speaks only the
  gateway WS protocol; does not connect to agent-bridge directly. See
  `docs/research/AGENT-BRIDGE-CONNECTIVITY.md`. (Clawd)
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
  (Rob)
- 2026-06-05 — Versioning: date-based `YYYY.M.PATCH` matching the HA
  release the node is tested against (e.g. `2026.6.0`). Patch
  increments for fixes within a HA release. (Clawd recommendation,
  Rob "ok either way")
- 2026-06-08 — Conversation relay runs on two parallel gateway WS
  connections (`role: node` for invokes, `role: operator` for chat),
  not a single node-role connection. Gateway role policy is binary
  per-method; `chat.send` is operator-scope. Device paired as
  dual-role via the `openclaw qr` bootstrap-token flow. (Clawd, after
  the single-connection ChatRelay failed verification.)
