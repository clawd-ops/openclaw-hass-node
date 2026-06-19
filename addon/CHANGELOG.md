# OpenClaw Node Add-on Changelog

## 2026.6.8a8 (2026-06-19)

### Bug fixes
- **#98 part 1 — `reset_pairing` add-on option (#99):** Add a `reset_pairing: bool` toggle. When `true`, on startup the node deletes the persisted `device_token` and device identity under `/data/openclaw`, then re-pairs with the bootstrap token from `pairing_token`. Set back to `false` after the node reconnects successfully. This is the user-facing escape hatch from `AUTH_TOKEN_MISMATCH` reconnect loops caused by stale persisted device tokens.
- **#98 part 2 — `node.pending.pull` response correlation (#100):** The drain step after a successful pair was reading the next WebSocket frame as the response, but the gateway may interleave events (e.g. `connect.approved`, queued `node.invoke.request`) before the actual response. That misread produced the `node.pending.pull failed: None` log line and tore down the otherwise-healthy session. Responses are now correlated by request id; events are dropped silently and the real response is matched.

### Dependencies
- `cryptography` bumped to `>=48.0.1` for GHSA-537c-gmf6-5ccf.

### Known issues
- **#98 part 3 (still open):** Dual-role reconnect may still send a stale device token from one role briefly after the other role pairs cleanly, until the file-reload cycle on the next reconnect catches up. With the pending.pull fix the timing window narrows significantly. If you still see one role looping `AUTH_TOKEN_MISMATCH` after a successful pair, toggle `reset_pairing: true` once to force a clean state.
