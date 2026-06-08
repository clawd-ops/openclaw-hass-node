# Install

> ⚠️ **Alpha.** Pair + connect + tool invokes work end-to-end. The
> integration appears as a conversation agent in HA's Voice
> assistants picker, but **Assist turns currently fail** with
> `unauthorized role: node` — see #82 / #84. Fix is in progress
> (P5.13 dual-WS refactor). Install if you want the tool surface
> or to help shape the project; don't expect working Assist yet.

End-to-end setup to get Home Assistant talking to your OpenClaw gateway.
Three pieces install in order: **gateway-side config**, then the **HA add-on (app)**,
then the **HACS shim**.

## Prerequisites

- A running OpenClaw gateway (this add-on (app) is the HA peripheral; the brain
  is whichever agent your OpenClaw is configured to route HA Assist turns
  to).
- Home Assistant OS / Supervised — needed for the add-on (app). The add-on (app)
  builds locally on-device on first install.
- Network reachability: the HA host must be able to reach the
  gateway's WSS URL.
- HACS installed in HA — for the conversation-entity shim.

## 1. OpenClaw gateway: allowlist the node commands

The gateway refuses to surface (or invoke) any node command that isn't on
its allowlist. The HA node ships 28 commands across `ha.*`, `fs.*`,
`system.*`, and `ping`. Add them to your `openclaw.json` under
`gateway.nodes.allowCommands`:

```json
{
  "gateway": {
    "nodes": {
      "allowCommands": [
        "ping",
        "fs.read", "fs.list", "fs.stat", "fs.glob",
        "fs.write", "fs.restore", "fs.history", "fs.diff",
        "fs.move", "fs.delete", "fs.patch",
        "system.run", "system.which",
        "ha.list_states", "ha.get_state", "ha.call_service",
        "ha.list_areas", "ha.list_devices", "ha.list_services",
        "ha.list_entity_registry", "ha.logbook", "ha.history",
        "ha.reload_config", "ha.light_turn_on", "ha.light_turn_off",
        "ha.list_automations", "ha.check_config"
      ]
    }
  }
}
```

Validate + reload:

```bash
openclaw config validate
openclaw gateway restart    # or apply hot via the gateway tool
```

Without this, the node will pair and connect, but `openclaw nodes describe`
will show `Commands: (none reported)` and no command will actually run.

**Do this step BEFORE the pairing-approval in step 3.** The gateway
captures the allowed-commands set at pairing-approval time, not on
every connect. If you approve first and patch second, the device's
stored approved-commands stays empty and you'll have to
`openclaw devices remove <id>` and re-pair to pick up the new list.

## 2. HA add-on (app): install + configure

1. Settings → Add-ons (Apps) → Add-on (App) Store → ⋮ → **Repositories** → paste
   `https://github.com/clawd-ops/openclaw-hass-node`.
2. Open **OpenClaw Node** → **Install**. First build runs locally (HA
   Python-on-Alpine base) and takes 2–4 minutes.
