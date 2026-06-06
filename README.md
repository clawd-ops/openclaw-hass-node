# openclaw-hass-node

OpenClaw node that runs as a Home Assistant add-on, giving the gateway
filesystem + shell + HA-control + Assist-pipeline access on the HA host.

## Source of truth across compactions

This repo and the docs in `docs/` are the durable plan. Clawd's context
window will compact during this build — when resuming work, **start by
reading `docs/PLAN.md` and `docs/STATUS.md`**. Those two files together
describe the goal, the architecture, what's done, what's next, and any
open questions. Do not trust in-conversation memory after a compaction;
trust the docs. Update `STATUS.md` whenever a milestone moves.

## Where to find things

- `docs/PLAN.md` — architecture, scope, decisions, open questions
- `docs/STATUS.md` — current phase, last completed step, next step
- `docs/COMMAND-SURFACE.md` — node commands to implement
- `docs/PACKAGING.md` — add-on layout, Dockerfile, config.yaml
- `addon/` — the HA add-on (Dockerfile, config.yaml, entrypoint)  *(not yet created)*
- `node/` — the OpenClaw node implementation  *(not yet created)*
- `hacs-shim/` — optional HACS component (only if add-on can't register the conversation agent)  *(not yet created)*

## Rules

- All `/config` mutations go through agent-bridge proposals. No direct writes.
- Reads + shell are direct.
- One node per HA instance.
- Add-on first; HACS only as last resort if a capability isn't reachable from add-on context.
