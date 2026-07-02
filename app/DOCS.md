# OpenClaw HA Node — App

The OpenClaw HA Node — App connects this Home Assistant instance to an
OpenClaw gateway as a remote node. It exposes a controlled filesystem,
shell, and Home Assistant control surface to the gateway, and streams
Assist conversation turns through node-role and operator-role
connections.

This page documents every option exposed by the app configuration
schema, plus the authorization model that governs the Home Assistant
control surface. The manifest field reference is at
<https://developers.home-assistant.io/docs/apps/configuration/>.

## Quick start

1. Install the app from the OpenClaw HACS repository.
2. Set `gateway_url` to the WebSocket URL of your OpenClaw gateway.
3. Generate a pairing token from the gateway (`openclaw qr`) and paste
   it into `pairing_token`.
4. Set `local_api_token` to a strong random value (used by the HACS
   integration to authenticate Assist turns to the app).
5. Start the app. After successful pairing, leave
   `pairing_token` and `reset_pairing` alone unless you intentionally
   want to re-pair.

## Configuration options

### `gateway_url`

- **Purpose**: WebSocket URL of the OpenClaw gateway this node connects
  to.
- **Type**: `url` (required).
- **Example**: `"wss://gateway.example.com/ws"`.
- **Default**: `"wss://gateway.example.com/ws"` (placeholder; must be
  replaced).
- **Security**: Use `wss://` for any non-loopback gateway. The pairing
  handshake and all relayed traffic flow over this channel; a plain
  `ws://` link will expose the pairing token and per-turn content.

### `pairing_token`

- **Purpose**: One-shot setup-code that the gateway issues for a new
  node identity. After the first successful handshake the app
  persists a long-lived device token internally and the setup-code is
  no longer required.
- **Type**: `password` (required for the first run; may be left blank
  once paired).
- **Example**: `"oc_pair_5f0c…"`.
- **Default**: `""`.
- **Security**: Treat as a secret with a short useful lifetime. Once
  pairing succeeds, clear the field or rotate it on the gateway side so
  the same code cannot be replayed by another device.

### `node_name`

- **Purpose**: Friendly display name the gateway uses for this node in
  its UI and audit logs.
- **Type**: `str?` (optional).
- **Example**: `"living-room-ha"`.
- **Default**: `""` (gateway falls back to the device record name).
- **Security**: Informational only; the gateway does not authorize
  anything based on it.

### `local_api_token`

- **Purpose**: Shared bearer token used by the in-Supervisor HACS integration
  to authenticate Assist relay requests to this app's local HTTP
  API. The same value is also used to derive the HMAC subkey that
  signs the HA actor metadata forwarded on each Assist turn.
- **Type**: `password?` (strongly recommended).
- **Example**: `"a-long-random-string-from-openssl-rand-hex-32"`.
- **Default**: `""`.
- **Security**: The local API is reachable only on the Supervisor
  app network (no host port mapping), but the HACS integration still
  requires this token to talk to it. If it is unset, the actor-signing
  path is disabled and HA Assist turns degrade to anonymous role
  resolution (every caller resolves to `user`). Set this to a strong
  random value; rotate it if it leaks.

### `reset_pairing`

- **Purpose**: One-shot recovery toggle. When `true` on the next app
  startup, the persisted device token is wiped while the device
  identity is kept. This lets a fresh setup-code from the *same*
  device record re-pair, which is the safe way out of an
  `AUTH_TOKEN_MISMATCH` loop. The wipe runs exactly once; subsequent
  restarts with the option still set to `true` skip the wipe
  automatically (a warning is logged). To request another wipe, toggle
  the option to `false`, save, and back to `true`.
- **Type**: `bool?`.
- **Example**: `true` (fires once), no need to revert to `false` unless
  you want to re-enable the one-shot for another wipe.
- **Default**: `false`.
- **Advanced**: A full identity wipe (also deletes `node-key.json`,
  forcing a brand-new gateway device record) is available out-of-band
  by setting the env var `OPENCLAW_RESET_PAIRING=identity` on the
  app container. The UI does not expose identity-mode because it is
  destructive enough to warrant an explicit out-of-band step.

### `reset_bootstrap`

