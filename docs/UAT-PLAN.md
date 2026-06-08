# User Acceptance Test Plan

> Walk through this in order. Each step has exact actions and the
> result you should see. If anything diverges, paste the diff into
> the channel and Clawd will dig in.

## Phase A — Install (today's deliverable)

### A1. Add the add-on (app) repository

1. In HA → **Settings → Add-ons (Apps) → Add-on (App) Store → ⋮ → Repositories**.
2. Add: `https://github.com/clawd-ops/openclaw-hass-node`
3. Refresh.
4. **Expect:** "OpenClaw Node" appears in the store under a section
   named the same as the repo.

### A2. Install and start

1. Click **OpenClaw Node** → **Install** (multi-arch image; will pick
   `amd64` / `aarch64` / `armv7` for your host).
2. After install, **Start**.
3. Open **Logs**.
4. **Expect:** within ~5 s, lines like:

   ```
   openclaw-node ready (version=2026.6.0)
   ha core version=2026.X.Y
   ha entities visible=NNN
   sample entity=light.<one_of_yours> state=on/off
   ```

5. **Failure modes to watch:**
   - "SUPERVISOR_TOKEN missing" → if running as an HA add-on (app), this
     is an add-on (app) permissions issue (check `hassio_api: true` and
     `homeassistant_api: true` in `addon/config.yaml`, and that the
     add-on (app) was started normally rather than e.g. via a manual
     `docker run`). If running standalone Docker, this is expected;
     the add-on (app) falls back to a `/data` writability check via
     `config._is_addon_mode` (PR #40).
   - "HA REST unreachable" → networking issue, not the node.
   - Python tracebacks → file a comment with the full log.

### A3. Install the companion integration (HACS)

1. **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/clawd-ops/openclaw-hass-node` as
   **Integration** category.
3. Search for **OpenClaw Gateway** → Install → Restart HA.
4. After restart: **Settings → Devices & Services → Add Integration
   → OpenClaw Gateway**.
5. Config flow asks for the add-on (app) socket; default
   (`http://a0d7b954-openclaw-gateway:8099`) should auto-fill.
6. **Expect:** integration sets up clean; one conversation entity
   `conversation.openclaw_gateway` shows up under Settings → Voice
   Assistants → Conversation agents.
7. **Not yet wired:** picking it as your Assist agent will return a
   "gateway not paired" reply. That's correct for today's
   deliverable; pairing is Phase B.

## Phase B — Pairing to OpenClaw gateway *(planned next, NOT today)*

This phase is scaffolded but not running. Steps will be added once
P2.3 (gateway WS pairing) lands.

## Phase C — Read-only HA via the node *(planned)*

Once pairing is up, this is what proves the MCP-replacement story:

### C1. Ask "what's the status of `light.X`?" in a Clawd channel.

- Clawd should answer using `node.invoke ha.get_state` instead of
  the existing MCP server. Verifiable via gateway logs showing the
  node call.

### C2. "List my lights / sensors / climate."

- `node.invoke ha.list_states` with a domain filter.

### C3. "What scripts are available?"

- `node.invoke ha.list_services` filtered to `script`.

### C4. "Show entity registry for `binary_sensor.front_door`."

- `node.invoke ha.list_entity_registry` + filter.

## Phase D — Writes via proposals *(planned)*

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

- Ask Clawd to "edit `.storage/core.config`". Expect refusal at the
  command dispatcher with a clear error message and no proposal
  emitted.

### D5. Breaking-change verification.

- Ask Clawd to apply a change that intersects a known recent HA
  breaking change. Expect the proposal body to cite the
  breaking-change entry and include a functional fix.

## Phase E — Assist conversation agent *(planned)*

### E1. Set Clawd as your Assist conversation agent in
   **Settings → Voice Assistants**.

### E2. Trigger a voice/text intent through Assist.

- Verify it flows: HA Assist → shim ConversationEntity → add-on (app)
  socket → gateway → Clawd → response streams back.

### E3. Tool calling via Assist.

- "Turn on the kitchen light." Should call back into the same node's
  `ha.call_service` via the proposal flow (since light writes are
  protected).

## Phase F — Cross-validation evidence

- After every PR merge in this repo, the PR description links to the
  Codex review verdict comment. Spot-check by opening any merged PR
  on `clawd-ops/openclaw-hass-node` — there should be a Codex
  reviewer comment with `LGTM` or `LGTM with notes`.
- Every PR has all 9 CI gates green (lint, typecheck, two test
  suites, coverage 100 %, security, docs, addon-build, cross-review).