3. **Configuration** tab — fill in:
   - `gateway_url`: e.g. `wss://oc.your-domain/ws`
   - `pairing_token`: a one-time token from your gateway's pairing
     flow. **For Assist to work, this MUST be a dual-role bootstrap
     token** (grants both `node` and `operator` roles on a single
     device record). The plain `openclaw devices add` flow issues a
     node-role-only token; the operator-role connection ChatRelay
     needs will then be rejected by the gateway with
     `INVALID_REQUEST: unauthorized role: operator`.

     On a headless gateway, extract the raw token like this:

     ```bash
     openclaw qr --json --no-ascii | \
       jq -r .setupCode | base64 -d | jq -r .bootstrapToken
     # → e.g. KsQ3euJaFrppxKsdqV4QUAJXhbtGg5pgg368BGUbwOk
     ```

     `openclaw qr` is named for the mobile pairing flow, but on a
     headless install you don't need the actual QR — the JSON output
     contains the same dual-role bootstrap profile the mobile app
     would scan. Paste the printed `bootstrapToken` value into this
     field.
   - `node_name`: friendly name shown in the gateway UI (e.g. `hass`).
   - `local_api_token` **(required)**: any opaque random string
     (e.g. `openssl rand -hex 32`). The local HTTP API is fail-closed:
     when this is empty, every non-public path returns
     `401 NO_TOKEN_CONFIGURED` and the HACS shim cannot reach the
     node. Public paths (`/health`, `/v1/health`,
     `/v1/conversation/info`) stay open so HA's health probes and the
     shim's config-flow discovery still work. **Paste the same value
     into the HACS shim config flow** (step 4) so the integration can
     authenticate.
   - `hass_url` *(optional)*: HA base URL fallback. Leave blank in
     normal Supervisor installs — the node hits `http://supervisor/core`
     with the Supervisor-injected token. Set when (a) your Supervisor
     does not inject `SUPERVISOR_TOKEN` despite the add-on's API flags,
     or (b) you run the same image standalone outside of Supervisor.
     Example values: `http://homeassistant:8123` from inside the add-on
     network, or your full external HTTPS URL.
   - `hass_token` *(optional)*: Long-lived access token from a HA user
     with the scopes your `ha.*` calls need. Required together with
     `hass_url` whenever you fill that in. Generate at HA → Profile →
     Long-Lived Access Tokens. Treated as a password (masked).
4. **Start** the add-on (app). Watch the log — you should see one
   `Connecting to gateway` and (the first time) a `PAIRING_REQUIRED`
   message.

## 3. Approve the pairing on the gateway

A node connecting with a dual-role bootstrap token files multiple pair
requests — one per role in the `devices` registry, plus one in the
`nodes` registry. **Approve all pending requests for this device** or
Assist will silently fall back to "operator not connected":

```bash
openclaw nodes pending           # node-role + captured commands
openclaw nodes approve <request-id>

openclaw devices list            # find both pending entries for this device
openclaw devices approve <request-id-node>
openclaw devices approve <request-id-operator>
```

The add-on (app)'s reconnect loop picks the approval up within ~5 seconds. The
log should switch to:

```
Gateway accepted connection; node is paired.
```

The gateway issues a long-lived device token in that connect response;
the add-on (app) persists it to `/data/openclaw/device-token` and reuses it
on every restart. **You don't need to re-paste `pairing_token` after the
first successful pairing** — it's consumed.

## 4. HACS shim: install + bind

1. HACS → Integrations → Custom repositories → add this repo URL,
   category Integration.
2. Install **OpenClaw Gateway** from HACS.
3. Restart HA.
4. Settings → Devices & Services → **Add Integration** → OpenClaw
   Gateway. The config flow asks for:
   - **Socket URL**: the add-on's local endpoint. The default
     `http://<addon-slug>:8099` works inside Supervisor. If the
     auto-detected hostname uses underscores, the integration
     rewrites them to dashes automatically (Supervisor DNS uses
     dashes).
   - **API token** **(required)**: paste the same `local_api_token`
     you set in the add-on config (step 2). The field renders as a
     password. The local API is fail-closed; if you skip this, the
     shim will get `401 NO_TOKEN_CONFIGURED` on every Assist turn.
5. Settings → **Voice assistants** → set OpenClaw Gateway as the
   conversation agent for whichever assistant you want.

## Verifying end-to-end

```bash
# Gateway side — node should be paired AND connected, with chips listed:
openclaw nodes describe --node <your-node-id>
# Expect: Status: paired · connected
#         Caps:   …
#         Commands: list of 28
```

Or round-trip a command directly from the gateway side:

```bash
openclaw nodes invoke --node <your-node-id> --command ping
# → {"pong": true}
```

Every invoke is logged in the add-on (app) log at INFO with a compact
entry/exit pair and elapsed ms:

```
invoke ▶ ping id=abc12345
invoke ◀ ping ok id=abc12345 4ms
```

Unknown commands log at WARNING (`UNKNOWN_COMMAND`); thrown exceptions
log at ERROR (`COMMAND_ERROR`) with a traceback. If you ran an invoke
and see nothing in the log, the node didn't receive it — check the
gateway's pending queue and the WSS connection.

