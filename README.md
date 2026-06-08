# openclaw-hass-node

[![GitHub stars](https://img.shields.io/github/stars/clawd-ops/openclaw-hass-node?style=social)](https://github.com/clawd-ops/openclaw-hass-node/stargazers)
[![BuyMeCoffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-donate-FFDD00?logo=buymeacoffee&logoColor=black)](https://www.buymeacoffee.com/roblandry)

> ⚠️ **Alpha — not ready for general use.** As of 2026-06-08 the
> node command surface works end-to-end (pair, connect, invoke
> round-trips cleanly), but the HA Assist conversation relay is
> not yet wired (P5.12), publishing infrastructure isn't in place,
> and breaking changes between versions are expected. If you
> install today, the add-on (app) will pair and run tool commands, but
> Assist turns will return a placeholder. Watch
> [`docs/STATUS.md`](docs/STATUS.md) for the first beta tag.

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
OpenClaw HA node (this add-on (app))
    │ chat.send + sessions.messages.subscribe (existing OC chat surface)
    ▼
OpenClaw gateway → configured agent
    │ ha.* tool calls back via node.invoke
    ▼
Speech reply
```

**New here?** Read **[`docs/OVERVIEW.md`](docs/OVERVIEW.md)** for
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

## Status

- **Node command surface**: complete (28 commands: `ha.*` × 13, `fs.*`
  × 11, `system.*` × 2, `ping`).
- **Pairing + connect**: works end-to-end with device-token persistence.
- **Conversation relay (`/v1/conversation` → OpenClaw chat surface)**:
  P5.12, not yet wired. Until it lands, the endpoint returns a clear
  placeholder. Tool invocations from the gateway work today.

Live state and roadmap: [`docs/STATUS.md`](docs/STATUS.md).
Architecture and decisions: [`docs/PLAN.md`](docs/PLAN.md).
Install/troubleshooting: [`docs/INSTALL.md`](docs/INSTALL.md).

## License

[MIT](LICENSE).

## For maintainers / contributors

- [`docs/MEMORY.md`](docs/MEMORY.md) — durable build memory written for
  the Clawd agent driving the implementation. Read it if you're
  resuming work after a context compaction.
- [`docs/LESSONS.md`](docs/LESSONS.md) — gotchas from the install
  push. Read **before** changing the connect frame, Dockerfile, or
  addon config.
- [`docs/PROCESS.md`](docs/PROCESS.md) — cross-provider review process.
- [`docs/QUALITY.md`](docs/QUALITY.md) — CI gates and quality bar.

[OpenClaw]: https://github.com/clawd-ops/openclaw
