# TODO — openclaw-hass-node + HA Assist (operational punch list)

Single source of truth. Edit this file to update task state; do not maintain parallel todo lists.

First written: 2026-06-20.

Supersedes (kept on disk for provenance, do not treat as live):
- `docs/HANDOFF-2026-06-20-streaming-followups.md`
- `docs/HANDOFF-2026-06-20-addon-command-surface.md`
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
- Runtime: node re-paired in operator role (dual-WS pairing).

---

## Open items

### 1. User mapping / identity propagation
- Status: OPEN
- HA Assist → gateway → tools does not propagate which human is talking. Ash nearly social-engineered a file delete because Clawd had no identity to authz against.
- Evidence: streaming-followups handoff item 1; addon log 06:00:58 EDT cross-session bleed (see item 9).
- Highest leverage; cross-links to 5, 9, 11.

### 2. Real per-tool progress events
- Status: OPEN
- 2026.6.19b2 keep-alive only emits a generic `Working on it...` placeholder after ~8s. Need per-tool labels via a proper gateway/node event contract (which tool, what it's doing).
- Pairs with item 8 (placeholder hides model misbehavior).
- Evidence: PR #130; streaming-followups handoff item 2.

### 3. Strip "alpha" wording everywhere
- Status: IN PROGRESS (likely DONE in user-facing surfaces; verify app/UI text only)
- Repo grep: only remaining `alpha` mentions are historical track explanations in `docs/RELEASE.md`, `docs/PACKAGING.md`, `addon/CHANGELOG.md`, and a "base64url alphabet" comment in `addon/node/src/openclaw_node/config.py:42` (false positive). HACS title moved to "OpenClaw Gateway (Beta)" per CHANGELOG.
- Remaining: confirm no `alpha` strings in the HA shim UI or addon config UI fields. Then close.

### 4. #128 / #129 turn-boundary stale-trailer race
- Status: OPEN (PR #129 merged but race may persist)
- Prior review found an unclosed post-ack runId-less `session.message` leak window. Do not assume #129 closed it.
- Action: re-audit relay code paths shipped in #129 for post-ack messages without a runId; add regression test.
- Cross-link: item 10 (placeholder coerces final) likely overlaps.

### 5. HA Assist not responding on Ash's device
- Status: OPEN
- 2026.6.19b2 keep-alive may not cover it. Revisit after streaming validation; likely tied to items 1 and/or 9.

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
- Status: OPEN
- Addon log 2026-06-20 06:00:58 EDT: a `cron` sessionKey emitted `delta`+`final` with `subscribed=['agent:clawd:ha-assist:01kvj6z9...']`. Cron output routed to an ha-assist subscriber.
- Likely root of cross-user message bleed (Ash seeing Rob's content).
- Triage with item 1.

### 10. Placeholder coerces stream to final → real answer dropped
- Status: OPEN (visible bug for "no follow-on response")
- 05:58:34 EDT sequence: placeholder `session.message` → real delta → `final` → HA closes stream → toolResult arrives → real assistant message → second `final` to no listeners.
- Fix area: gateway stream-finalization must wait for all post-toolResult assistant turns to settle before emitting `final`.
- Likely overlaps with item 4.

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
- Status: UNCONFIRMED (treat as OPEN until proven)
- addon-command-surface handoff: "PR #132 merged but the allowCommands entry is NOT yet added. Tool currently unreachable from gateway." Same risk for `ha.list_addons`, `ha.addon_info`, `ha.addon_stats`, `ha.addon_changelog`, `ha.addon_documentation`.
- Action: verify `nodes.allowCommands` in the operator's private gateway config repo includes all six new commands. Add missing entries before declaring Tier A "live to callers".

### 15. Q1 — HACS shim default hostname hash
- Status: OPEN (low risk)
- `custom_components/openclaw_gateway/const.py` default URL uses `http://a0d7b954-openclaw-hass-node:8099`. Hash unverified offline.
- Action: during UAT, read actual hostname from HA → Settings → Add-ons → OpenClaw Node → Network. Update if different.
- May be partially mooted by PR #94 (shim queries Supervisor for hostname).

### 16. Q2 — `pairing_token` addon option: remove or keep
- Status: OPEN
- Recommendation in QUESTIONS-FOR-ROB.md was REMOVE for honesty. Bootstrap-token flow has since been accepted (PR #93). Decide whether to keep as-is, rename, or remove now that the path is real.

### 17. Open GitHub issues (not otherwise tracked above)
- Status: OPEN
- #107 — `reset_pairing` should be one-shot: don't wipe again until the user toggles.
- #78 — Auto-bootstrap API token between node and integration (enhancement).
- #1 — Direction (catch-all, leave for Rob).

### 18. SUPERVISOR_TOKEN injection loop (`hass-node-supervisor-token` in open-loops.md)
- Status: LIKELY DONE — verify and close
- Evidence: PRs #110 (hassio_api: true), #116/#117 (with-contenv shebang) merged; streaming-followups handoff explicitly says the SUPERVISOR_TOKEN caveat is STALE as of 2026-06-20.
- Action: confirm `ha.list_areas` succeeds end-to-end post-rebuild, then remove the loop entry.

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
