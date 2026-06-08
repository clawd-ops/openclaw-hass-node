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
17. **Add-on options reach the Python process via `addon/run.sh`** which
    reads `/data/options.json` and exports each key as an env var
    (uppercased). When adding a new option, update `run.sh` too.
