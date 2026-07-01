# Build-time lessons (in-repo memory)

> Each entry is a thing that bit us during the 2026-06-06 → 2026-06-07
> install push. Future-Clawd: read this **before** editing the connect
> frame, the Dockerfile, or the addon config — every one of these was
> a surprise the first time and an obvious-in-hindsight fix the second.

## Addon / build

1. **Supervisor's build context is the addon folder, not the repo root.**
   Dockerfile `COPY` paths must resolve against `app/`. Concretely:
   `COPY node /opt/...` works only because `node/` lives at `app/node/`.
   `COPY app/run.sh /run.sh` does NOT work — `app/` is the context.
2. **`image:` in `config.yaml` makes Supervisor pull, not build.** If we
   set `image: ghcr.io/...{arch}` with no published images, Supervisor
   404s on the pull and surfaces a useless "unknown error" dialog. Leave
   `image:` out until P7 stands up the GHCR publish workflow.
3. **`build_from` only accepts HA's allowed base images.** A bare
   `python:3.13-alpine` is silently ignored and Supervisor falls back to
   `*-base` (no Python). Use
   `ghcr.io/home-assistant/{arch}-base-python:3.13-alpine3.20`.
4. **CI's app-smoke job must use `docker build app`, not `docker
   build -f app/Dockerfile .`.** The latter passes if the Dockerfile
   compiles but lies about whether Supervisor's invocation will work.
   The former mirrors Supervisor exactly.

## Protocol wire shapes — canonical source

**Read `/app/node_modules/openclaw/dist/plugin-sdk/packages/gateway-protocol/src/schema/protocol-schemas.d.ts` BEFORE editing any RPC payload.**

The .md docs at `/app/docs/gateway/protocol.md` only list method names.
Field shapes (`{id, nodeId, ok, payload, error: {code, message}}` etc.)
live in `ProtocolSchemas.*Params` TypeBox declarations in that .d.ts
file. The `*.js` compiled outputs are searchable but harder to read.

Tonight ate 4 PRs (#41–#44) because I was bouncing between the .md
docs and the compiled JS instead of going straight to the SDK schema.
Lessons learned from that loop:

- `client.id` must be a `GATEWAY_CLIENT_IDS` enum value (lesson 5).
- `node.invoke.result` shape is `{id, nodeId, ok, payload?, error?}`
  where `error` is an object `{code, message}`, not a string.
- `node.pending.ack` takes `{ids: string[]}` — array, not single `id`.
- `node.pending.pull` takes `{}` — no `limit` param.

## Connect frame (`/app/dist/message-handler-Du1uvc4A.js` is the source of truth)

5. **`client.id` is enum-validated against `GATEWAY_CLIENT_IDS`.** Valid
   values: `webchat-ui`, `openclaw-control-ui`, `openclaw-tui`,
   `webchat`, `cli`, `gateway-client`, `openclaw-macos`, `openclaw-ios`,
   `openclaw-android`, **`node-host`**, `test`, `fingerprint`,
   `openclaw-probe`. Anything else → `INVALID_REQUEST at /client/id`.
6. **Ed25519 v3 payload is reconstructed gateway-side from
   `connectParams.client.{id, mode, platform, deviceFamily}`.** Any
   field you don't send arrives as undefined → empty string → signature
   verify fails (`DEVICE_AUTH_SIGNATURE_INVALID`, reason
   `device-signature`). Keep `_CLIENT_ID`, `_CLIENT_MODE`, `_PLATFORM`,
   `_DEVICE_FAMILY` in `identity.py` strictly in lockstep with the
   `client` dict in `gateway_ws.py`.
7. **`displayName` is what the UI and `openclaw nodes describe` show.**
   Without it, the node renders as the 64-hex device fingerprint. Send
   `config.node_name` or a labelled fallback.
8. **`caps` is enum-validated too — only `tool-events` is currently
   meaningful.** Domain names like `"system"`/`"fs"` are dropped
   silently. Don't rely on caps for the "what does this node do?"
   surface; commands are.
9. **`commands` are filtered through the gateway's `allowCommands` set
   before being stored.** Anything not in
   `PLATFORM_DEFAULTS[platformId] ∪ cfg.gateway.nodes.allowCommands`
   gets dropped. That's why a fresh node shows
   `Commands: (none reported)` despite sending the full list — the
   operator must allowlist them in `openclaw.json`. Documented in
   `INSTALL.md` § 1.

## Pairing / tokens

10. **`pairing_token` is single-use.** Once `openclaw devices approve`
    runs, the original token is invalidated and the gateway issues a
    long-lived `deviceToken` in the `hello-ok.auth.deviceToken` field
    of the connect response. The node must extract it, persist it,
    and prefer it over the pairing_token on subsequent connects.
11. **Persistence lives at `config.device_token_path`** =
    `data_dir/device-token`. Atomic write via `tempfile` + `replace`.
    Load in `__main__.py` before constructing `GatewayClient`.
12. **Reconnect cadence is 5 s** (`_RECONNECT_DELAY_S`). After
    approval, expect ~5 s of one more rejection before the next attempt
    picks up the new state.

## Protocol notes that aren't in `/app/docs/gateway/protocol.md`

13. **`node.pending.pull` returns `ok: false, error: null`** on a freshly-
    paired node. Treated as a non-fatal warning; the event loop runs
    fine after.
14. **The `connect.approved` event the pairing machine waits for may
    never fire** for some pairings — the node has to retry-connect
    after `PAIRING_REQUIRED` and the *next* connect succeeds. The
    pairing-machine's PENDING-state "hold connection open" path is
    cosmetic; what actually drives state forward is the reconnect loop.
15. **Nodes can also originate chat turns via `chat.send`** — they're
    not pure peripherals. `/app/docs/nodes/index.md` frames them as
    peripherals only, which led to the P5.2–P5.11 "build a parallel
    brain" detour. See `docs/research/OPENCLAW-INTEGRATION.md` for the
    post-mortem and the runtime-audit at
    `workspace/runtime-audits/2026-06-06-openclaw-node-conversation-relay-doc-gap.md`
    for the upstream-doc fix.

## Operator setup steps that the docs don't mention

16. **`gateway.nodes.allowCommands`** (see lesson 9) is required to
    surface our commands. Documented in `INSTALL.md` § 1.
17. **Add-on (App) options reach the Python process via `app/run.sh`** which
    reads `/data/options.json` and exports each key as an env var
    (uppercased). When adding a new option, update `run.sh` too.
18. **Bump `app/config.yaml` `version:` on every release.** Otherwise
    HA Supervisor never offers Update; users must Uninstall →
    Reinstall, which wipes `/data`, deletes the persisted device-token
    and Ed25519 identity, and forces a re-pair every release. See
    `docs/CONTRIBUTING.md`.
19. **`gateway.nodes.allowCommands` is part of the approved command
    surface.** If the operator approves a node before adding the addon's
    command list to `openclaw.json`, the gateway stores a too-small
    approved command set. Current gateway builds can repair command
    surface upgrades through a node pending-reapproval request: add the
    allowCommands patch, restart/reconnect the add-on so it advertises
    the full list, then approve the new `openclaw nodes pending` request.
    Full remove/re-pair is the fallback only when no node reapproval
    request can be produced or approved.
20. **Persisted device_token must have a fallback path.** If the
    gateway evicts the device (operator-initiated remove, gateway data
    wipe, etc.), the addon would loop NOT_PAIRED forever sending the
    same invalid token. `_maybe_drop_invalid_device_token` deletes the
    persisted token on NOT_PAIRED/PAIRING_REQUIRED/AUTH_TOKEN_MISMATCH/
    token_mismatch and falls back to `config.pairing_token` so the
    addon self-heals on the next reconnect.
21. **OpenClaw has two parallel pair registries.** A node connecting
    with `role: node` files **two** pair requests in parallel:
    - One in the **devices** registry (auth/token only, no commands).
    - One in the **nodes** registry (commands + caps + node metadata).

    Approving only via `openclaw devices approve <id>` pairs the
    device but leaves the nodes-registry request hanging and the
    stored `commands` / `caps` / `declaredCommands` are all null —
    invoke fails with `node did not declare any supported commands`.
    The correct flow is **also** `openclaw nodes approve <id>` (find
    the request id under `openclaw nodes pending` or read
    `~/.openclaw/nodes/pending.json`). 28-command surface appeared
    immediately after the nodes-side approval landed.
22. **`gateway.nodes.allowCommands` has `reloadKind: restart`.** After
    editing the patch into `openclaw.json`, restart the gateway before
    the next node approval/reapproval so the runtime allowlist includes
    the new commands. Approval against stale runtime config stores the
    stale command surface; fix that by reloading config, reconnecting
    the add-on, and approving the resulting node reapproval request.

## Gateway role policy is binary — operator-scope methods need an operator connection

`isCoreNodeGatewayMethod(method) ? role === 'node' : role === 'operator'`
in `/app/node_modules/openclaw/dist/role-policy-BdV3KRcf.js`. A
`role: node` connection literally cannot call any operator-scope method,
regardless of what scopes it advertised at connect. `chat.send` and
`sessions.messages.subscribe` are `operator.write` scope, so they require
a `role: operator` connection. The P5.12 ChatRelay was built on the
opposite assumption — it shipped (PR #72), looked clean in code review,
passed CI, and failed on first real use (#82) with `INVALID_REQUEST
unauthorized role: node`. There's no `node.chat.send` equivalent.

Fix path documented under P5.13 / #84: open a second WS as
`role: operator`, dedicated to ChatRelay. The device must be paired as
dual-role `[node, operator]` first — done by the `openclaw qr` flow,
which issues a bootstrap token bound to `PAIRING_SETUP_BOOTSTRAP_PROFILE`
(`/app/node_modules/openclaw/dist/device-bootstrap-RTH5XJTg.js`).

When designing anything that calls gateway RPCs from the node: check
the method's `scope` in `core-descriptors-B9yUgJ17.js`. If it's not
`node`, it needs an operator connection.

## Gateway caches and re-approves the node's advertised commands

Discovered 2026-06-20 while shipping the Tier A addon command surface
(b3 → b4), then refined 2026-06-28 after reading the current gateway
reconnect code. The gateway stores the approved command surface for each
node in `~/.openclaw/nodes/paired.json` keyed by `nodeId`. Newly-shipped
commands must be present in three places before they can be used:

- `_NODE_COMMANDS` in `gateway_ws.py` so the node advertises them.
- `commands/dispatcher.py` so the node can handle them.
- `gateway.nodes.allowCommands` so the operator allows them.

If any of those is missing, the command may appear in source or docs but
the gateway will still reject invocations with:

```
node command not allowed: the node (platform: linux) does not support "ha.<x>"
```

…and the invoke never reaches the node (no log line at the addon).
Adding the command to `nodes.allowCommands` in `~/.openclaw/openclaw.json`
is necessary but NOT sufficient by itself.

**Current path when adding new node commands:**

1. Ship + release the command in the node (advertise it from
   `_NODE_COMMANDS` in `gateway_ws.py`, register the handler in
   `commands/dispatcher.py`).
2. Add the command to `nodes.allowCommands` in the operator's gateway
   config (`~/.openclaw/openclaw.json`).
3. Restart the addon so it reconnects and advertises the fresh command
   list. `hassio.addon_restart` via HA service call is enough. A gateway
   hot reload alone does not make the old addon process advertise new
   commands.
4. Watch `openclaw nodes pending` / `openclaw nodes status`. Current
   gateway builds detect a command-surface upgrade on reconnect and
   create a node `pending-reapproval` request. Approve that request with
   `openclaw nodes approve <requestId>`; this updates the paired node
   record without removing the device or doing a full re-pair.
5. Verify with `openclaw nodes status` or the `nodes` tool — the live
   `commands` array should now include the new entries. If it doesn't,
   the addon image didn't pick up the latest source (HA "Update"
   sometimes reuses a cached layer — try Rebuild).

Surgical workaround when you can't bounce the addon or use node
reapproval: directly edit `paired.json` to append the missing commands,
then hot-reload the gateway. Brittle and drift-prone; only use if you
understand why the normal reconnect/reapproval path is not available.
The b3→b4 bring-up did this once and the addon restart immediately
overwrote it, which proved the proper fix was the fresh addon
advertise/reapproval path.

Future-Clawd: when a Tier B / new HA-domain command lands and "the node
does not support" shows up despite a green build and a config push,
this is almost always why. Don't waste a session debugging the wire
protocol — check `_NODE_COMMANDS`, restart the addon, then approve the
pending node reapproval.

## Known streaming edge cases (2026-06-20 audit, gateway-side root causes)

Investigated 2026-06-20 for TODO items #4, #9, #10. All three live in
the gateway (`/app/dist/*.js`), not this repo. None are being patched
upstream by this project — these are operational notes so future-Clawd
can recognise the symptoms instead of re-deriving them.

### #4 non-streaming stale-trailer (theoretical, structural)

PR #129 closed the STREAMING variant via the `_seen_same_run_event`
gate in `chat_relay.py:902-908`. The non-streaming `relay_turn` path
is intentionally NOT gated because the gateway's deferred-reply flow
can legitimately emit a single runId-less `session.message` as the
only event for a turn. Extending the gate would break that flow.

Closing the non-streaming case cleanly requires the gateway to tag
`session.message` with `runId` consistently. Until then: accept the
theoretical risk; revisit only if it manifests in production. If it
does, look for timing-based heuristics (post-ack quiescence period
before accepting a runId-less terminal) rather than a hard gate.

### #9 cross-session subscriber bleed (gateway leak, addon-defended)

Gateway: `/app/dist/server-session-events-TsYthLSk.js:166-211`
`handleTranscriptUpdateBroadcast` unions the broad
`sessionEventSubscribers.getAll()` registry into per-session
`session.message` fan-out. Cron-session deltas therefore reach every
connection subscribed to `sessions.changed`.

Addon defense already in place: `ChatRelay.handle_event`
(`chat_relay.py:851`) drops events whose `sessionKey` is not in
`_reply_events` or `_delta_queues`. Wrong-sessionKey events show in
`[relay-diag]` logs but never reach HA text extraction. Don't be
alarmed by cron sessionKeys in addon logs — they're gateway noise,
not a user-visible leak.

### #10 placeholder coerces `final` (gateway lifecycle bug)

Gateway: `/app/dist/chat-BA3ikhey.js:2811/3031/3216` →
`broadcastChatFinal`. Fires when the placeholder/short turn's
`deliveredReplies` settles, before any post-toolResult assistant
continuation lands. Real reply arrives after stream is closed; HA
sees nothing. Stream-finalization pipeline:
`/app/dist/setup.finalize-DqUrEk5p.js` +
`pending-final-delivery-B7VNQKmB.js`.

Addon band-aid (treat first `final` as soft, wait 1-2s for a real
post-toolResult assistant `session.message`) was considered and
rejected — would change stream contract semantics and delay every
legitimate fast turn. Don't add it.

If "no follow-on response" reports get reliable, the right fix is
gateway-side: defer `broadcastChatFinal` until any pending toolResult
+ its assistant continuation settle for the same `runId`.

## Use GPT-5.5 for checks, not just for code review

Standing instruction from Rob 2026-06-20. The cross-agent review hard
rule (Anthropic plans/drives, Codex/GPT-5.5 reviews/catches) is the
floor, not the ceiling. Empower a fresh GPT-5.5 subagent for any
**verification step** where an independent second pass adds value, not
just for the pre-merge code review.

What that looks like in practice:

- **Pre-merge code review** — still required for every non-trivial
  diff (see `feedback_codex_reviews_use_cli` in the workspace memory).
- **Plan agent for fan-out investigations** — when a TODO item touches
  multiple subsystems (e.g. addon + gateway + integration), dispatch a Plan
  subagent (sonnet-4-6 or gpt-5.5) with a file-path-anchored prompt
  and a sharp output spec (< 800 words, no code, file:line evidence).
  Used for #4/#9/#10 and #2 — both produced concrete merge plans that
  avoided dead-end implementations.
- **Post-merge verification** — when a release lands and the next step
  is "test it in production", consider spawning a GPT-5.5 subagent to
  drive the verification (invoke the new command surface, check the
  log shape, diff against expected output) instead of doing it
  in-context. Frees the main session for the next task.
- **Diff-shaped questions** — "is this still true?", "did we actually
  close X?", "does this match the canonical schema?" — these are
  perfect for a quick subagent dispatch with the file-path hand-off.

When dispatching, give the subagent:

1. The concrete question or task, in one sentence.
2. The file paths it should read.
3. The output shape you want (verdict + evidence pointer; not a
   narrative).
4. A word cap on the response.

The subagent doesn't see your conversation history. Treat it like a
colleague who just walked into the room — brief them tight or get
generic work back.

## "Release PR merged" ≠ "release cut"

Caught 2026-06-20 after I bumped version strings through b3 → b4 → b5
in three separate PRs and assured Rob he could "Update" the addon
through HA — but HA Supervisor reads from published GitHub releases,
not from main, so no Update prompt ever appeared. Rebuild from the
add-on UI was the only working path.

Fixed in PR #156: `.github/workflows/release-on-version-bump.yml`
now creates the tag + GitHub release automatically when a push to
`main` bumps the five tracked version files, with notes extracted
from `app/CHANGELOG.md`. The lesson — "release PR merged ≠ release
cut" — is no longer a live trap on the happy path; it's preserved
here as the reason the workflow exists.

Emergency-only manual recipe (Action failure, retroactive tag):

```sh
SHA=$(git rev-parse main)                # or the merge commit
gh tag v2026.6.20bN $SHA
git push origin v2026.6.20bN
gh release create v2026.6.20bN \
  --title "2026.6.20bN — <line from CHANGELOG>" \
  --prerelease \
  --notes "<short body; link to full CHANGELOG>"
```

## "User-facing docs" includes more than `docs/` and `README.md`

The 2026-06-20 doc-cleanup sweep stripped phase IDs (`P5.13`, `P3.x`,
etc.) from everything in `docs/` and `README.md` — but missed
`app/config.yaml`'s `description:` block, which is the text HA
Supervisor renders in the addon list and detail page. Caught by Rob
post-merge. Both the planner subagent and the post-merge verifier
restricted their scope to `docs/` and the top-level README.

Before treating a doc sweep as complete, scan ALL of these for the
same staleness patterns:

- `README.md` and everything under `docs/` (the obvious ones)
- `app/config.yaml` — `description:` field; rendered by Supervisor
- `app/build.yaml` labels (rarely user-visible but worth a glance)
- `custom_components/openclaw_hass_node_assist/manifest.json` — `name` field;
  shown in HA Integrations list. (As of 2026-06-20 the manifest reads
  "OpenClaw HA Node — Assist"; HACS reads "OpenClaw HA Node — Assist (Beta)" from
  `hacs.json`. Mild inconsistency, intentionally left.)
- `custom_components/openclaw_hass_node_assist/strings.json` — config-flow UI
  copy shown during integration setup
- `hacs.json` — `name` field; shown in HACS catalog
- GitHub repo description (`gh repo view ... --json description`)

Grep recipe for the next sweep:

```sh
grep -rn -E "\bP[3-6]\.[0-9]+\b" \
  README.md docs/ app/config.yaml app/build.yaml \
  custom_components/ hacs.json
```

## Cross-agent code review applies to CI / scripts / workflows too

Caught 2026-06-20 by Rob: PRs #155 (`scripts/bump-version.py` +
`hacs.json` rename) and #156 (`.github/workflows/release-on-version-bump.yml`
+ Version Sync CI job) merged without a GPT-5.5 review pass. Both were
real code — a Python script with regex-driven find/replace logic, plus
a GitHub Actions workflow that mutates main (tags + releases). I
treated them like docs because the diff sat outside `app/node/src/`.

The HARD RULE in MEMORY.md / `feedback_codex_reviews_use_cli` covers
**any** non-trivial code change in clawd-ops/* repos — not just the
addon source tree. CI workflows are especially review-worthy because
their failure modes are silent until production: a bad regex in a
`paths:` filter, a missing `permissions:` scope, an off-by-one in awk
extraction — these don't surface in unit tests.

Future-Clawd: before claiming a code PR is ready to merge, ask
yourself "does this change touch any of: Python under `app/`,
GitHub Actions YAML, shell/Python scripts under `scripts/`, Docker /
build config, packaging metadata?" If yes → spawn the GPT-5.5 review
pass. Docs-only direct-merge is for `.md` (and the equivalent
README/STATUS/HANDOFF surface), not for anything executable.
