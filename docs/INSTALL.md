# Install openclaw-hass-node

End-to-end setup to get Home Assistant talking to your OpenClaw gateway.
Three pieces install in order: **gateway-side config**, then the **HA add-on**,
then the **HACS shim**.

## Prerequisites

- A running OpenClaw gateway (this add-on is the HA peripheral; the brain
  is whichever agent your OpenClaw is configured to route HA Assist turns
  to).
- Home Assistant OS / Supervised — needed for the add-on. The add-on
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

## 2. HA add-on: install + configure

1. Settings → Add-ons → Add-on Store → ⋮ → **Repositories** → paste
   `https://github.com/clawd-ops/openclaw-hass-node`.
2. Open **OpenClaw Node** → **Install**. First build runs locally (HA
   Python-on-Alpine base) and takes 2–4 minutes.
3. **Configuration** tab — fill in:
   - `gateway_url`: e.g. `wss://oc.your-domain/ws`
   - `pairing_token`: a one-time token from your gateway's pairing flow
     (`openclaw devices` shows pending requests / lets you issue
     bootstrap tokens).
   - `node_name`: friendly name shown in the gateway UI (e.g. `hass`).
4. **Start** the add-on. Watch the log — you should see one
   `Connecting to gateway` and (the first time) a `PAIRING_REQUIRED`
   message.

## 3. Approve the pairing on the gateway

A node connecting with `role: node` files two pair requests — one in the
`devices` registry and one in the `nodes` registry. **Approve both** or
the node pairs but with zero commands captured (so no `ha.*` invoke
will work):

```bash
openclaw nodes pending           # find the request id (this is the one that captures commands)
openclaw nodes approve <request-id>

openclaw devices list            # also pair on the devices side for token auth
openclaw devices approve <request-id>
```

The add-on's reconnect loop picks the approval up within ~5 seconds. The
log should switch to:

```
Gateway accepted connection; node is paired.
```

The gateway issues a long-lived device token in that connect response;
the add-on persists it to `/data/openclaw/device-token` and reuses it
on every restart. **You don't need to re-paste `pairing_token` after the
first successful pairing** — it's consumed.

## 4. HACS shim: install + bind

1. HACS → Integrations → Custom repositories → add this repo URL,
   category Integration.
2. Install **OpenClaw Gateway** from HACS.
3. Restart HA.
4. Settings → Devices & Services → **Add Integration** → OpenClaw
   Gateway. Point its config flow at the add-on's local socket (the
   default `http://<addon-slug>:8099` works inside Supervisor).
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

Every invoke is logged in the add-on log at INFO with a compact
entry/exit pair and elapsed ms:

```
invoke ▶ ping id=abc12345
invoke ◀ ping ok id=abc12345 4ms
```

Unknown commands log at WARNING (`UNKNOWN_COMMAND`); thrown exceptions
log at ERROR (`COMMAND_ERROR`) with a traceback. If you ran an invoke
and see nothing in the log, the node didn't receive it — check the
gateway's pending queue and the WSS connection.

Then ask HA Assist anything. Until P5.12 lands the ChatRelay, the node's
`/v1/conversation` returns a placeholder ("chat-surface relay not wired
yet — see P5.12"). Tool calls work standalone; conversation flow lands
when the relay does.

## Troubleshooting

| Symptom in add-on log                                            | Fix                                                                                              |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `Failed to install app · unknown error · check Supervisor logs`  | Usually means the local build failed. Check Supervisor logs for the actual stack.                |
| `pip: not found`                                                 | Wrong base image. Should be fixed in current `build.yaml` (HA python-on-Alpine).                  |
| `INVALID_REQUEST: at /client/id: must be equal to one of …`      | Out-of-date add-on. Update — `client.id` must be `node-host`.                                    |
| `DEVICE_AUTH_SIGNATURE_INVALID`                                  | Out-of-date add-on. Update — `client.deviceFamily` must be sent in connect frame for signature.  |
| `AUTH_TOKEN_MISSING`                                             | Either no `pairing_token` set on first run, or the token expired before pairing was approved.    |
| `NOT_PAIRED` after `openclaw devices approve`                    | Add-on still using old pairing_token. Update to the latest add-on (token now auto-persists).     |
| `Gateway connection lost: <ws error>` (every 5s)                 | Network or `gateway_url` typo. The 5s reconnect cadence is normal.                               |
| Connected but `openclaw nodes describe` shows `Commands: (none)` | Missing the `gateway.nodes.allowCommands` patch above — OR you approved the pairing before applying the patch. Run `openclaw devices remove <id>` and let the addon re-pair. |

## Updating

Each release re-runs the local Supervisor build. After updating the
add-on repo, **Update** the add-on (or **Stop → Rebuild → Start**). The
device token persists across restarts; you do not need to re-pair.
