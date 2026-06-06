# Packaging

## Repo layout (target)

```
openclaw-hass-node/
├── README.md
├── repository.yaml              # HA add-on store descriptor
├── docs/
│   ├── PLAN.md
│   ├── STATUS.md
│   ├── COMMAND-SURFACE.md
│   └── PACKAGING.md
├── addon/
│   ├── config.yaml              # HA add-on manifest
│   ├── Dockerfile               # Built for amd64/aarch64/armv7
│   ├── build.yaml               # HA build args per arch
│   ├── rootfs/                  # s6-overlay scripts
│   │   └── etc/s6-overlay/...
│   └── icon.png / logo.png
├── node/                        # OpenClaw node implementation
│   ├── package.json or pyproject.toml
│   ├── src/
│   │   ├── entrypoint.*         # Detects add-on vs standalone
│   │   ├── gateway-ws.*         # Gateway WS client (role: node)
│   │   ├── pairing.*
│   │   ├── commands/
│   │   │   ├── fs.*
│   │   │   ├── system.*
│   │   │   ├── ha.*
│   │   │   └── assist.*
│   │   ├── ha-client.*          # HA REST + WS client
│   │   └── agent-bridge.*       # Proposal emit + wait
│   └── tests/
└── hacs-shim/                   # Only if Plan B required
    ├── custom_components/openclaw_assist/
    │   ├── __init__.py
    │   ├── manifest.json
    │   ├── conversation.py      # AbstractConversationAgent → POST to add-on
    │   └── config_flow.py
    └── hacs.json
```

## Add-on `config.yaml` sketch

```yaml
name: OpenClaw Node
version: 0.1.0
slug: openclaw_node
description: OpenClaw gateway peripheral for Home Assistant
arch:
  - amd64
  - aarch64
  - armv7
init: false
hassio_api: true
hassio_role: admin
homeassistant_api: true
auth_api: true
map:
  - config:rw
  - share:rw
  - ssl:rw
  - addons:rw
  - media:rw
  - backup:rw
options:
  gateway_url: "wss://oc.landry.me/ws"
  pairing_token: ""
  node_name: ""
schema:
  gateway_url: url
  pairing_token: password
  node_name: str?
ingress: false
panel_icon: mdi:robot-happy
```

## Standalone Docker (same image)

```
docker run -d --name openclaw-hass-node \
  -e HASS_URL=http://homeassistant.local:8123 \
  -e HASS_TOKEN=... \
  -e GATEWAY_URL=wss://oc.landry.me/ws \
  -e PAIRING_TOKEN=... \
  -v /opt/hass/config:/config \
  -v /opt/hass/share:/share \
  ghcr.io/roblandry/openclaw-hass-node:latest
```

Entrypoint detects mode:
- `SUPERVISOR_TOKEN` present → add-on mode, talk to `http://supervisor/`
  and `http://homeassistant/`.
- Else → standalone, use `HASS_URL` + `HASS_TOKEN`.

## Implementation language

Open. Candidates:
- **TypeScript/Node** — matches existing OpenClaw node tooling, easy
  WS, npm reuse.
- **Python** — easier interop with HA Python ecosystem; useful if Plan B
  HACS shim has to share code.

Pick during P2 based on which OpenClaw node SDK is most mature.
Default lean: TypeScript unless the HACS shim grows shared logic.
