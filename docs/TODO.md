# TODO — openclaw-hass-node + HA Assist (operational punch list)

Single source of truth. Edit this file to update task state; do not maintain parallel todo lists.

First written: 2026-06-20.

Supersedes (historical reference only; the in-repo handoff files were
deleted in PR #150, workspace-side originals remain on disk):
- `docs/HANDOFF-2026-06-20-streaming-followups.md` (deleted)
- `docs/HANDOFF-2026-06-20-addon-command-surface.md` (deleted)
- `docs/QUESTIONS-FOR-ROB.md` (deleted in Phase 2 doc reshape; Q1/Q2 carried below)
- `~/.openclaw/workspace/handoffs/2026-06-20-ha-assist-followups.md`
- `~/.openclaw/workspace/handoffs/2026-06-20-MASTER-todo.md` (this file's original location)

`docs/STATUS.md` and `docs/design/PLAN.md` remain as architecture/release docs; this file is the operational punch list.

Item numbers are stable identifiers (PR descriptions reference them); they are not a priority order. Open items are listed first, then closed items.

---

## Recently done (this session, ascending PR order)

- #129 — `fix(node)`: Assist follow-up turns now stream correctly (closes #128).
- #130 — release: 2026.6.19b2 (slow-turn progress for HA Assist streaming).
- #131 — docs: HA Assist streaming + node follow-up punch list.
- #132 — `feat`: expose addon logs through the node (`ha.addon_logs`).
- #134 — `feat(node)`: Tier-A addon commands (`addon_info`/`addon_stats`/`addon_changelog`/`addon_documentation`).
- #135 — docs: "with great power" agent-blast-radius disclaimer.
- #136 — `test(http_api)`: stream preconditions + catch-all, restore 95% coverage gate (closes #127).
- #137 — release: 2026.6.20b3 (Tier A read-only addon command surface).
- #138 — `fix(addon)`: `hassio_role: manager` + pairing retry-after.
- #139 — docs: canonical `docs/TODO.md` consolidating the punch list.
- #140 — release: 2026.6.20b4 (Tier A Supervisor access fix + pairing retry-after).
- #141 — docs: close TODO #3/#14/#18 + capture `paired.json` cache lesson.
- #142 — docs: close TODO #4/#9/#10 with gateway-side root-cause notes.
- #143 — `feat(node)`: name the actual tool in HA Assist slow-turn progress.
- #144 — docs: close TODO #5/#15/#16 + capture GPT-5.5 verification workflow.
- #145 — release: 2026.6.20b5 (tool-named slow-turn progress).
- #146 — docs: capture release-PR-vs-release-cut gap in LESSONS.
- #147 — `fix(node)`: forward `agent` events to `ChatRelay` so tool progress actually fires.
- #148 — release: 2026.6.20b6 (dispatch `agent` events to `ChatRelay`).
- #149 — release: sync remaining version strings to 2026.6.20b6.
- #150 — docs: pre-1.0 cleanup sweep (phase IDs stripped, versions bumped, STATUS rewritten).
- #151 — docs: command count off-by-one (35→34, 21→20 `ha.*`).
- #152 — docs: post-#150 GPT-5.5 review follow-up (handoff status + mapped roots).
- #153 — docs: strip P5.13 from user-facing addon description.
- #154 — docs: lesson — user-facing surface extends past `docs/`.
- #155 — `chore`: `scripts/bump-version.py` (one command, all five version files); drop "(Beta)" from `hacs.json`.
- #156 — `ci`: auto-cut release on version bump + Version Sync gate on PRs.
- #157 — docs: close TODO #2/#6/#8, clear Stale/Unconfirmed, capture review-scope lesson.
- #158 — `fix(ci)`: PEP 440 regex + CHANGELOG version match correctness (GPT-5.5 review catch on #156).
- #159 — docs: reorder Recently-done PRs ascending + fold in missing entries.
- #160 — docs(todo): split Open vs Closed, restore #152.
- #161 — docs: restore Tier A/B/C policy as `docs/design/COMMAND-TIERS.md`.
- #162 — docs: capture identity + scopes design (TODO #1).
- #163 — docs: rewrite `IDENTITY-AND-SCOPES` — two-gate, addon-only design (v2).
- #164 — `feat(shim)`: forward HA user identity as `actor` on `/v1/conversation/stream`.
- #165 — `feat(node)`: assist identity routing + Tier B addon lifecycle commands.
- #166 — release: 2026.6.20b7 (identity routing + Tier B).
- #167 — `fix(node)`: harden identity routing review findings (actor-HMAC derived from `local_api_token`, no new secret).

Runtime events (not PRs):

- Node re-paired in operator role (dual-WS pairing).
- `paired.json` for the live `hass` node refreshed to b4 advertise via `hassio.addon_restart`; all six Tier A addon commands verified end-to-end. Lesson captured in `docs/operations/LESSONS.md`.

---

## Open items

### 1. User mapping / identity propagation
- Status: IMPLEMENTED-IN-PR — see [`docs/design/IDENTITY-AND-SCOPES.md`](design/IDENTITY-AND-SCOPES.md)
- Captures the agreed addon-only model (2026-06-23): three roles (`user`/`admin`/`super_admin`); HA `is_admin` drives auto-mapping for `user` and `admin`; `super_admin` is an explicit opt-in list in addon options. Shim forwards `actor`, addon resolves role, prepends a hardened per-turn authorization disclaimer, and optionally sends `agentId` from `identity.user_agent_map` / `default_agent_id`.
- This is prompt-level for shared-agent setups. Hard concern-A enforcement still comes from gateway-side agent inventories; hard concern-B invoke enforcement still needs a future gateway invoke envelope that carries session/actor context.
- Highest leverage; cross-links to 7, 9, 11.

### 7. Issue triage automation
- Status: OPEN (design)
- Read-only triage first; write actions (label, comment, close) behind allowlist. Hard stop before close/merge without Rob's explicit approval.
- Shares ingress with item 13 (github-bridge).

### 11. Sunset HA MCP → node-tool path with software-blocked read-only guards
- Status: IN PROGRESS — Tier A done; Tier B shipped in #165 and registered in `commands/dispatcher.py`; subagent-side enforcement still open. **Tier policy + cadence: see `docs/design/COMMAND-TIERS.md`.**
- Tier A read-only commands shipped (PRs #132 / #134 / #137): `ha.addon_logs`, `ha.list_addons`, `ha.addon_info`, `ha.addon_stats`, `ha.addon_changelog`, `ha.addon_documentation`. Working end-to-end on b6.
- Remaining (in order):
  1. **Subagent-side allowlist enforcement at the node** (`commands/dispatcher.py` or new policy layer). MUST land before any subagent path is wired to call these commands.
  2. **Wire the subagent path** to use the Tier A surface instead of `mcp__homeassistant__*`.
  3. **Tier B** lifecycle (`addon_start`/`stop`/`restart`) admin-gated via `OPENCLAW_ADMIN_TOKEN` + per-slug allow/deny (deny `homeassistant`, `supervisor`, `core_*`) + audit log. Implemented in current PR; verify in release before closing.
- Tier C (install/uninstall/update/rebuild) explicitly NOT adding.

### 12. Generated docs site for node command surface + protocols
- Status: DEFERRED
- GitHub Pages or similar once Tier A and #11 subagent allowlist land. Scope: command catalog, tier-A/B/C policy, role/identity model, addon ↔ gateway architecture diagram.

### 13. Proactive GitHub event notifications to Clawd
- Status: OPEN (design)
- Webhook bridge GitHub → OpenClaw (likely `oc-hooks.landry.me/plugins/github-bridge`, following pocket/linear/agentmail pattern). Events: PR opened/synchronized/closed, check_suite completed, pull_request_review submitted, issues opened/labeled.
- Cross-link: item 7 shares ingress; CLW-47 github-bridge plugin in `open-loops.md` is already partly scoped but currently BLOCKED on Rob's gateway-flip approval.

### 17. Open GitHub issues (not otherwise tracked above)
- Status: OPEN
- #107 — `reset_pairing` should be one-shot: don't wipe again until the user toggles.
- #78 — Auto-bootstrap API token between node and integration (enhancement).
- #1 — Direction (catch-all, leave for Rob).

### 19. Auto-generated changelog (preferred direction per Rob, 2026-06-27)
- Status: OPEN
- Today: release workflow extracts notes from a hand-written `addon/CHANGELOG.md` section per version heading. Conventional Commits is policy on every PR so the history stays machine-readable, but nothing reads it automatically.
- Goal: extend `.github/workflows/release-on-version-bump.yml` (or a sibling step) so the release notes are derived from the Conventional Commit subjects since the previous tag, grouped by type (Features / Fixes / Refactor / Docs / etc.). Hand-written `addon/CHANGELOG.md` becomes optional embellishment rather than the source.
- Constraints: must respect prerelease semantics (a/b/rc/.dev); must still produce a useful HA-Supervisor-rendered `addon/CHANGELOG.md` (Supervisor reads this file for the addon's Changelog tab); must not require a separate `release: <version>` PR if the changelog can be generated mid-flight.
- Lands AFTER the doc cleanup + Phase 2 doc-architecture reshape, but BEFORE we'd want to take the project to 1.0.

### 20. Proposal-gated write path — agent-bridge UI wiring
- Status: OPEN — handlers return `PROPOSAL_REQUIRED` today; the actual `propose_edit` → `resolve_proposal` round-trip through the agent-bridge UI is not wired.
- Affects `fs.write`, `fs.patch`, `fs.move`, `fs.delete`, `ha.config.*`.
- Goal: a user-visible "agent wants to make this change → accept / reject" pane in agent-bridge that the node waits on before applying the write.
- This is the next major write-surface milestone.

### 21. HACS brands PR — OpenClaw icon
- Status: OPEN (external)
- Upstream HACS "brands" PR is open; while it's pending the integration shows the default HACS icon, not the OpenClaw one. Pure cosmetic; tracked so we don't forget to confirm after it merges.

### 22. Publishing — GHCR per-arch image + HACS index
- Status: OPEN
- Today: Supervisor builds locally on-device from the cloned add-on repo; HACS install works but isn't in the default HACS index.
- Goal: per-arch image published to GHCR by the release workflow (lets us put the `image:` key back in `config.yaml` and skip on-device builds), `addon` repository metadata published, HACS default-index PR submitted.
- Independent of #20; can land in parallel.

### 23. HA-version-rooted commands + breaking-change verification (PLAN §2c)
- Status: OPEN — gated on #20 (writes need to actually round-trip through agent-bridge before pre-change verification has a place to fire).
- Designed in [`design/PLAN.md`](design/PLAN.md) §2c. Three pieces:
  1. **HA core version detection on connect.** Node hits Supervisor `/info` (or `/api/config`) and emits the version as pairing metadata so the gateway-side agent always knows the live HA version of the target.
  2. **New `docs.lookup(topic, version=current)` command.** Fetches documentation from the `home-assistant/home-assistant.io` repo at the tag matching the running core version, with a local cache. Goes through `dispatcher.py` like any other command.
  3. **New `docs.breaking_changes(version=current, since=<prev>?, domain=?)` command.** Pulls the breaking-changes section of the relevant release notes from the same docs repo. Used by the HARD rule below.
- **HARD rule on the write path** (also gated on #20): before any proposal that touches HA config (yaml or API-driven), the generator must call `docs.lookup` + `docs.breaking_changes`, cite the relevant breaking-change entry in the proposal body if any, and include the functional fix (not just the original edit). Codex review re-runs `docs.breaking_changes` against the diff and blocks merge if a breaking change was missed.
- Why deferred, not killed: the discipline (version-aware proposals + cited breaking-change checks) is load-bearing for safe `/config` mutations. Cheap to defer; expensive to recreate later if we drop the design intent.

### 26. `identity.super_admins` accepts HA usernames (auto-resolve to UUID at startup)
- Status: OPEN.
- Why: `identity.super_admins` currently takes raw HA user UUIDs, which the HA UI does not expose in plain text. Documented lookup paths (URL fragment, WebSocket `auth/list`, `.storage/auth_provider.homeassistant`) work but are awkward. Rob's call (2026-06-27): do NOT add a new node command + gateway-side dropdown — keep this entirely in the addon Configuration tab. The fix is to let operators paste usernames they already know, and resolve to UUIDs internally on startup.
- Pieces:
  1. **Schema unchanged externally**: `identity.super_admins` stays `list of str`. Each entry may be either a 32-char hex UUID OR a HA username. Strings shorter than 32 chars or that don't match the hex pattern are treated as usernames.
  2. **Runtime resolution**: on startup, the addon queries HA for the username→UUID map (canonical source is the WebSocket `auth/list` admin command using `SUPERVISOR_TOKEN`; alternative is reading `/config/.storage/auth_provider.homeassistant` directly). Build a one-shot resolution and persist the resolved UUID list to the in-memory identity policy.
  3. **Fail-soft**: a username with no matching HA user logs a WARNING but does not block startup; the unresolved entry is dropped from the effective super_admins set. Surface unresolved entries in the addon's startup logs and (eventually) in a gateway-side health check.
- Out of scope (rejected): adding `ha.list_users` as a node command, or any gateway-side UI work. The addon handles resolution internally.

---

## Closed items

### 2. Real per-tool progress events
- Status: CLOSED 2026-06-20 (verified end-to-end on b6)
- PRs #143 (tool capture + relay branch) → #147 (forward `agent` events through the WS dispatch filter) → #149 sync → b6 release. Rob's screenshot confirmed `🔧 Calling Bash...` mid-stream on a tool-heavy HA Assist turn.
- Out of scope this round: multi-tool turns only label the first visible delta. Considered for a v2 with proper ephemeral status frames + HACS shim change.
- Cross-link: item 8 partially addressed — when the model fakes a wait without a tool call, the user now sees the generic `Working on it...` instead of a tool name, which is a visible tell. Root-cause fix is still prompt-side.

### 3. Strip "alpha" wording everywhere
- Status: CLOSED 2026-06-20
- Remaining grep hits are only historical-track explanations (`docs/operations/RELEASE.md`, `docs/design/PLAN.md`, `addon/CHANGELOG.md`) and an unrelated `base64url alphabet` comment. HACS title is `OpenClaw Gateway (Beta)`. User-facing surfaces are clean.

### 4. #128 / #129 turn-boundary stale-trailer race
- Status: CLOSED 2026-06-20 (streaming variant fixed; non-streaming variant accepted as structural)
- Audit: PR #129's `_seen_same_run_event` gate in `chat_relay.py:902-908` closes the streaming case the original review flagged. Verified against the b3 changelog and the live merged code; the `stale_unconfirmed_session` branch covers post-ack runId-less `session.message` for any session that has a delta queue open.
- Theoretical non-streaming variant: the same gate is intentionally NOT applied to `relay_turn` (non-streaming) consumers because the gateway's deferred-reply flow can legitimately emit a single runId-less `session.message` as the only event. Extending the gate would break that flow. Closing this cleanly requires the gateway to consistently tag `session.message` with `runId` — outside this repo's reach.
- Recommendation: do not extend the gate. If a non-streaming stale-trailer bug actually manifests in production, revisit with timing-based heuristics or wait for a gateway-side runId-tagging fix.

### 5. HA Assist not responding on Ash's device
- Status: CLOSED 2026-06-20 (per Rob)
- Streaming work shipped through b4 (b1 streaming, b2 keepalive, b3 stale-trailer race, b4 hassio_role) addresses the most likely transport-level causes. Item #1 (user identity) still tracks the per-user authz story that was the original trigger.

### 6. Doc cleanup sweep (pre-1.0 hygiene)
- Status: CLOSED 2026-06-20
- PR #150 (sweep, 17 files), #151 (command-count off-by-one fix), #152 (post-review follow-up: handoff status + mapped roots), #153 (addon/config.yaml description), #154 (LESSONS user-facing surface inventory). Phase IDs and PR numbers stripped from user-facing prose. STATUS.md rewritten. RELEASE.md gained the manual procedure (later automated by PR #156). HACS title aligned with the integration manifest (PR #155).

### 8. Prompt guard: no faked waiting/working
- Status: CLOSED 2026-06-20 (out-of-repo; partial mitigation in-repo)
- The fix is a system-prompt rule on the agent side (Clawd's prompt), not in `openclaw-hass-node`. The model emitting "Timer's running. Waiting." with no tool call is the agent runtime's responsibility — this repo is just the transport.
- Partial in-repo mitigation shipped with item #2: tool-named progress (`🔧 Calling X...`) means a faked wait is now visible as either silence or a generic placeholder rather than the same UX as a real tool turn. If a faked wait recurs, the symptom is now diagnosable from HA Assist alone.
- Action if it reproduces: file an issue against the Clawd agent system prompt, not this repo.

### 9. Cross-session subscriber bleed
- Status: CLOSED 2026-06-20 (addon-defended; gateway leak documented, not actionable from this repo)
- Root cause: gateway `handleTranscriptUpdateBroadcast` in `/app/dist/server-session-events-TsYthLSk.js:166-211` unions the broad `sessionEventSubscribers.getAll()` registry into per-session `session.message` fan-out. Cron-session output therefore reaches every connection subscribed to `sessions.changed`.
- Addon defense: `ChatRelay.handle_event` (`chat_relay.py:851`) drops any event whose `sessionKey` is not in `_reply_events` or `_delta_queues`. Wrong-sessionKey events show up in `[relay-diag]` logs but never reach HA text extraction. The "Ash seeing Rob's content" reports remain correlation, not confirmed root cause.
- Recommendation: leave addon filter as-is (defense in depth). Gateway-side fix would remove `sessionEventSubscribers.getAll()` from the message fan-out path; not pursuing.

### 10. Placeholder coerces stream to final → real answer dropped
- Status: CLOSED 2026-06-20 (gateway-side; addon band-aid rejected)
- Root cause: gateway `broadcastChatFinal` in `/app/dist/chat-BA3ikhey.js:2811/3031/3216` fires once the placeholder/short turn's `deliveredReplies` settles, before any post-toolResult assistant continuation lands. Stream-finalization pipeline lives in `/app/dist/setup.finalize-DqUrEk5p.js` + `pending-final-delivery-B7VNQKmB.js`.
- Addon band-aid considered (treat first `final` as soft, wait 1-2s for a real assistant `session.message` post-toolResult before closing the stream). Rejected: would change stream contract semantics and delay every legitimate fast turn. Plan agent explicitly recommended against.
- Recommendation: do nothing in this repo. If users hit "no follow-on response" reliably, revisit with a gateway-side change to defer `broadcastChatFinal` until pending toolResults + their assistant continuations settle for the same `runId`.

### 14. Gateway allowCommands sync for new node commands
- Status: CLOSED 2026-06-20
- All six Tier A commands (`ha.addon_logs`, `ha.list_addons`, `ha.addon_info`, `ha.addon_stats`, `ha.addon_changelog`, `ha.addon_documentation`) verified working end-to-end against the live `hass` node on 2026.6.20b4. Discovered + fixed a separate cache layer: the gateway's per-node `commands` array in `nodes/paired.json` is set at original pair time and is NOT refreshed by WS reconnect. `hassio.addon_restart` (full handshake) is what rewrites it. Recipe in `docs/operations/LESSONS.md` — "Gateway caches the node's advertised commands at pair time".

### 15. Q1 — HACS shim default hostname hash
- Status: CLOSED 2026-06-20 (mooted by PR #94)
- PR #94 (merged 2026-06-08) added `_supervisor_addon_hostname` in `custom_components/openclaw_gateway/config_flow.py:50-90` — the shim asks HA's `hassio` integration for the addon's real hostname at config-flow time. The hardcoded `a0d7b954-openclaw-hass-node` in `const.py:14` is now only a last-resort fallback, not the canonical path. Worst case the user edits the URL once during config flow.

### 16. Q2 — `pairing_token` addon option: remove or keep
- Status: CLOSED 2026-06-20 (keep as-is)
- After PR #93 accepted the `openclaw qr` setup-code envelope as a valid `pairing_token` value, the option is the canonical on-boarding path: the user pastes the setup code into the addon Configuration UI on first install, the node normalises it (`addon/node/src/openclaw_node/config.py:24-56`), pairs, and the gateway issues a real device token. No alternative bootstrap UI exists, so removing it would block first-install. Keep.

### 18. SUPERVISOR_TOKEN injection loop (`hass-node-supervisor-token` in open-loops.md)
- Status: CLOSED 2026-06-20
- PRs #110 (`hassio_api: true`) and #116/#117 (`with-contenv` shebang) shipped. Streaming-followups handoff confirmed STALE. Live verification: b4 addon log shows `[run.sh] SUPERVISOR_TOKEN injected (len=112)` and the Tier A Supervisor calls now work, which transitively proves the token is being injected. Removed from open-loops.

---

## Stale claims to strike

All cleared 2026-06-20. Kept here as a marker that they were addressed:

- ~~MEMORY.md SUPERVISOR_TOKEN-not-injected loop~~ — removed from `~/.openclaw/proactivity/open-loops.md` in PR #141.
- ~~STATUS.md "Where we are (2026-06-08 PM)" header~~ — rewritten in PR #150.
- ~~STATUS.md "Currently on 2026.6.19b1"~~ — updated to b6 in PR #150.
- ~~"Strip alpha wording" framed as broken~~ — closed under item #3.

## Unconfirmed (need outside evidence)

All four items from the original audit have since been confirmed or resolved:

- ~~Item 14: gateway `allowCommands` sync~~ — verified end-to-end after the `paired.json` cache refresh; all six Tier A commands work (LESSONS captures the cache behaviour).
- ~~Item 4: stale-trailer race~~ — closed as documented (streaming variant fixed in b3; non-streaming variant structurally accepted).
- ~~Item 5: Ash device issue~~ — closed per Rob.
- ~~Item 10: gateway stream finalization~~ — closed as gateway-side, not actionable from this repo.
