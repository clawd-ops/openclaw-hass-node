# User Acceptance Test Plan

> Walk through this in order. Each step has exact actions and the
> result you should see. If anything diverges, paste the diff into
> the channel and the agent will dig in.
>
> **State as of the current beta (`2026.6.20b7`):** install, pair,
> connect, gateway-side tool invokes, and Assist conversation relay
> (dual websocket pair, streaming token deltas, tool-named progress)
> all work end-to-end. Local HTTP API is fail-closed (a token is
> required); HACS integration probes for the local API at config-flow time.
> The proposal/write flow is still planned.

## Phase A — Install

### A1. Add the add-on (app) repository

1. In HA → **Settings → Add-ons (Apps) → Add-on (App) Store → ⋮ → Repositories**.
2. Add: `https://github.com/clawd-ops/openclaw-hass-node`
3. Refresh.
4. **Expect:** "OpenClaw Node" appears in the store under a section
   named the same as the repo.

### A2. Install and start

1. Click **OpenClaw Node** → **Install** (multi-arch image; will pick
   `amd64` / `aarch64` / `armv7` for your host).
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
#         Commands: list of 37 (ha.*, fs.*, system.*, ping)
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
(`gateway.nodes.allowCommands` in `openclaw.json`) controls which
commands are surfaced — see `INSTALL.md` step 1.

## Phase D — Writes via proposals *(planned)*

The write side of `fs.*` (`fs.write`, `fs.restore`, `fs.move`,
`fs.delete`, `fs.patch`) is implemented in the node but is *not* yet
behind the proposal/agent-bridge flow.

### D1. Toggle a light.

- `node.invoke ha.call_service` with `light.turn_on`.
- Goes through proposal flow → agent-bridge UI shows the proposal →
  accept → light turns on → backup engine records the prior state.

### D2. Edit `configuration.yaml`.

- A small comment-only change. Verify:
  1. Proposal appears in agent-bridge.
  2. `ha.check_config` runs before apply.
  3. Prior bytes captured under `/share/openclaw-backups/`.
  4. `fs.history /config/configuration.yaml` shows the version.
  5. `fs.restore` reverses cleanly.

### D3. Reject a proposal.

- Decline in the agent-bridge UI.
- Verify the file is unchanged and the node logs the rejection.

### D4. `.storage/` refusal.

- Ask the agent to "edit `.storage/core.config`". Expect refusal at the
  command dispatcher with a clear error message and no proposal
  emitted.

### D5. Breaking-change verification.

- Ask the agent to apply a change that intersects a known recent HA
  breaking change. Expect the proposal body to cite the
  breaking-change entry and include a functional fix.

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
  `ha.call_service` via the proposal flow (since light writes are
  protected).

### E4. Tool-named progress lines.

- Ask Assist a prompt that requires a tool call, e.g. "what's the
  weather?" While the agent is still thinking, the conversation
  surface should show a progress line naming the tool, e.g.
  `🔧 Calling weather...` (or whichever tool the agent picks). The
  progress line is replaced by the final answer when the turn
  completes.

## Phase F — Cross-validation evidence

- After every PR merge in this repo, the PR description links to the
  Codex review verdict comment. Spot-check by opening any merged PR
  on `clawd-ops/openclaw-hass-node` — there should be a Codex
  reviewer comment with `LGTM` or `LGTM with notes`.
- Every PR has all CI gates green (ruff check + format, mypy strict,
  pytest with branch coverage gated at 95%, security, app-smoke).
