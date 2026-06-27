# openclaw-hass-node

[![GitHub stars](https://img.shields.io/github/stars/clawd-ops/openclaw-hass-node?style=social)](https://github.com/clawd-ops/openclaw-hass-node/stargazers)
[![BuyMeCoffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-donate-FFDD00?logo=buymeacoffee&logoColor=black)](https://www.buymeacoffee.com/roblandry)

> ⚠️ **Beta — not yet 1.0.** The node command surface and the HA
> Assist conversation relay (dual websocket pair, streaming token
> deltas, tool-named progress) both work end-to-end: pair, connect,
> invoke round-trips, and conversation relay all stream cleanly.
> Publishing infrastructure is still settling and breaking changes
> between pre-1.0 builds are still possible. Watch
> [`docs/STATUS.md`](docs/STATUS.md) for the first stable tag.

> 🕷️ **With great power comes great responsibility.** Installing this
> add-on hands an AI agent a direct line into your Home Assistant: it can
> read entity state, call services, control devices, edit files under
> `/config`, `/share`, and `/media`, fetch addon logs and metadata, and
> (gated behind `OPENCLAW_ADMIN_TOKEN`) reload HA core config or run
> shell commands inside the addon container. We sandbox what we can —
> path-traversal protection, a read-only addon surface that strips
> secrets at the boundary, admin tokens on the destructive commands —
> but we are not a tinfoil hat. A misbehaving, jailbroken, or
> well-meaning-but-overconfident agent CAN delete your automations, brick
> an addon, leak configuration to a chat channel, or otherwise turn your
> smart home into a smart pile of rubble. **If your agent vaporizes your
> HA install, sets your living-room lights to disco at 3 AM, or your Pi
> catches fire trying to render a chart, that is on you and your agent —
> not on this project.** Back up `/config` before you pair. Use a
> least-privilege agent. Pair this with a real backup integration. You
> have been warned.

Home Assistant add-on (app) + HACS shim that connects HA to an [OpenClaw][]
gateway as a node. Lets your OpenClaw agent (Clawd or whichever agent
you've routed to) answer HA Assist turns and run the full `ha.*`
control surface — read entity states, call services, control lights,
read logbook/history, validate config — via the standard OpenClaw
Gateway Protocol.

```
HA Assist UI
    │ user turn
    ▼
HACS shim (ConversationEntity)
    │ POST /v1/conversation
    ▼
OpenClaw HA node (this add-on)
    │ chat.send + sessions.messages.subscribe   ◄── WORKING (operator-role websocket)
    ▼
OpenClaw gateway → configured agent
    │ ha.* tool calls back via node.invoke      ◄── WORKING (node-role websocket)
    ▼
Speech reply
```

Both halves work end-to-end. The node opens two parallel gateway
connections — node-role for invokes, operator-role for the
conversation relay — sharing a single device identity. Pair the
device with a dual-role profile via `openclaw qr`.

**New here?** Read **[`docs/design/PLAN.md`](docs/design/PLAN.md)** for
what this is, what each part (add-on (app), HACS integration, gateway)
does and why, the end-to-end request flow, and the security model.

## Install

See **[`docs/INSTALL.md`](docs/INSTALL.md)** for the full end-to-end
walkthrough, including the **required** `openclaw.json` patch on the
gateway side. Short version:

1. Patch `gateway.nodes.allowCommands` in your OpenClaw config (the
   gateway silently drops unknown commands; without this the node
   pairs but no commands work).
2. Add this repo as an HA add-on (app) repository, install **OpenClaw Node**,
   fill in `gateway_url` + `pairing_token` + `node_name`, start it.
3. `openclaw devices approve <request-id>` on the gateway.
4. Install the **OpenClaw Gateway** HACS integration, point its config
   flow at the add-on (app) socket.
5. Pick it as your HA Assist conversation agent.

> Standalone Docker (without HA Supervisor) is **not a supported install
> path during beta**. The runtime entrypoint depends on
> the HA base-python image's s6-overlay `with-contenv` wrapper to pick up
> Supervisor's injected env. Standalone-mode code in `__main__.py`
> still exists for testing on the dev host directly, but the Docker
> image is HA-only.

## Status

- **Node command surface**: complete (37 commands: `ha.*` × 23, `fs.*`
  × 11, `system.*` × 2, `ping`).
- **Pairing + connect**: works end-to-end with device-token persistence.
- **Conversation relay (`/v1/conversation` → OpenClaw chat surface)**:
  working today, streams token deltas with tool-named progress lines.
  The node's operator-role WebSocket owns the relay; pair with a
  dual-role profile to enable it.
- **Local HTTP API**: fail-closed bearer auth (`local_api_token` is
  required; non-public paths return `401 NO_TOKEN_CONFIGURED`
  otherwise). The HTTP command surface is allowlisted to `ping` and
  `system.which`; the full surface is gateway-authorized and delivered
  over the node-role gateway WS.

Live state and roadmap: [`docs/STATUS.md`](docs/STATUS.md).
Architecture and decisions: [`docs/design/PLAN.md`](docs/design/PLAN.md).
Install/troubleshooting: [`docs/INSTALL.md`](docs/INSTALL.md).
Release + versioning policy: [`docs/operations/RELEASE.md`](docs/operations/RELEASE.md).

## License

[MIT](LICENSE).

## For maintainers / contributors

- [`docs/MEMORY.md`](docs/MEMORY.md) — durable build memory written for
  the Clawd agent driving the implementation. Read it if you're
  resuming work after a context compaction.
- [`docs/operations/LESSONS.md`](docs/operations/LESSONS.md) — gotchas from the install
  push. Read **before** changing the connect frame, Dockerfile, or
  addon config.
- [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) — cross-provider review process.
- [`docs/operations/QUALITY.md`](docs/operations/QUALITY.md) — CI gates and quality bar.

[OpenClaw]: https://github.com/clawd-ops/openclaw