- **Purpose**: Re-enable the one-shot bootstrap endpoint after it has
  been consumed. When `true` on the next app startup, the
  `bootstrap-consumed` and `bootstrap-claimed` markers are removed from
  `data_dir`, re-opening a fresh 5-minute bootstrap window. Use this if you need to
  reinstall or reconfigure the HACS integration and the bootstrap
  endpoint is reporting `BOOTSTRAP_CONSUMED`.
- **Type**: `bool?`.
- **Example**: `true` (re-opens the window once on next restart).
- **Default**: `false`.
- **Security**: After the HACS integration fetches the token, set this
  back to `false`. If left as `true`, the bootstrap markers are cleared
  on every subsequent restart, which re-opens the 5-minute window each
  time. The integration claims a rotated operational token immediately
  after fetching the bootstrap token, but the short bootstrap window should
  still only be open intentionally. See `docs/security-notes.md` for the
  full bootstrap threat model.

### `identity.super_admins`

- **Purpose**: List of Home Assistant usernames whose Assist turns
  receive the `super_admin` policy context (no forbidden-command
  defaults). HA admins not in this list resolve to `admin`; every
  other HA user resolves to `user`.
- **Type**: list of `str`.
- **Example**:
  ```yaml
  identity:
    super_admins:
      - "bigrob8181"
  ```
- **Default**: `[]`.
- **Security**: These are HA usernames, not Discord or OpenClaw
  identities. The app resolves them to HA user IDs at startup using
  the HA `config/auth/list` WebSocket command, then compares the signed Assist
  actor user ID against the resolved set. Granting `super_admin`
  removes the per-turn
  forbidden-command guard rails for that user; keep the list short
  and intentional.

If a configured username is not found at startup, the app logs a
warning and ignores that entry for the current run. This keeps a typo
from blocking startup while still failing closed to the lower `admin`
or `user` role for Assist turns.

### `identity.user_agent_map`

- **Purpose**: Optional per-HA-user gateway agent routing. Each entry
  picks which gateway `agentId` to use on `chat.send` for the named
  HA user. The agents themselves are configured in the gateway; this
  app only chooses between them.
- **Type**: list of `{ha_username: str, agent_id: str}` objects.
- **Example**:
  ```yaml
  identity:
    user_agent_map:
      - ha_username: "bigrob8181"
        agent_id: "<your-agent-id>"
      - ha_username: "ash"
        agent_id: "household"
  ```
- **Default**: `[]`.
- **Security**: Routing only; does not grant permissions. The app
  resolves usernames to HA user IDs at startup, then routes signed
  Assist actors by ID. Pointing a user at an agent that exposes more
  capabilities is a policy choice for the operator.

### `identity.default_agent_id`

- **Purpose**: Gateway `agentId` to use for HA users not matched by
  `user_agent_map`. Empty preserves the gateway's own default routing.
- **Type**: `str?`.
- **Example**: `"my-agent"`.
- **Default**: `""`.
- **Security**: Same as `user_agent_map`: routing only.

### `identity.forbidden_commands`

- **Purpose**: JSON patch over the built-in per-role forbidden-command
  defaults. Use `add` to add commands to the deny set and `remove` to
  drop defaults you do not want enforced for a role.
- **Type**: `str?` (parsed as JSON).
- **Example**:
  ```yaml
  identity:
    forbidden_commands: '{"user":{"add":["ha.call_service:lock.unlock"],"remove":[]}}'
  ```
- **Default**: `""` (use the built-in defaults from
  `openclaw_node.authz`).
- **Security**: This is a prompt-level guard rail (the per-turn
  authorization disclaimer). It is not a hard invoke-time gate.
  Removing defaults loosens the policy advertised to the model;
  invoke-time protection still lives in each command handler.

### `addon_lifecycle.allowlist`

- **Purpose**: List of Supervisor add-on slugs that may be controlled
  by the Tier B lifecycle commands `ha.addon_start`, `ha.addon_stop`,
  and `ha.addon_restart`. Tier B default-denies every slug; a slug
  must appear here to be allowed at all.
- **Type**: list of `str`.
- **Example**:
  ```yaml
  addon_lifecycle:
    allowlist:
      - openclaw_hass_node
      - my-safe-addon
  ```
