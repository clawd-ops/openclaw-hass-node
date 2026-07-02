# Security Notes

## Bootstrap Endpoint (`GET /v1/bootstrap`)

### Background

PR #229 introduced an unauthenticated `GET /v1/bootstrap` endpoint so the HACS
integration can auto-fetch the local API token during config-flow setup, eliminating
the manual copy-paste step. Because the integration needs the token *before* it has one,
the endpoint must be reachable without a bearer token.

### Threat Model

The node's HTTP API runs on port 8099 inside the HA Supervisor add-on network. There is
no `ports:` host-mapping in `app/config.yaml`, so the port is not exposed to the LAN or
internet. The realistic threat surface is **other add-ons or integrations running inside
the same Supervisor network** that could call the endpoint before the HACS integration
does and obtain the API token.

### Defense Layers

Four layers are applied, with decreasing coupling to the network topology:

#### Layer 1 — Network Origin (addon_mode only)

In `addon_mode`, the bootstrap handler checks `request.remote` against the HA Supervisor
internal network range (`172.30.32.0/23`). Requests from outside that range — including
anything that would require a host port mapping to reach — receive **404 Not Found**
(not 401, so the endpoint's existence is not disclosed).

In standalone mode this check is skipped; callers are local by definition.

#### Layer 2 — One-Shot Semantics

The first successful token fetch writes `<data_dir>/bootstrap-consumed` with mode
`0o600` and immediately clears the in-memory token. Any subsequent request returns
**410 Gone** (`BOOTSTRAP_CONSUMED`), regardless of whether the window is still open.

If the token was cleared from memory but the file write failed (e.g. disk full), the
in-memory token is still gone — the token cannot be served twice.

#### Layer 3 — Time-Window

The endpoint only responds with a token during the first `BOOTSTRAP_WINDOW_SECONDS`
(300 s, 5 minutes) after process startup. After the window closes, requests return
**410 Gone** (`BOOTSTRAP_EXPIRED`). The window resets on each add-on restart.

Combined effect of Layers 2 and 3: an attacker inside the Supervisor network must
reach the endpoint within the first 5 minutes of a restart AND before the HACS
integration fetches it. The HACS config-flow probe runs immediately when the user
clicks "Add Integration", so the attacker window is typically measured in seconds.

#### Layer 4 — Token Rotation

After `GET /v1/bootstrap` succeeds, the HACS integration immediately calls
`POST /v1/bootstrap/claim` with `Authorization: Bearer <bootstrap-token>`.
The claim endpoint writes a fresh `local-api-token`, updates the running API
auth token, records the claimed state, and returns the rotated token for the
integration to store. Retries with the original bootstrap bearer are
idempotent within the same node runtime, so a lost claim response can recover
the already-rotated token instead of rotating again. The original token
announced by `GET /v1/bootstrap` stops working as a normal API bearer as soon
as the claim succeeds.

### Operator Recovery: Re-Running Bootstrap

If you need to let the HACS integration fetch the token a second time (e.g. after
reinstalling the integration):

1. In the add-on options, set **`reset_bootstrap: true`**.
2. Restart the add-on.
3. Within 5 minutes, run through the HACS config-flow "Add Integration" step.
4. Set **`reset_bootstrap: false`** in the add-on options after the integration
   fetches and claims its token. If left as `true`, the bootstrap markers are
   cleared on every subsequent restart, which re-opens the 300-second window
   each time.

Alternatively, you can delete the `bootstrap-consumed` and `bootstrap-claimed`
files from `/data/openclaw/` via the HA File editor add-on, then restart the
add-on.

### Verifying the Endpoint is Locked Down

From a machine outside the Supervisor network (e.g. the HA host via SSH):

```sh
# If the port is not exposed (expected), this should time out or be refused:
curl --max-time 5 http://<addon-hostname>:8099/v1/bootstrap

# Within the Supervisor network (e.g. from another add-on's shell):
# First fetch succeeds; second fetch returns 410.
```

A 404 response means the network-origin check rejected the caller.
A 410 response means the token was already consumed or the window closed.
