# Packaging

> **Language:** Python 3.13+ for both packages. See `docs/QUALITY.md`
> for the strict-typing / Google docstrings / 100 % pytest gates.

## Repo layout (target)

```
openclaw-hass-node/
├── README.md
├── repository.yaml                  # HA add-on store descriptor
├── hacs.json                        # HACS descriptor for the shim
├── pyproject.toml                   # Root workspace (uv)
├── .github/workflows/ci.yaml        # Lint, type, test, coverage, build
├── .pre-commit-config.yaml
├── docs/
│   ├── PLAN.md
│   ├── STATUS.md
│   ├── COMMAND-SURFACE.md
│   ├── PACKAGING.md
│   ├── HA-CONFIG-EDITING.md
│   ├── BACKUPS.md
│   ├── PROCESS.md
│   ├── QUALITY.md
│   ├── RESEARCH-CONVERSATION-AGENT.md
│   └── RESEARCH-AGENT-BRIDGE-CONNECTIVITY.md
├── addon/
│   ├── config.yaml                  # HA add-on manifest
│   ├── Dockerfile                   # python:3.13-alpine base
│   ├── build.yaml                   # HA build args per arch
│   ├── rootfs/etc/s6-overlay/...    # s6 service definitions
│   └── icon.png / logo.png
├── node/                            # OpenClaw node (Python package)
│   ├── pyproject.toml
│   ├── src/openclaw_node/
│   │   ├── __init__.py
│   │   ├── __main__.py              # Detects add-on vs standalone
│   │   ├── gateway_ws.py            # Gateway WS client (role: node)
│   │   ├── pairing.py
│   │   ├── commands/
│   │   │   ├── fs.py
│   │   │   ├── system.py
│   │   │   ├── ha.py
│   │   │   ├── ha_config.py
│   │   │   ├── docs_lookup.py
│   │   │   └── assist.py
│   │   ├── ha_client.py             # HA REST + WS client
│   │   ├── backups.py               # /share/openclaw-backups/ store
│   │   └── propose.py               # node.propose → gateway broker
│   └── tests/
│       └── ... (mirrors src/, 100 % branch coverage)
└── custom_components/
    └── openclaw_gateway/            # HACS shim (Plan B)
        ├── __init__.py
        ├── manifest.json
        ├── config_flow.py
        ├── conversation.py          # ConversationEntity → POST to add-on
        ├── const.py
        └── strings.json
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
