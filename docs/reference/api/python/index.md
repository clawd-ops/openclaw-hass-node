# Python API Reference

Auto-generated from Google-style docstrings in `app/node/src/openclaw_node/`.

## Package layout

| Module | Purpose |
|--------|---------|
| `openclaw_node` | Package root and version |
| `openclaw_node.config` | Environment-driven configuration |
| `openclaw_node.authz` | Authorization / scope enforcement |
| `openclaw_node.identity` | Device identity and Ed25519 keypair |
| `openclaw_node.pairing` | Gateway pairing handshake |
| `openclaw_node.token_store` | Persistent token storage |
| `openclaw_node.gateway_ws` | Gateway WebSocket relay |
| `openclaw_node.ha_client` | Home Assistant REST / WS client |
| `openclaw_node.http_api` | Internal HTTP API server |
| `openclaw_node.chat_relay` | HA Assist conversation relay |
| `openclaw_node.backup_store` | Config backup store |
| `openclaw_node.safe_fd` | File-descriptor safety helpers |
| `openclaw_node.safe_path` | Path validation helpers |
| `openclaw_node.commands` | Command handler subpackage |
