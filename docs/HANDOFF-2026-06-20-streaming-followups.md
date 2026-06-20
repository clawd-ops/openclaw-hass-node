# 2026-06-20 — HA Assist / openclaw-hass-node follow-ups

Owner: Clawd (Rob authorized merging open b1/b2 streaming PRs only if Codex review is clean).
Status: Rob slept through it; in the morning, validate Codex verdict, merge if clean, then file/refresh tickets for everything below.

This list MUST survive context compaction. Treat as authoritative until each item has a Linear/GitHub ticket and is migrated there.

## Outstanding asks (do not drop)

1. **User mapping / identity propagation** — HA Assist → gateway → tools does not propagate which human is talking. Ash nearly social-engineered a file delete because Clawd had no identity to authz against. Need per-user identity end-to-end, used for authz on destructive ops. Highest leverage item.

2. **Real per-tool progress events** — current b2 fix only emits a generic `Working on it...` placeholder after 8s of silence. Rob wants per-tool labels via a proper gateway/node event contract: which tool is running, what it is doing. Placeholder masks model misbehavior (see #8).

3. **Strip "alpha" wording everywhere** — including app/UI text. Earlier attempt looked partially reverted. Verify the actual repo state and finish.

4. **#128 / #129 turn-boundary stale-trailer race** — still open. Do NOT merge the old #129 branch as-is. Prior review found an unclosed post-ack runId-less `session.message` leak window.

5. **HA Assist not responding on Ash's device** — b2 timeout fix may not cover this. Revisit after b2 validation. Likely related to (1) and/or (9).

6. **Doc cleanup sweep** — pre-1.0 hygiene pass: README, STATUS, docs across openclaw-hass-node and sibling repos brought current to actual state. Rob wants this before first real cut.

7. **Issue triage automation** — safe pipeline for Clawd to label, dedupe, and route incoming GitHub issues post-launch. Read-only triage first; any write actions (label add/remove, comment, close) gated behind an allowlist. Hard stop before close/merge without Rob's explicit approval. Design pass needed.

8. **Prompt guard: no faked waiting/working** — NEW finding 2026-06-20. Model emitted `"Timer's running. Waiting."` as a final text with `stopReason=stop`, no tool invoked, turn ended in 15s. Streaming/keep-alive never exercised. Need system-prompt guard: if assistant claims to be waiting/timing/working, there MUST be an active tool call. Text-only `working...` is forbidden. Pairs naturally with (2).

9. **Cross-session subscriber bleed** — NEW finding 2026-06-20. Addon log at 2026-06-20 06:00:58 EDT shows a `cron` sessionKey emitting `delta`+`final` events with `subscribed=['agent:clawd:ha-assist:01kvj6z9...']`. Cron output routed to an ha-assist subscriber. Likely cause of cross-user message bleed reports (Ash seeing Rob's stuff, etc.). Triage as part of (1).

10. **Placeholder coerces stream to final → real answer dropped** — NEW finding 2026-06-20, corrects earlier diagnosis. Sequence at 05:58:34 EDT:
    - `.726` `session.message` assistant placeholder (runId=None)
    - `.728` real model `delta`
    - `.729` `final` — HA closes stream at `.732` (HTTP 200)
    - `.765` `toolResult` arrives AFTER stream closed
    - `.768` real assistant `session.message` (the actual "Timer expired" text)
    - `35.226` second `final` — no subscriber listening
    The first `final` is firing on the placeholder/short turn instead of on the *true* end-of-turn after tool results land. Real answer is generated but never reaches the HA UI. This is the visible bug for "no follow-on response" reports. Likely fix area: gateway's stream-finalization rule must wait until all post-toolResult assistant turns settle before emitting `final`.

11. **Sunset HA MCP → node-tool path with hard read-only blast-radius guards** — Rob's direction 2026-06-20. Subagents should stop using the HA MCP server (the one we just used to fetch addon logs) and instead reach HA through `openclaw-hass-node` tool surface. Constraint: any subagent path must be **software-blocked** from destructive ops (no service calls that mutate state, no addon restart/stop, no entity writes), not just prompt-instructed. Design: read-only allowlist of services/endpoints enforced at the node before the tool ever reaches Anthropic/etc. Pairs with (1) for user-identity gating on the few mutations that survive the allowlist.

## Evidence for #8 and #9

- Session transcript: `/home/openclaw/.openclaw/agents/clawd/sessions/ac3a8fd9-f5e1-4065-ba33-b255fabcddd4.jsonl`
  - 09:52:11 UTC user msg "set timer for 60s..."
  - 09:52:26 UTC assistant final "Timer's running. Waiting." stopReason=stop, no tool calls between
- Addon logs (supervisor REST `/api/hassio/addons/fcccfbbd_openclaw_hass_node/logs`):
  - 05:52:25 single `delta` then 05:52:26 `final` — clean turn close, model decision not infra bug
  - 06:00:58 cron sessionKey events delivered to ha-assist subscriber list (wrong-session bleed)

## Context corrections noted

- MEMORY caveat "openclaw-hass-node SUPERVISOR_TOKEN missing" is STALE as of 2026-06-20. Supervisor token (HASS_TOKEN env in gateway pod) works against `$HASS_URL/api/hassio/addons/<slug>/logs`. Strike that caveat from MEMORY.md next pass.