Then ask HA Assist anything. The conversation relay (P5.13 dual-WS)
forwards Assist turns through the node's operator-role connection into
an OpenClaw agent session and streams the response back. If you see a
timeout, check the node log for connection/auth errors, confirm both
the node-role and operator-role connections are up (two "connected"
log lines per connect cycle), and verify the gateway is reachable.
Pair the device with a dual-role profile via `openclaw qr` for Assist
to work.

## Troubleshooting

| Symptom in add-on (app) log                                            | Fix                                                                                              |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `Failed to install app · unknown error · check Supervisor logs`  | Usually means the local build failed. Check Supervisor logs for the actual stack.                |
| `pip: not found`                                                 | Wrong base image. Should be fixed in current `build.yaml` (HA python-on-Alpine).                  |
| `INVALID_REQUEST: at /client/id: must be equal to one of …`      | Out-of-date add-on (app). Update — `client.id` must be `node-host`.                                    |
| `DEVICE_AUTH_SIGNATURE_INVALID`                                  | Out-of-date add-on (app). Update — `client.deviceFamily` must be sent in connect frame for signature.  |
| `AUTH_TOKEN_MISSING`                                             | Either no `pairing_token` set on first run, or the token expired before pairing was approved.    |
| `NOT_PAIRED` after `openclaw devices approve`                    | Add-on (App) still using old pairing_token. Update to the latest add-on (app) (token now auto-persists).     |
| `Gateway connection lost: <ws error>` (every 5s)                 | Network or `gateway_url` typo. The 5s reconnect cadence is normal.                               |
| Connected but `openclaw nodes describe` shows `Commands: (none)` | Missing the `gateway.nodes.allowCommands` patch above — OR you approved the pairing before applying the patch. Run `openclaw devices remove <id>` and let the addon re-pair. |

## Updating

Each release re-runs the local Supervisor build. After updating the
add-on (app) repo, **Update** the add-on (app) (or **Stop → Rebuild → Start**). The
device token persists across restarts; you do not need to re-pair.

## Standalone Docker (without Home Assistant Supervisor)

The same image used by the HA add-on can run as a plain Docker
container. The `addon/config.yaml` file is HA-Supervisor metadata; it
is not consulted outside Supervisor, so the standalone path is wired
entirely through env vars and a published port.

```bash
# Build once
docker build -t openclaw-hass-node:dev addon/

# Run
docker run --rm \
  -e GATEWAY_URL="wss://oc.your-domain/ws" \
  -e PAIRING_TOKEN="<one-time pairing token>" \
  -e NODE_NAME="hass" \
  -e OPENCLAW_LOCAL_API_TOKEN="$(openssl rand -hex 32)" \
  -v openclaw-hass-node-data:/data \
  -p 8099:8099 \
  openclaw-hass-node:dev
```

Notes:
- The container always binds `0.0.0.0:8099` inside; the `-p 8099:8099`
  is what exposes it to the host. Use `-p 127.0.0.1:8099:8099` to bind
  only to loopback.
- `OPENCLAW_LOCAL_API_TOKEN` is strongly recommended for standalone
  Docker — without it the API is open to anything that can reach the
  published port.
- `-v openclaw-hass-node-data:/data` persists the device identity and
  device token across restarts. Without it the node re-pairs on every
  start.
- The HA REST snapshot endpoint (`/v1/ha/snapshot`) needs `HASS_URL`
  and `HASS_TOKEN` env vars in standalone mode (Supervisor injects the
  equivalents automatically inside HA).
- Conversation-agent integration in this mode: the HACS shim is a
  Home Assistant custom component, so it only matters if you have a
  Home Assistant instance somewhere — but that instance does **not**
  need to be HAOS/Supervised. HA Container works too. Install HACS in
  whichever HA you have, install the **OpenClaw Gateway** shim, and
  point its socket URL at this node (e.g.
  `http://<node-host>:8099`, plus the same `local_api_token` you set
  on the node). The default URL targets the HA add-on hostname; the
  config flow lets you override it.
- If you have no Home Assistant at all and are only running the node
  container, gateway-side tool invokes still work as soon as the node
  is paired/approved — there is just no HA Assist surface to wire up.
