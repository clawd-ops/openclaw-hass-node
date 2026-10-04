# User Acceptance Test Plan

> Walk through this in order. Each step has exact actions and the
> result you should see. If anything diverges, paste the diff into
> the channel and the agent will dig in.
>
> **Release and installed state:** the latest published beta is
> [`2026.9.13b1`](https://github.com/clawd-ops/openclaw-hass-node/releases/tag/v2026.9.13b1),
> but the last observed live installation remains `2026.7.23b1` until an
> operator performs the Tier B add-on install. Install, pair, connect,
> gateway-side tool invokes, and Assist conversation relay have prior
> end-to-end evidence; they are not yet fresh UAT evidence for `2026.9.13b1`.
> Local HTTP API is fail-closed (a token is required); HACS integration probes
> for the local API at config-flow time. The native OpenClaw approval/write flow
> is still planned. The Gateway remains the sole approval authority; any add-on
> view is presentation-only.
>
> **Scope of the current plan:** Phases A to C and E apply to the published
> beta. [Phase G](#phase-g-policy-gate-bounds-and-assist-remedy-unreleased-source)
> covers behaviour that is merged on `main` but not in any published beta; run it
> only against a build that contains it. No release-candidate claim is made
> until these phases have been run against an installed build and the result is
> recorded.

## Phase A — Install

### A1. Add the add-on (app) repository

1. In HA → **Settings → Add-ons (Apps) → Add-on (App) Store → ⋮ → Repositories**.
2. Add: `https://github.com/clawd-ops/openclaw-hass-node`
3. Refresh.
4. **Expect:** "OpenClaw Node" appears in the store under a section
   named the same as the repo.

### A2. Install and start

1. Click **OpenClaw Node** → **Install** (multi-arch image; will pick
   `amd64` or `aarch64` for your host).
2. **Configuration** tab — fill in `gateway_url`, `pairing_token`,
   `node_name`, and (recommended) `local_api_token`.
3. **Start**.
4. Open **Logs**.
5. **Expect:** within a few seconds, log lines like:

   ```
   Starting openclaw-hass-node <version> in add-on mode
   Gateway URL: wss://...
   Data dir: /data/openclaw
   Loaded existing device identity: <device-id>
   ```

   (Or `Generated new device identity: <device-id>` on first run.)

   On the first connect with a pairing token you will see
   `PAIRING_REQUIRED` and a waiting message. After the gateway-side
   approval the node logs the approval event.

6. **Expected version line:** the version printed in the first log
   line MUST equal what is in `app/config.yaml`. CI gates on
   `test_version_sync.py` keep this from drifting.

7. **Failure modes to watch:**
   - "SUPERVISOR_TOKEN missing" → if running as an HA add-on (app), this
     is an add-on (app) permissions issue (check `hassio_api: true` and
     `homeassistant_api: true` in `app/config.yaml`). If running
     standalone Docker, this is expected; the node falls back to a
     `/data` writability check.
   - "local_api_token is unset" warning → expected if you skipped the
     option; set it before exposing the API outside the Supervisor
     network.
   - "HA REST unreachable" → networking issue, not the node.
   - Python tracebacks → file a comment with the full log.

### A3. Install the companion integration (HACS)

1. **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/clawd-ops/openclaw-hass-node` as
   **Integration** category.
3. Search for **OpenClaw HA Node — Assist** → Install → Restart HA.
4. After restart: **Settings → Devices & Services → Add Integration
   → OpenClaw HA Node — Assist**.
5. Config flow asks for the add-on (app) socket; default points at
   the add-on hostname (`http://<addon-slug>:8099`). If you set
   `local_api_token`, paste the same value here so the integration can
   call the local API.
6. **Expect:** integration sets up clean; one conversation entity
   `conversation.openclaw_hass_node_assist` shows up under Settings → Voice
   Assistants → Conversation agents.

## Phase B — Pairing to OpenClaw gateway *(working)*

### B1. Approve the pairing on the gateway

A node connecting with `role: node` files two pair requests — one in
the `devices` registry and one in the `nodes` registry. **Approve
both** or the node pairs but with zero commands captured.

```bash
openclaw nodes pending
openclaw nodes approve <request-id>

openclaw devices list
openclaw devices approve <request-id>
```

**Expect:** within ~5 s the add-on log switches to `Pairing approved
by gateway.` The gateway issues a long-lived `device_token` on that
connect response; the node persists it to
`/data/openclaw/device-token` (mode `0o600`) and reuses it on every
restart — no need to re-paste `pairing_token` after the first
successful pairing.

### B2. Confirm the gateway sees the node

```bash
openclaw nodes describe --node <your-node-id>
# Expect: Status: paired · connected
#         Caps:   …
#         Commands: list of 57 (ha.*, fs.*, system.*, ping)
```

## Phase C — Tool invokes through the gateway *(working)*

### C1. ping

```bash
openclaw nodes invoke --node <your-node-id> --command ping
# → {"pong": true, "message": "", "ts": <ms>}
```

The add-on log shows:

```
invoke ▶ ping id=abc12345
invoke ◀ ping ok id=abc12345 4ms
```

### C2. Read entity state

Ask in an OpenClaw agent channel: "what is the state of `light.X`?" The agent
should answer via `node.invoke ha.get_state` against this node, not
the legacy MCP server.

### C3. Filesystem reads

`fs.read`, `fs.list`, `fs.stat`, `fs.glob`, `fs.history`, `fs.diff`
all hit the node. The gateway-side allowlist
(`gateway.nodes.commands.allow` in `openclaw.json`) controls which
commands are surfaced — see `INSTALL.md` step 1.

## Phase D — Native approval and protected writes *(planned)*

The write side of `fs.*` (`fs.write`, `fs.restore`, `fs.move`,
`fs.delete`, `fs.patch`) is implemented in the node but does not yet consume
native OpenClaw plugin approvals for protected mutations.

### D1. Toggle a light.

- `node.invoke ha.call_service` with `light.turn_on`.
- With an authorized principal and an `auto_allow` policy decision, no approval
  prompt appears and the light turns on.

### D2. Edit `configuration.yaml`.

- A small comment-only change. Verify:
  1. A request bound to the exact node, principal, command, parameters, and
     precondition appears on a native operator approval surface.
  2. An operator-device `allow-once` decision is consumed exactly once.
  3. `ha.check_config` runs before apply.
  4. Prior bytes are captured under `/share/openclaw-backups/`.
  5. `fs.history /config/configuration.yaml` shows the version.
  6. `fs.restore` reverses cleanly.

### D3. Deny an approval request.

- Deny on the native operator approval surface.
- Verify the file is unchanged and the node logs the rejection.

### D4. `.storage/` refusal.

- Ask the agent to "edit `.storage/core.config`". Expect refusal at the
  command dispatcher with a clear error message and no approval request
  emitted.

### D5. Breaking-change verification.

- Ask the agent to apply a change that intersects a known recent HA
  breaking change. Expect the approval evidence to cite the
  breaking-change entry and include a functional fix.

### D6. Replay and mutation refusal.

- Reuse a consumed `allow-once` decision, then alter one canonical parameter
  before a fresh approved operation is forwarded. Both attempts must fail
  closed without a second side effect.

## Phase E — Assist conversation agent *(live)*

The node opens parallel node-role and operator-role gateway
connections; the operator-role connection owns the conversation relay
(`chat.send` + `sessions.messages.subscribe`), and selecting the
OpenClaw HA Node — Assist integration as your Assist conversation agent streams real
responses back through the gateway. Pair the device with a dual-role
profile via `openclaw qr`.

### E1. Set the agent as your Assist conversation agent in
   **Settings → Voice Assistants**.

### E2. Trigger a voice/text intent through Assist.

- Verify it flows: HA Assist → integration ConversationEntity → add-on (app)
  socket → gateway → the agent → response streams back.

### E3. Tool calling via Assist.

- "Turn on the kitchen light." Should call back into the same node's
  `ha.call_service`. With an authorized principal and an `auto_allow` policy
  decision, no approval prompt appears and the light turns on.

### E4. Tool-named progress lines.

- Ask Assist a prompt that requires a tool call, e.g. "what's the
  weather?" While the agent is still thinking, the conversation
  surface should show a progress line naming the tool, e.g.
  `🔧 Calling weather...` (or whichever tool the agent picks). The
  progress line is replaced by the final answer when the turn
  completes.

## Phase F — Cross-validation evidence

- After every PR merge in this repo, the PR description links to the
  cross-provider review verdict comment. Spot-check by opening any merged PR
  on `clawd-ops/openclaw-hass-node` — there should be a reviewer comment whose
  first line is `APPROVE` or `REQUEST CHANGES`, with one
  `Reviewed head:` line pinning the head and base SHAs and a final
  `Reviewer model:` stamp. A verdict covers only the head it names.
- Every PR has all CI gates green (ruff check + format, mypy strict,
  pytest with branch coverage gated at 95%, security, app-smoke).

## Phase G — Policy gate, bounds, and Assist remedy *(unreleased source)*

These cases cover behaviour merged after `2026.9.13b1`. Every Gateway-forwarded
invoke reaches the node as an operator call, so the household-user gate cannot
be exercised through `node.invoke`; it is covered by tests at source until the
Assist principal reaches the dispatcher. Record the installed version and the
result of each case in the [compatibility matrix](../COMPATIBILITY-MATRIX.md).

### G1. Deny-class service is refused for the operator.

- Invoke `ha.call_service` with a deny-class service against an entity that does
  not exist (for example `update.skip`, which is in the denied `update`
  domain), so a failed refusal could not change anything real. Expect
  `SERVICE_DENIED`, and no HA request is made.

### G2. Light control is not prompted.

- Invoke `ha.call_service` with `light.turn_on` on a known light. Expect success
  with no approval prompt, `changed_states` populated, and
  `changed_states_complete: true` when the target named concrete entity IDs.

### G3. Unknown history entity and read-input validation.

- Invoke `ha.history` for an entity that does not exist. Expect `HA_NOT_FOUND`,
  not an empty history. A misspelled parameter name is refused with
  `INVALID_PARAM`.
- Invoke `ha.list_states` with an `entity_filter` glob (for example
  `light.kitchen*`). Expect a small filtered set, not every entity.
- Invoke `ha.history` and `ha.logbook` directly with `start` / `end`. Expect
  `INVALID_PARAM`: the direct-path names are `start_time` / `end_time` (the
  Assist tool maps `start` / `end` itself).
- Invoke `ha.history` twice with the same bound, once as `...Z` and once as
  `...+00:00`. Expect identical results.

### G4. Oversized request.

- Send an invoke whose `paramsJSON` exceeds the documented limit: expect
  `REQUEST_TOO_LARGE` and no command run.
- This specifically exercises the gateway ingress bound (`paramsJSON` above
  512 KiB). The 8 MiB `fs.write` content cap sits behind that smaller ingress
  bound for the invoke path, and the local HTTP API does not expose `fs.write`,
  so that cap is covered by source-level tests.
- Invoke `fs.patch` with a small patch against a large existing file such that
  the patched result would exceed 8 MiB: expect `RESULT_TOO_LARGE` and the file
  unchanged. This bound is reachable through invoke because the request itself
  stays small.

### G5. `system.run` timeout.

- Through `exec host=node` after approval, run a command that spawns a
  background child and exceeds its timeout. Expect the `timedOut` payload and the
  child gone afterward. Output from a detached process that outlives the timeout
  is abandoned and the payload carries `outputIncomplete: true`.

### G6. Assist remedy.

- With the Assist integration, trigger a caller-fixable configuration failure
  (for example an unresolvable session owner). Expect a short remedy sentence in
  the reply on both streaming and non-streaming turns, never the bare error
  code and never node log text.

### G7. Multi-agent gateway remedy.

- On a multi-agent gateway with `identity.default_agent_id` unset, expect one
  ERROR in the node log at startup naming `default_agent_id`.
- Send one Assist turn from a user with no per-user agent mapping. Expect the
  curated remedy sentence naming `default_agent_id` and no session RPC.
- Set `default_agent_id` to a listed agent and restart. Expect the same turn to
  succeed, and the node log to show an agent-qualified session key
  (`agent:<agentId>:ha-assist:<id>`).

### G8. `fs` history, restore, and diff by `version_id`.

- Write a new file in an allowed, unprotected root, then apply two successive
  `fs.patch` calls that each change the content, then call `fs.history`. A
  write to a file that does not yet exist captures no snapshot; each patch
  captures the bytes it replaces. Expect two entries with distinct `version_id`
  values (the written bytes, then the first patch's result), and each patch
  recorded as an operation.
- `fs.diff` between two `version_id` values, then `fs.diff` from one
  `version_id` against live bytes. Expect the expected changes.
- `fs.restore` by `version_id`. Expect the file to equal the chosen version.
- Call `fs.restore` with `version` `0`. Expect an out-of-range refusal, not a
  silent restore of a different version. Positions are 1-indexed.

### G9. Tier B native approval.

- Call `ha_addon_restart` for an allowlisted slug. Expect an approval prompt
  naming the command and the add-on. Deny it: nothing restarts. Repeat and
  approve: the add-on restarts once.
- Call it for a slug outside `addon_lifecycle.allowlist`. Expect a refusal
  before any approval prompt.
- Repeat the approve and deny steps for `ha_reload_config` and
  `ha_update_install`.

### G10. Entity registry on a large installation.

- On an installation with a large entity registry, invoke
  `ha.list_entity_registry` and `ha.config.entity_registry` `list`, once with
  no filter and once with a `domain` filter. Expect both to succeed and the
  filtered result to be a subset of the unfiltered one, with no transport-size
  failure. The unfiltered call was a live failure on
  `2026.9.13b1`.

### G11. Lifecycle timeout *(optional, needs a slow add-on)*.

- Restart an allowlisted add-on whose restart outlasts the Supervisor timeout.
  Expect `OUTCOME_UNKNOWN`, no automatic retry by the node, and
  `ha.addon_info` afterwards read as a current snapshot only (it cannot show
  whether the restart happened).

### G12. Household everyday control (source-only).

- Household everyday control is merged at source and is **not** live-testable:
  every Gateway-forwarded invoke and every local HTTP API call arrives as an
  operator call, so the household principal does not reach the dispatcher until
  Assist-principal propagation is built. Record the household path as
  unverified; do not report it as passing from operator results.
- Operator-path checks that are live-testable (no change beyond one harmless
  entity of your choice):
  - `ha.call_service` for an everyday-control service (for example
    `switch.turn_on`) on a known entity succeeds with no approval prompt.
  - A deny-class service is still refused with `SERVICE_DENIED` (G1).
  - A service outside the everyday-control table is not refused for the
    operator; the node log records it as an unclassified operator call.
  - The node log and the reply contain no `code` value when one is supplied.
