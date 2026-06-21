# TODO — openclaw-hass-node + HA Assist (operational punch list)

Single source of truth. Edit this file to update task state; do not maintain parallel todo lists.

First written: 2026-06-20.

Supersedes (historical reference only; the in-repo handoff files were
deleted in PR #150, workspace-side originals remain on disk):
- `docs/HANDOFF-2026-06-20-streaming-followups.md` (deleted)
- `docs/HANDOFF-2026-06-20-addon-command-surface.md` (deleted)
- `docs/QUESTIONS-FOR-ROB.md` (Q1/Q2 carried below)
- `~/.openclaw/workspace/handoffs/2026-06-20-ha-assist-followups.md`
- `~/.openclaw/workspace/handoffs/2026-06-20-MASTER-todo.md` (this file's original location)

`docs/STATUS.md` and `docs/PLAN.md` remain as architecture/release docs; this file is the operational punch list.

---

## Recently done (last ~7 days, verified against git log + gh pr list)

- PR #138 — `fix(addon)`: hassio_role manager + pairing retry-after surfacing. Merged 2026-06-20.
- PR #137 — release 2026.6.20b3: Tier A read-only addon command surface. Merged 2026-06-20.
- PR #136 — `test(http_api)`: cover stream preconditions + catch-all, restore 95% coverage gate (closes #127). Merged 2026-06-20.
- PR #135 — docs: "with great power" agent-blast-radius disclaimer. Merged 2026-06-20.
- PR #134 — `feat(node)`: Tier-A addon commands (info, stats, changelog, documentation). Merged 2026-06-20.
- PR #132 — `feat`: expose addon logs through the node (`ha.addon_logs`). Merged 2026-06-20.
- PR #131 — docs: HA Assist streaming + node follow-up punch list. Merged 2026-06-20.
- PR #130 — release 2026.6.19b2: slow-turn progress for HA Assist streaming. Merged 2026-06-20.
- PR #129 — `fix(node)`: Assist follow-up turns stream correctly (closes #128). Merged 2026-06-20. NOTE: streaming-followups handoff flagged a possible stale-trailer race; see open item 4.
- PR #139 — docs: canonical `docs/TODO.md` (consolidates HA Assist + node punch list). Merged 2026-06-20.
- PR #140 — release 2026.6.20b4: Tier A Supervisor access fix + pairing retry-after. Merged 2026-06-20.
- Runtime: node re-paired in operator role (dual-WS pairing).
- Runtime: `paired.json` for live `hass` node refreshed to b4 advertise via `hassio.addon_restart`; all six Tier A addon commands verified working end-to-end (closes item 14). Lesson captured in `docs/LESSONS.md`.

---

## Open items

### 1. User mapping / identity propagation
- Status: OPEN
- HA Assist → gateway → tools does not propagate which human is talking. Ash nearly social-engineered a file delete because Clawd had no identity to authz against.
- Evidence: streaming-followups handoff item 1; addon log 06:00:58 EDT cross-session bleed (see item 9).
- Highest leverage; cross-links to 5, 9, 11.

### 2. Real per-tool progress events
- Status: IN REVIEW (PR #143)
- Operator client now advertises the `tool-events` capability at handshake; ChatRelay records active tool from `agent`/`session.tool` events and uses the name in the visible 8s slow-turn delta (`🔧 Calling weather...` instead of `Working on it...`). Falls back to generic placeholder when no tool active.
- Out of scope this round: multi-tool turns only label the first visible delta. Considered for v2 with proper ephemeral status frames + HACS shim change.
- Cross-link: item 8 still open — empty waits without tool calls remain a separate prompt-side concern.

### 3. Strip "alpha" wording everywhere
- Status: CLOSED 2026-06-20
- Remaining grep hits are only historical-track explanations (`docs/RELEASE.md`, `docs/PACKAGING.md`, `addon/CHANGELOG.md`) and an unrelated `base64url alphabet` comment. HACS title is `OpenClaw Gateway (Beta)`. User-facing surfaces are clean.

### 4. #128 / #129 turn-boundary stale-trailer race
- Status: CLOSED 2026-06-20 (streaming variant fixed; non-streaming variant accepted as structural)
- Audit: PR #129's `_seen_same_run_event` gate in `chat_relay.py:902-908` closes the streaming case the original review flagged. Verified against the b3 changelog and the live merged code; the `stale_unconfirmed_session` branch covers post-ack runId-less `session.message` for any session that has a delta queue open.
- Theoretical non-streaming variant: the same gate is intentionally NOT applied to `relay_turn` (non-streaming) consumers because the gateway's deferred-reply flow can legitimately emit a single runId-less `session.message` as the only event. Extending the gate would break that flow. Closing this cleanly requires the gateway to consistently tag `session.message` with `runId` — outside this repo's reach.
- Recommendation: do not extend the gate. If a non-streaming stale-trailer bug actually manifests in production, revisit with timing-based heuristics or wait for a gateway-side runId-tagging fix.

### 5. HA Assist not responding on Ash's device
- Status: CLOSED 2026-06-20 (per Rob)
- Streaming work shipped through b4 (b1 streaming, b2 keepalive, b3 stale-trailer race, b4 hassio_role) addresses the most likely transport-level causes. Item #1 (user identity) still tracks the per-user authz story that was the original trigger.

### 6. Doc cleanup sweep (pre-1.0 hygiene)
- Status: OPEN
- README, STATUS, docs across openclaw-hass-node and sibling repos brought current to actual state before first real cut.
- STATUS.md currently still labelled "Where we are (2026-06-08 PM)" — bring forward to b3 reality (dual WS shipped, streaming live, Tier A addon surface, hassio_role fix).

### 7. Issue triage automation
- Status: OPEN (design)
- Read-only triage first; write actions (label, comment, close) behind allowlist. Hard stop before close/merge without Rob's explicit approval.
- Shares ingress with item 13 (github-bridge).

### 8. Prompt guard: no faked waiting/working
- Status: OPEN
- Finding: model emitted `"Timer's running. Waiting."` as final text, `stopReason=stop`, no tool calls, 15s turn. Need system-prompt rule: any claim of waiting/timing/working REQUIRES an active tool call.
- Evidence: `/home/openclaw/.openclaw/agents/clawd/sessions/ac3a8fd9-f5e1-4065-ba33-b255fabcddd4.jsonl` 09:52:11–09:52:26 UTC.
- Pairs with item 2.

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

### 11. Sunset HA MCP → node-tool path with software-blocked read-only guards
- Status: IN PROGRESS
- Tier A read-only commands shipped (PRs #132, #134, #137): `ha.addon_logs`, `ha.list_addons`, `ha.addon_info`, `ha.addon_stats`, `ha.addon_changelog`, `ha.addon_documentation`.
- Remaining:
  - Subagent-side allowlist enforcement at node (`commands/dispatcher.py` or new policy layer). MUST land before any subagent path is wired to call these commands.
  - Tier B lifecycle (`addon_start`/`stop`/`restart`) admin-gated via `OPENCLAW_ADMIN_TOKEN` + per-slug allow/deny (deny `homeassistant`, `supervisor`, `core_*`). Separate PR with its own allowlist surface.
  - Tier C (install/uninstall/update/rebuild) explicitly NOT adding.
- See addon-command-surface handoff for tiering rules.

### 12. Generated docs site for node command surface + protocols
- Status: DEFERRED
- GitHub Pages or similar once Tier A and #11 subagent allowlist land. Scope: command catalog, tier-A/B/C policy, role/identity model, addon ↔ gateway architecture diagram.

### 13. Proactive GitHub event notifications to Clawd
- Status: OPEN (design)
- Webhook bridge GitHub → OpenClaw (likely `oc-hooks.landry.me/plugins/github-bridge`, following pocket/linear/agentmail pattern). Events: PR opened/synchronized/closed, check_suite completed, pull_request_review submitted, issues opened/labeled.
- Cross-link: item 7 shares ingress; CLW-47 github-bridge plugin in `open-loops.md` is already partly scoped but currently BLOCKED on Rob's gateway-flip approval.

### 14. Gateway allowCommands sync for new node commands
- Status: CLOSED 2026-06-20
- All six Tier A commands (`ha.addon_logs`, `ha.list_addons`, `ha.addon_info`, `ha.addon_stats`, `ha.addon_changelog`, `ha.addon_documentation`) verified working end-to-end against the live `hass` node on 2026.6.20b4. Discovered + fixed a separate cache layer: the gateway's per-node `commands` array in `nodes/paired.json` is set at original pair time and is NOT refreshed by WS reconnect. `hassio.addon_restart` (full handshake) is what rewrites it. Recipe in `docs/LESSONS.md` — "Gateway caches the node's advertised commands at pair time".

### 15. Q1 — HACS shim default hostname hash
- Status: CLOSED 2026-06-20 (mooted by PR #94)
- PR #94 (merged 2026-06-08) added `_supervisor_addon_hostname` in `custom_components/openclaw_gateway/config_flow.py:50-90` — the shim asks HA's `hassio` integration for the addon's real hostname at config-flow time. The hardcoded `a0d7b954-openclaw-hass-node` in `const.py:14` is now only a last-resort fallback, not the canonical path. Worst case the user edits the URL once during config flow.

### 16. Q2 — `pairing_token` addon option: remove or keep
- Status: CLOSED 2026-06-20 (keep as-is)
- After PR #93 accepted the `openclaw qr` setup-code envelope as a valid `pairing_token` value, the option is the canonical on-boarding path: the user pastes the setup code into the addon Configuration UI on first install, the node normalises it (`addon/node/src/openclaw_node/config.py:24-56`), pairs, and the gateway issues a real device token. No alternative bootstrap UI exists, so removing it would block first-install. Keep.

### 17. Open GitHub issues (not otherwise tracked above)
- Status: OPEN
- #107 — `reset_pairing` should be one-shot: don't wipe again until the user toggles.
- #78 — Auto-bootstrap API token between node and integration (enhancement).
- #1 — Direction (catch-all, leave for Rob).

### 18. SUPERVISOR_TOKEN injection loop (`hass-node-supervisor-token` in open-loops.md)
- Status: CLOSED 2026-06-20
- PRs #110 (`hassio_api: true`) and #116/#117 (`with-contenv` shebang) shipped. Streaming-followups handoff confirmed STALE. Live verification: b4 addon log shows `[run.sh] SUPERVISOR_TOKEN injected (len=112)` and the Tier A Supervisor calls now work, which transitively proves the token is being injected. Removed from open-loops.

---

## Stale claims to strike

- MEMORY.md `project_hass_node_supervisor_token` / `hass-node-supervisor-token` loop wording: "SUPERVISOR_TOKEN not being injected" — STALE as of 2026-06-20. Token works via `HASS_TOKEN` env in gateway pod against `$HASS_URL/api/hassio/addons/<slug>/logs`. See item 18.
- STATUS.md header "Where we are (2026-06-08 PM)" — out of date. Dual-WS (PR #86), streaming (b1/b2/b3), Tier A addon surface, hassio_role manager all shipped after that date. Item 6 owns the rewrite.
- STATUS.md "Currently on 2026.6.19b1" — current is 2026.6.20b3 (PR #137).
- "Strip alpha wording" framed as actively broken — verified largely DONE; only app/UI surface verification remains (item 3).

---

## Unconfirmed (need outside evidence)

- Item 14: gateway `nodes.allowCommands` sync for the six Tier A commands. Operator gateway-config repo is private and not reachable from this pod. Evidence needed: `grep ha.addon_ <gateway-config-repo>/...` or operator confirmation.
- Item 4: whether #129's merged code actually closes the post-ack runId-less `session.message` window, or whether the race the prior review flagged is still live. Evidence needed: re-read of the merged relay code paths + a targeted test.
- Item 5: whether Ash's device issue is fixed by b2/b3 streaming work or still reproducible. Evidence needed: live test on her device.
- Item 10: gateway-side stream-finalization rule. Evidence needed: read of gateway streaming finalizer to confirm the wait-for-post-toolResult condition.
