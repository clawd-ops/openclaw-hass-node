# Build-time lessons (in-repo memory)

> Each entry is a thing that bit us during the 2026-06-06 → 2026-06-07
> install push. Future-Clawd: read this **before** editing the connect
> frame, the Dockerfile, or the addon config — every one of these was
> a surprise the first time and an obvious-in-hindsight fix the second.

## Addon / build

1. **Supervisor's build context is the addon folder, not the repo root.**
   Dockerfile `COPY` paths must resolve against `addon/`. Concretely:
   `COPY node /opt/...` works only because `node/` lives at `addon/node/`.
   `COPY addon/run.sh /run.sh` does NOT work — `addon/` is the context.
2. **`image:` in `config.yaml` makes Supervisor pull, not build.** If we
   set `image: ghcr.io/...{arch}` with no published images, Supervisor
   404s on the pull and surfaces a useless "unknown error" dialog. Leave
   `image:` out until P7 stands up the GHCR publish workflow.
3. **`build_from` only accepts HA's allowed base images.** A bare
   `python:3.13-alpine` is silently ignored and Supervisor falls back to
   `*-base` (no Python). Use
   `ghcr.io/home-assistant/{arch}-base-python:3.13-alpine3.20`.
4. **CI's addon-smoke job must use `docker build addon`, not `docker
   build -f addon/Dockerfile .`.** The latter passes if the Dockerfile
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
    brain" detour. See `RESEARCH-OPENCLAW-INTEGRATION.md` for the
    post-mortem and the runtime-audit at
    `workspace/runtime-audits/2026-06-06-openclaw-node-conversation-relay-doc-gap.md`
    for the upstream-doc fix.

## Operator setup steps that the docs don't mention

16. **`gateway.nodes.allowCommands`** (see lesson 9) is required to
    surface our commands. Documented in `INSTALL.md` § 1.
17. **Add-on (App) options reach the Python process via `addon/run.sh`** which
    reads `/data/options.json` and exports each key as an env var
    (uppercased). When adding a new option, update `run.sh` too.
18. **Bump `addon/config.yaml` `version:` on every release.** Otherwise
    HA Supervisor never offers Update; users must Uninstall →
    Reinstall, which wipes `/data`, deletes the persisted device-token
    and Ed25519 identity, and forces a re-pair every release. See
    `docs/CONTRIBUTING.md`.
19. **`gateway.nodes.allowCommands` is read at *pairing-approval time*,
    not at every connect.** If the operator approves a device before
    they add the addon's command list to `openclaw.json`, the gateway
    stores an empty approved-commands set for that device and no
    reconnect will fix it. Remediation: `openclaw devices remove
    <deviceId>`, ensure the allowCommands patch is live, then re-pair.
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
22. **`gateway.nodes.allowCommands` has `reloadKind: restart`** (verify
    via `mcp__openclaw__gateway action=config.schema.lookup
    path=gateway.nodes.allowCommands`). After editing the patch into
    `openclaw.json`, restart the gateway (via
    `mcp__openclaw__gateway action=restart` or pod-delete) **before**
    the next pair approval — the allowlist is captured into the
    device's stored record at approval-time. Approval before restart
    stores an empty set; pairing then has to be removed and re-done.

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