- **Default**: `[]` (no Tier B lifecycle action is allowed).
- **Security**: Allowing a slug here means any caller who can
  authenticate to the local API (via `local_api_token`) and reach this
  app can start/stop/restart that add-on. Only add slugs you accept
  the operator-side risk of remote restart for. The denylist below
  still applies on top, and `core_*` slugs are always denied.

### `addon_lifecycle.denylist`

- **Purpose**: Additional Supervisor slugs that are always denied for
  Tier B commands, on top of the always-denied `core_*` prefix and
  the built-in denylist (`homeassistant`, `supervisor`).
- **Type**: list of `str`.
- **Example**:
  ```yaml
  addon_lifecycle:
    denylist:
      - homeassistant
      - supervisor
      - my-untrusted-addon
  ```
- **Default**:
  ```yaml
  - homeassistant
  - supervisor
  ```
- **Security**: A slug listed both here and in `allowlist` is denied;
  the denylist wins. Use this to make sure even an accidental
  allowlist entry cannot bypass policy.

### `hass_url`

- **Purpose**: Optional fallback URL for the Home Assistant Core API.
  When unset, the app uses the Supervisor-injected
  `SUPERVISOR_TOKEN` against `http://supervisor/core`, which is the
  normal path on HassOS / Supervised installs. Set this only when
  running the app stand-alone (no Supervisor) or when the
  Supervisor-injected token is unavailable for some reason and a
  long-lived token fallback is needed.
- **Type**: `str?`.
- **Example**: `"http://homeassistant:8123"`.
- **Default**: `""` — leave blank on HassOS / Supervised.
- **Security**: Internal cluster addresses preferred. Treat as the
  endpoint a long-lived token is bound to.

### `hass_token`

- **Purpose**: Long-lived HA access token paired with `hass_url`.
  Required only when the Supervisor-injected token cannot be used.
- **Type**: `password?`.
- **Example**: long-lived access token from a Home Assistant user.
- **Default**: `""` — leave blank on HassOS / Supervised.
- **Security**: Inherits every permission of the HA user it was
  issued for. Prefer a dedicated user with the minimum scopes the
  `ha.*` commands you intend to call actually need. Rotate it when
  an operator leaves.

#### Browser autofill gotcha (`hass_url` + `hass_token`)

If, on a later restart, you see values you did not type appear in
`hass_url` (often a username string) and `hass_token` (a password
string), the source is almost certainly your browser's password
manager. A `[text-field][password-field]` pair in any HTML form will
sometimes get auto-populated with `(saved-username, saved-password)`
for a different site. The values get saved into the app options on
form submit; nothing inside the app injects them.

These two fields are placed at the very end of the options form,
separated from the other password fields above by the `identity` and
`addon_lifecycle` nested blocks, specifically to keep them out of any
autofill chain. If autofill still strikes, clear both fields, save,
and use a different browser profile (or disable the password manager
on the HA hostname) when editing the app options.

## Authorization model for the HA control surface

The `ha.*` commands split into two tiers.

### Tier A — read / info (no special gate)

Read-only Supervisor metadata that does not change add-on state:

- `ha.addon_logs`
- `ha.addon_info`
- `ha.addon_stats`
- `ha.addon_changelog`
- `ha.addon_documentation`

These are gated only by the local API bearer (`local_api_token`) and
by Supervisor's own role limits on this app. The app manifest
asks for `hassio_role: manager`, which is narrower than `admin` but
covers add-on management read endpoints.

### Tier B — lifecycle (allowlist-gated)

State-changing Supervisor calls:

- `ha.addon_start`
- `ha.addon_stop`
- `ha.addon_restart`

Tier B authorization is:

1. The request must be authenticated by the pairing session — that is,
   the caller has already proved possession of `local_api_token`. This
   is the same gate every local API call passes through.
2. The target slug must appear in `addon_lifecycle.allowlist` (and not
   in `addon_lifecycle.denylist`, and not a `core_*` slug).

There is no separate operator admin token for Tier B. The pairing
session is the authentication boundary; the allowlist is the
authorization boundary. If you do not want a particular add-on to be
restartable remotely, leave its slug out of the allowlist.

## Related references

- Home Assistant app configuration reference:
  <https://developers.home-assistant.io/docs/apps/configuration/>
- Project repository:
  <https://github.com/clawd-ops/openclaw-hass-node>
