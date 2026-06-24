# What is openclaw-hass-node?

> ⚠️ **Beta.** The node command surface and the HA Assist
> conversation relay (dual websocket pair, streaming token deltas,
> tool-named progress) are working end-to-end on the current beta
> track. Publishing infrastructure is still settling and pre-1.0
> breaking changes are still possible. Track [`STATUS.md`](STATUS.md)
> for the road to 1.0.


This project connects [Home Assistant](https://www.home-assistant.io)
to an [OpenClaw](https://github.com/clawd-ops/openclaw) gateway as a
first-class **node**. Once installed, the AI agent running inside your
OpenClaw gateway (Clawd, or whichever agent you have routed) can read
and control your Home Assistant install the same way it controls any
other OpenClaw node: through the standard OpenClaw Gateway Protocol,
with the same pairing, auth, audit, and command-allowlist machinery.

You get two practical outcomes today:

1. **Your agent can drive HA directly.** From any OpenClaw chat,
   sub-agent, cron job, or scheduled task, the agent can read entity
   states, call services, control lights, query history and logbook,
   inspect the filesystem inside the HA container, and run shell
   commands, by invoking commands on this node.
2. **HA Assist can talk to your real AI.** Asking Assist a question or
   giving it a command forwards the turn to your OpenClaw gateway over
   the node's operator-role WebSocket, your configured agent answers,
   and the answer plays back through HA's voice pipeline. Pair the
   device with a dual-role profile (`openclaw qr`) for Assist to work.

The project has three moving parts, plus the gateway it talks to.

---

## Part 1: The Home Assistant add-on (app) (`addon/`)

A Supervisor add-on (app) you install into HA. This is the long-lived
process that connects to your OpenClaw gateway and handles incoming
commands.

**What it does:**

- Holds an outbound WebSocket connection (WSS) to your OpenClaw
  gateway. The gateway never connects in; HA stays behind its
  firewall.
- Identifies itself with the gateway's standard signed handshake
  (Ed25519, payload format v3). After the first successful pairing,
  it persists a long-lived device token in `/data/openclaw/` and
  reuses it on every restart, so you only paste the pairing token
  once.
- Serves the 37 commands the agent uses:
  - `ha.*` (23 commands): list states, get state, list devices, list
    entity registry, list areas, list services, call service, turn
    lights on/off, logbook, history, list automations, check config,
    reload config, the Tier A read-only addon surface (list
    addons, addon info, addon stats, addon logs, addon changelog,
    addon documentation), and Tier B addon lifecycle commands
    (addon start/stop/restart) behind an admin token and slug allowlist.
  - `fs.*` (11 commands): list directory, read file, write file,
    stat, glob, etc. Scoped to the maps the add-on (app) is granted
    (`config:rw`, `share:rw`, `media:rw`). Other roots (`ssl`,
    `addons`, `backup`) are intentionally not mapped — re-add only
    when a shipped feature needs them.
  - `system.*` (2 commands): `system.run` (admin-token-gated shell
    invocation) and `system.which` (locate executables on PATH).
  - `ping`: liveness check.
- Logs every invoke at INFO with a compact entry/exit pair and
  elapsed milliseconds, so you can see what the agent is doing
  without enabling debug.

**Why it lives in HA's Supervisor:** Supervisor add-ons (apps) get a
short-lived `SUPERVISOR_TOKEN`, controlled filesystem maps, and a
clean lifecycle (install, update, uninstall). That is the right
isolation boundary for a process with this much reach. The add-on (app)
runs as its own container; HA can stop, restart, and uninstall it
without touching the rest of your install.

**Configuration the user provides:**

- `gateway_url` (e.g. `wss://gateway.example.com/ws`)
- `pairing_token` (one-time, issued by `openclaw devices pair` on
  the gateway)
- `node_name` (optional friendly name shown in the gateway's node
  list)

---

## Part 2: The HACS integration (`custom_components/openclaw_gateway/`)

A thin Home Assistant custom integration installed via HACS. It does
**not** do command execution; that is the add-on (app)'s job. The
integration's job is to make the add-on (app) visible to HA's voice
pipeline so users can pick it as their Assist conversation agent.

**What it does:**

- Registers a `ConversationEntity` that HA's Assist pipeline can
  select.
- Forwards each conversation turn from HA to the add-on (app)'s local
  socket (`http://<addon-slug>:8099/v1/conversation`).
- Returns the agent's reply for HA to speak.

**Why it's separate from the add-on (app):** HA's Assist pipeline only
binds to entities exposed by an integration, not to add-ons (apps).
Splitting the two lets people who only want the tool surface skip
the conversation wiring entirely, and lets people who only want
Assist routing run it against a non-add-on (app) instance later.

**Configuration the user provides:** Just pick the add-on (app)'s socket
in the config flow, then select **OpenClaw Gateway** as the
conversation agent under Settings → Voice assistants.

---

## Part 3: The OpenClaw gateway (`openclaw.json`)

The gateway is not part of this repo, but it has to be configured to
accept this node. Two things need to be set on the gateway side
**before** approving the pairing request:

- `gateway.nodes.allowCommands` must list every command this node
  will accept. The gateway silently drops unknown commands, so a
  node pairs cleanly but invokes return UnknownCommand if the
  allowlist is missing. The `docs/INSTALL.md` walkthrough has the
  full list.
- Restart the gateway after patching `allowCommands`. The config
  key uses `reloadKind: restart`; it's captured into the device
  record at pairing-approval time, so the restart has to happen
  before approval.

Pairing has two queues, both of which need approval:

- `openclaw nodes approve <id>` (captures the command allowlist
  into the device record).
- `openclaw devices approve <id>` (captures auth identity).

---

## How a request flows end-to-end

```
HA Assist user turn
    │ "turn off the kitchen lights"
    ▼
HACS integration (ConversationEntity)
    │ POST /v1/conversation
    ▼
HA add-on (app) (this project)
    │ chat.send to OpenClaw gateway
    ▼
OpenClaw gateway → configured agent
    │ agent decides to call ha.call_service
    ▼
gateway → node.invoke(ha.call_service, …)
    │
    ▼
HA add-on (app) runs the call via HA's REST API
    │ result
    ▼
Agent crafts reply
    │ chat.send back
    ▼
HACS integration returns reply to HA Assist
    │
    ▼
HA speaks "Done."
```

For invokes the agent initiates directly (e.g. from a chat session,
not Assist), the top half of that flow is skipped: the gateway sends
`node.invoke` straight to the add-on (app).

---

## Security model

- **Outbound-only WSS.** The add-on (app) opens the connection to the
  gateway. There is no inbound port for the gateway to attack.
- **Signed handshake.** Pairing uses an Ed25519 key generated inside
  the add-on (app). The private key never leaves `/data`. Connect frames
  are signed (payload format v3) and the gateway verifies the
  signature against the registered public key.
- **Device-token persistence with self-heal.** After first pairing
  the add-on (app) persists the device token. If the gateway later rejects
  it (`NOT_PAIRED`, `PAIRING_REQUIRED`, `AUTH_TOKEN_MISMATCH`,
  `token_mismatch`), the add-on (app) drops the stored token and falls
  back to the pairing token, so a stale token doesn't lock you out.
- **Gateway-side allowlist.** Even if the add-on (app) were compromised,
  the gateway only honors commands listed in
  `gateway.nodes.allowCommands`. Removing a command from that list
  and restarting the gateway disables it everywhere.
- **HA Supervisor isolation.** The add-on (app) runs in its own container
  with explicit filesystem maps. Removing a map (e.g. `media:rw`)
  immediately removes the add-on (app)'s access to that area.
- **No agent reasoning happens here.** This node is purely an
  executor. Prompts, tool-choice, model selection, and policy all
  live in the gateway. That keeps the trust boundary clean: the
  node does what the gateway tells it, the gateway does what the
  agent decides, and the agent runs under whatever policy you have
  configured upstream.

---

## What this is not

- Not an AI by itself. It does not call any LLM. It only exposes HA
  to the agent your OpenClaw gateway already runs.
- Not a replacement for HA Assist. It plugs into Assist as one more
  conversation agent option; the rest of the pipeline (wake word,
  STT, TTS) is unchanged.
- Not a public-internet bridge. Both the WSS to the gateway and the
  agent that responds are yours.

---

## Where to go next

- New user: [`INSTALL.md`](INSTALL.md)
- Live project state and roadmap: [`STATUS.md`](STATUS.md)
- Gotchas and postmortems from the build:
  [`docs/LESSONS.md`](LESSONS.md)
- Cross-provider review and version-bump rules:
  [`docs/PROCESS.md`](PROCESS.md),
  [`docs/CONTRIBUTING.md`](CONTRIBUTING.md)
- Build memory for the Clawd agent driving the work:
  [`docs/MEMORY.md`](MEMORY.md)
