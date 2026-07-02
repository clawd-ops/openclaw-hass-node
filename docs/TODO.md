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

## Open items

### 7. Issue triage automation
- Status: OPEN (design)
- Read-only triage first; write actions (label, comment, close) behind allowlist. Hard stop before close/merge without Rob's explicit approval.
- Shares ingress with item 13 (github-bridge).

### 11. Sunset HA MCP → node-tool path with software-blocked read-only guards
- Status: IN PROGRESS — Tier A done; Tier B shipped in #165 and registered in `commands/dispatcher.py`; subagent-side enforcement still open. **Tier policy + cadence: see `docs/design/COMMAND-TIERS.md`.**
- Tier A read-only commands shipped (PRs #132 / #134 / #137): `ha.addon_logs`, `ha.list_addons`, `ha.addon_info`, `ha.addon_stats`, `ha.addon_changelog`, `ha.addon_documentation`. Working end-to-end on b6.
- 2026-06-28 overnight cutover progress: live OpenClaw config removed `mcp.servers.homeassistant` and `mcp.servers.homeassistant-readonly` after verifying `nodes.invoke` against the connected `hass` node with `ha.get_state`. Workspace AGENTS instructions now tell Clawd/HomeOps/PoolMaster/ReefMaster to use the `hass` node command surface instead of `mcp__homeassistant*`. Existing already-running sessions may still hold old MCP child processes until they exit; fresh-session validation remains required before closing.
- **Assist-side enforcement (DONE):** the `openclaw-hass-node-assist-tools` plugin provides gateway-plugin-level enforcement for Assist contexts — `nodes.invoke` is not exposed in Assist turns, so Assist HA operations must go through the plugin's `ha_*` wrappers. This is Assist-side / gateway-plugin enforcement, NOT subagent-side enforcement.
- **Subagent-side enforcement (OPEN):** background subagents that are not spawned from an Assist turn do have `nodes.invoke` in principle, but the node dispatcher has no mechanism to distinguish subagent callers from main-session callers. The required work is: pass caller/session context into the dispatcher envelope so the node can apply the Tier A read-only allowlist specifically to background subagent sessions. Until this lands, the restriction is prompt-instructed only (SKILL.md guidance), not software-blocked.
- Remaining (in order):
  1. **Subagent-side allowlist enforcement at the node** (`commands/dispatcher.py` or new policy layer) — needs caller/session context in the invoke envelope. MUST land before any subagent path can be considered software-blocked read-only.
  2. **Wire the subagent path** to use the Tier A surface instead of `mcp__homeassistant__*` (Tier A command-surface operating guidance is already covered by item #34 / the shipped SKILL.md).
  3. **Tier B** lifecycle (`addon_start`/`stop`/`restart`) gated by the pairing-session bearer plus per-slug allow/deny (deny `homeassistant`, `supervisor`, `core_*`) + audit log. Implemented in current PR; verify in release before closing.
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
- #199 — Active-chat tool-usage progress visibility gap (UX, carries forward from #35).
- #200 — Tool-start delta not reaching HA Assist active-chat view (investigation/fix).
- #201 — `ha_*` wrappers do not solve filesystem / file-transfer confusion in Assist (open, do NOT close — see #35 note).
- #202 — `nodes.invoke` filtered in Assist by design; fix is enabling `openclaw-hass-node-assist-tools` plugin (CLOSED in this PR — see SKILL.md update).

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

### 27. Ingress configuration UI for the add-on
- Status: OPEN (design) — captured 2026-06-28 from operator UX feedback.
- Goal: serve a small web UI from the add-on over HA Ingress that renders user-friendly editors for the trickier config shapes:
  - HA-user dropdowns for `identity.super_admins` and `identity.user_agent_map.*.ha_user_id`, populated live from `config/auth/list`.
  - Allowlist pickers for `identity.forbidden_commands` and `addon_lifecycle.allowlist` / `denylist`, populated from the live command catalog and installed-addon list.
  - Inline help / tooltips so the operator doesn't have to know what `addon_lifecycle` means before configuring it.
- Replaces hand-edited YAML for the shapes HA's option-schema validators cannot express (no native dynamic enums for users or addon slugs).
- Cross-link: promotes the footnote on closed #26 ("ingress management UI remains possible as a separate future feature") to a real open item. If this ships, the startup `config/auth/list` resolution stays as a safety net but the dropdown becomes the canonical input path.
- Cross-link: shares the ingress surface with #20 (agent-bridge proposal review pane) — consider one ingress app with both panels rather than two.

### 32. Config option to show/hide tool usage in HA Assist
- Status: OPEN
- Requested: 2026-06-28 by Rob.
- Goal: addon option `show_tool_progress: true|false` (default `true`). When `false`, the relay skips emitting `🔧 Calling X...` deltas entirely (both the legacy immediate path and the `ToolProgressFrame` cap path). Lets users who want a quieter HA Assist UX suppress the per-tool progress lines without needing a different cap.
- Scope: `app/config.yaml` schema entry + relay check in `handle_event` before the queue push. Default `true` preserves current behavior.

### 33. Quiet `[relay-diag]` INFO noise in addon logs
- Status: CLOSED 2026-06-28 — moved `[relay-diag]` from INFO to DEBUG in PR #214 (shipped in v2026.7.1b1). TODO.md status lagged; corrected in 2026.7.1b2 release PR.
- Requested: 2026-06-28 by Rob.
- Goal: drop the per-event `[relay-diag]` log line in `handle_event` from `INFO` to `DEBUG`. The diagnostic was added during the stale-trailer race debugging and is no longer needed at INFO level; it floods the addon log on every gateway event.
- Scope: one-line change in `chat_relay.py` — `_LOG.info("[relay-diag] ...")` → `_LOG.debug(...)`.

### 34. Agent skill for HA node command-surface usage
- Status: CLOSED 2026-06-28 — repo skill merged in PR #194 and Skill Workshop proposal applied.
- Requested: 2026-06-28 by Rob.
- Goal: create/apply a reusable agent skill that teaches Clawd/Codex/subagents the HA node command catalog, the MCP-to-node replacements, and the Tier A/Tier B subagent safety boundary.
- Why: there are enough commands now that relying on session memory causes regressions; agents need durable guidance so MCP sunset work keeps moving across compactions and subagent handoffs.
- Final skill: `openclaw-hass-node-skill`; repo mirror lives at `skills/openclaw-hass-node-skill/SKILL.md`.
- Cross-link: this supports item #11; it does not by itself retire the MCPs. The implementation still needs subagent-side allowlist enforcement and subagent wiring to the node Tier A surface.

### 35. HA Assist active-chat tool usage still not visible
- Status: MOSTLY SUPERSEDED — the `tool_progress` frame path and per-tool-start delta logic were shipped in b3/b4 (PRs #179, #184, #186, #188, #190). The transport-level plumbing is in place.
- Remaining UX gap: the tool-start delta may still not render visibly in HA Assist's active-chat view for all turn types. Tracked in GitHub issues #199 (progress visibility gap) and #200 (tool-start delta not reaching active-chat view).
- Live-validation note: this item stays open until a real HA Assist turn with ≥1 tool call is confirmed to show `🔧 Calling X...` in the active-chat view on the currently installed version. Do not close based on code analysis alone — the prior attempts all had code in place but did not verify end-to-end.
- Cross-link: item #32 (show/hide config) must wait until this works; hiding broken output is not useful.

### 36. Node command gaps discovered while migrating workspace HA scripts
- Status: IMPLEMENTED-PENDING-LIVE-VALIDATION — keep OPEN until the live install/re-pair/allowCommands gate passes.
- Reported: 2026-06-28 during MCP sunset / workspace script migration.
- Context: workspace scripts were moved off direct `HASS_URL` / `HASS_TOKEN` where the node command surface already supports the required read path. Calendar/event and deeper HomeOps details now report explicit gaps instead of using the old token path.
- Implemented command coverage:
  1. `calendar.get_events` equivalent with `return_response` semantics, so `calendar-hass-status.mjs` and `calendar-conflict-check.mjs` can fetch event lists through the node.
  2. `/api/config` equivalent, so `homeassistant-ops-status.mjs` can report HA version/location/components through the node.
  3. `/api/events` equivalent, so HomeOps status can report event-bus listener counts through the node.
  4. `/api/config/config_entries/entry` equivalent, so HomeOps status can report unhealthy config entries through the node.
  5. Home Assistant core log read equivalent, so HomeOps status can restore `core_log_attention` without direct REST.
- Local validation: handlers, dispatcher registration, docs, and unit coverage are in place; workspace scripts now run through the HA node helper instead of `HASS_TOKEN`.
- Remaining live gate: publish/install the updated node, restart/re-pair after `gateway.nodes.allowCommands` includes the new commands, then run the affected workspace scripts through `openclaw nodes invoke` against the connected node. Do NOT close this item based on local validation alone — live install + re-pair + allowCommands inclusion must be confirmed.
- Acceptance: the affected workspace scripts run through `openclaw nodes invoke` / the HA node command surface by default, with no `HASS_TOKEN` path required for normal operation and no allowlist rejection for the new commands.

### 37. Plugin packaging — `openclaw-hass-node-assist-tools` needs a build step + npm publish
- Status: OPEN — discovered during v2026.7.1b1 rollout (2026-07-01).
- Root cause: the plugin ships a TypeScript entry point with no compiled `dist/` directory. The pnpm workspace also hoists `node_modules` with symlinks that point outside the plugin tree.
- Symptoms:
  - `openclaw plugins install <path>` fails — the gateway expects compiled JS, not `.ts` source.
  - `openclaw plugins install --link <repo>/plugins/openclaw-hass-node-assist-tools` fails the safety scan — the scanner rejects symlinks that resolve outside the plugin root (pnpm-hoisted deps hit this).
- Workaround in use (see `docs/operations/LESSONS.md` — "Plugin packaging gap"): copy the plugin to a stable path outside the pnpm workspace, run `npm install` there for self-contained (non-symlinked) deps, then `openclaw plugins install --link <stable-path>`.
- Required fix: add a `build` script to the plugin's `package.json` (`tsc --outDir dist`) and run it in CI / the release workflow so the plugin ships with a compiled `dist/`. Longer term, publish to npm or GHCR so `openclaw plugins install <package-name>` works without a local clone at all.
- Acceptance: `openclaw plugins install --link plugins/openclaw-hass-node-assist-tools` from a fresh pnpm-installed repo clone succeeds (requires `dist/` present AND no symlinks escaping the plugin root), confirmed by `openclaw plugins inspect openclaw-hass-node-assist-tools --runtime` exiting 0.

---

## Closed items

### 19. Auto-generated changelog (preferred direction per Rob, 2026-06-27)
- Status: CLOSED — shipped in PR #224.
- `scripts/generate-release-notes.sh` groups Conventional Commit subjects since
  the previous tag into Features / Fixes / Refactor / Performance / Docs / Other,
  skipping `chore(release):` version-bump commits.
- Workflow (`release-on-version-bump.yml`) now:
  - Finds the previous tag automatically.
  - Runs the script to generate grouped notes.
  - If a hand-written `app/CHANGELOG.md` section exists for the version: uses it
    as the primary body and appends the auto-derived commit list as a secondary
    "Commits since X" block.
  - If no hand-written section exists: uses auto-generated list as the sole body
    AND commits a new section to `app/CHANGELOG.md` so HA Supervisor Changelog
    tab always has content. (`app/CHANGELOG.md` is excluded from the trigger
    `paths:`, so the commit-back does not cause a recursive re-run.)
- Hand-written `app/CHANGELOG.md` sections are preserved and take precedence;
  the auto-list augments rather than replaces them. No separate release PR needed.

### 29. Multi-tool labeling in HA Assist slow-turn progress (v2 of #2)
- Status: CLOSED 2026-06-28 — shipped in PR #179.
- Additive `tool_progress` NDJSON frames (`phase=start|end`, `name`, optional `id`, monotonic `seq`). Capability-negotiated per-request via `client_caps: ["tool-progress-frames"]` body field from the HACS integration. Addon emits `ToolProgressFrame` sentinels; HTTP API serialises them; integration swallows and `_LOGGER.debug`s them (ChatLog has no ephemeral hook today). Race fix: `phase=end` gated on `id`-match (when present) or `name`-match plus monotonic `seq` guard; an id-less `end` cannot clear an active tool that has an id. Legacy textual `🔧 Calling X...` delta suppressed when cap is active (no dual emit). Tests cover: cap on vs off, sequential tool calls in one turn, end-before-newer-start race, id-aware clearing.

### 1. User mapping / identity propagation
- Status: CLOSED 2026-06-28 — addon side shipped via PRs #164–#167 + #177; design captured in [`docs/design/IDENTITY-AND-SCOPES.md`](design/IDENTITY-AND-SCOPES.md).
- Three-role model (`user`/`admin`/`super_admin`); HA `is_admin` drives auto-mapping for `user` and `admin`; `super_admin` is an explicit opt-in list in addon options. Shim forwards `actor`, addon resolves role, prepends a hardened per-turn authorization disclaimer, and optionally sends `agentId` from `identity.user_agent_map` / `default_agent_id`.
- Out of scope for this repo (closing cleanly): hard concern-A enforcement comes from gateway-side agent inventories; hard concern-B invoke enforcement needs a future gateway invoke envelope that carries session/actor context. Track gateway-side work in the gateway repo, not here.

### 28. Documentation tab intro / glossary (prelude to per-option detail)
- Status: CLOSED 2026-06-28 — `app/DOCS.md` (shipped in PR #177) covers the substance.
- Has: orientation paragraph, Quick start, per-option detail with purpose/example/default/security on every option, and a dedicated **Authorization model for the HA control surface** section that documents Tier A / Tier B and the explicit "There is no separate operator admin token for Tier B" line.
- Residual polish (not a blocker, not opening a separate item preemptively): the three-token model is described inside each token's own option section rather than as one up-front glossary block. If a future operator still trips on the token-vs-token question, lift those three explanations into one prelude block then.

### 30. GitHub Releases tab missing entries since b9
- Status: CLOSED 2026-06-28 — not a real gap.
- Re-checked 2026-06-28: GitHub Releases tab now shows all 8 releases matching all 8 tags (`v2026.6.20b4` through `v2026.6.20b11`); each Release entry exists with prerelease flag, title, and notes. The earlier observation was a tag-name string-sort artifact and/or browser cache — `b9` rendered later than `b10/b11` in the list view, making it look like the most recent.
- The b9 prerelease cap from PR #177 will roll the date forward instead of going to `b10+` going forward, which sidesteps the string-sort confusion for future releases.
- No workflow regression to fix.

### 31. Add-on icon stopped rendering in HA after the doc / schema reshape
- Status: CLOSED 2026-06-28 — resolved per Rob's observation on the b11 install (likely a Supervisor cache refresh after the b10/b11 manifest re-parse).
- No repo-side change required. If the icon disappears again after a future schema change, re-open and trace `ha apps | grep -i openclaw` `logo:` against the manifest.

### 26. Identity user options accept HA usernames
- Status: CLOSED in PR #177.
- `identity.super_admins` and `identity.user_agent_map` are configured
  with HA usernames. On startup, the add-on calls HA WebSocket
  `config/auth/list`, resolves names to HA user IDs, and keeps only the
  resolved IDs in the in-memory policy used by signed Assist actor
  checks and per-user agent routing.
- Unknown usernames log a warning and are ignored for that run, so a
  typo fails closed to the lower `admin` or `user` role without
  blocking add-on startup; unresolved route mappings fall back to the
  default agent route.
- Out of scope: native HA Configuration-tab user dropdowns. The add-on
  schema has no dynamic user selector; an ingress management UI remains
  possible as a separate future feature if needed.

### 2. Real per-tool progress events
- Status: CLOSED 2026-06-20 (verified end-to-end on b6)
- PRs #143 (tool capture + relay branch) → #147 (forward `agent` events through the WS dispatch filter) → #149 sync → b6 release. Rob's screenshot confirmed `🔧 Calling Bash...` mid-stream on a tool-heavy HA Assist turn.
- Out of scope this round: multi-tool turns only label the first visible delta. Considered for a v2 with proper ephemeral status frames + HACS integration change.
- Cross-link: item 8 partially addressed — when the model fakes a wait without a tool call, the user now sees the generic `Working on it...` instead of a tool name, which is a visible tell. Root-cause fix is still prompt-side.

### 3. Strip "alpha" wording everywhere
- Status: CLOSED 2026-06-20
- Remaining grep hits are only historical-track explanations (`docs/operations/RELEASE.md`, `docs/design/PLAN.md`, `app/CHANGELOG.md`) and an unrelated `base64url alphabet` comment. HACS title is `OpenClaw HA Node — Assist`. User-facing surfaces are clean.

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
- PR #150 (sweep, 17 files), #151 (command-count off-by-one fix), #152 (post-review follow-up: handoff status + mapped roots), #153 (app/config.yaml description), #154 (LESSONS user-facing surface inventory). Phase IDs and PR numbers stripped from user-facing prose. STATUS.md rewritten. RELEASE.md gained the manual procedure (later automated by PR #156). HACS title aligned with the integration manifest (PR #155).

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

### 15. Q1 — HACS integration default hostname hash
- Status: CLOSED 2026-06-20 (mooted by PR #94)
- PR #94 (merged 2026-06-08) added `_supervisor_addon_hostname` in `custom_components/openclaw_hass_node_assist/config_flow.py:50-90` — the integration asks HA's `hassio` integration for the addon's real hostname at config-flow time. The hardcoded `a0d7b954-openclaw-hass-node` in `const.py:14` is now only a last-resort fallback, not the canonical path. Worst case the user edits the URL once during config flow.

### 16. Q2 — `pairing_token` addon option: remove or keep
- Status: CLOSED 2026-06-20 (keep as-is)
- After PR #93 accepted the `openclaw qr` setup-code envelope as a valid `pairing_token` value, the option is the canonical on-boarding path: the user pastes the setup code into the addon Configuration UI on first install, the node normalises it (`app/node/src/openclaw_node/config.py:24-56`), pairs, and the gateway issues a real device token. No alternative bootstrap UI exists, so removing it would block first-install. Keep.

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
