# Packaging

> **Language:** Python 3.13+ for both packages. See `docs/CONTRIBUTING.md`
> for the CI gates (ruff check + format, mypy strict, pytest with 95%
> branch coverage).

## Repo layout

```
openclaw-hass-node/
├── README.md
├── repository.yaml                  # HA add-on (app) store descriptor
├── hacs.json                        # HACS descriptor for the shim
├── docs/
│   ├── PLAN.md
│   ├── STATUS.md
│   ├── COMMAND-SURFACE.md
│   ├── PACKAGING.md                 # (this file)
│   ├── INSTALL.md
│   ├── OVERVIEW.md
│   ├── CONTRIBUTING.md
│   ├── RELEASE.md
│   ├── UAT-PLAN.md
│   ├── LESSONS.md
│   ├── HA-CONFIG-EDITING.md
│   ├── BACKUPS.md
│   ├── RESEARCH-CONVERSATION-AGENT.md
│   └── RESEARCH-OPENCLAW-INTEGRATION.md
├── addon/                           # Build context for Supervisor
│   ├── config.yaml                  # HA add-on (app) manifest
│   ├── Dockerfile                   # HA per-arch Python base
│   ├── build.yaml                   # Per-arch BUILD_FROM + labels
│   ├── run.sh                       # Entrypoint (exports env, runs node)
│   ├── icon.png / logo.png
│   └── node/                        # OpenClaw node (Python package)
│       ├── pyproject.toml
│       ├── src/openclaw_node/
│       │   ├── __init__.py          # Version + importlib.metadata
│       │   ├── __main__.py          # Detects add-on (app) vs standalone
│       │   ├── config.py            # Env-driven configuration
│       │   ├── identity.py          # Ed25519 device identity
│       │   ├── gateway_ws.py        # Gateway WS client (role: node)
│       │   ├── ha_client.py         # HA REST + WS client
│       │   ├── http_api.py          # Local HTTP API (bearer-gated)
│       │   ├── safe_fd.py           # TOCTOU-safe fd primitives
│       │   ├── backup_store.py      # Content-addressed backup store
│       │   └── commands/
│       │       ├── dispatcher.py    # Command registry (28 commands)
│       │       ├── ping.py
│       │       ├── fs.py            # fs.read/list/stat/glob
│       │       ├── fs_write.py      # fs.write/restore/history/diff
│       │       ├── fs_patch.py      # fs.patch
│       │       ├── fs_move_delete.py # fs.move/delete
│       │       ├── ha.py            # 15 ha.* commands
│       │       ├── system.py        # system.which
│       │       └── system_run.py    # system.run (admin-gated)
│       └── tests/
│           └── ...                  # 95%+ branch coverage
└── custom_components/
    └── openclaw_gateway/            # HACS integration (conversation shim)
        ├── __init__.py
        ├── manifest.json
        ├── config_flow.py
        ├── conversation.py          # ConversationEntity -> POST to add-on
        ├── const.py
        └── strings.json
```

## Add-on (App) `config.yaml`

Current shipped values (see `addon/config.yaml` for the live file):

```yaml
name: OpenClaw Node
version: "2026.6.8a7"
slug: openclaw_hass_node
arch: [amd64, aarch64, armv7]
init: false
# Least-privilege API surface — no Supervisor API access, no HA Auth API
# (the local HTTP API authenticates with `local_api_token` directly via
# `hmac.compare_digest`, not via HA-issued tokens).
homeassistant_api: true
# `hassio_api: true`, `hassio_role: admin`, and `auth_api: true` were all
# removed in PR #68/#69 — nothing shipped today calls those Supervisor
# surfaces. Re-add only when a real shipped feature needs them.
map:
  - config:rw    # fs.* mutations gated by software _is_protected("/config")
                 # → PROPOSAL_REQUIRED before any write/rename/unlink
  - share:rw    # backups + delete-trash store
  - media:rw    # generic fs.* write root
# `ssl:ro`, `addons:ro`, `backup:ro` removed — no shipped feature consumes
# them and they leak sensitive material via the generic fs.read surface.
options:
  gateway_url: "wss://gateway.example.com/ws"
  pairing_token: ""
  node_name: ""
  local_api_token: ""    # shared bearer for the local HTTP API (HACS shim)
  hass_url: ""
  hass_token: ""
ingress: false
```

No host port mapping. The local HTTP API is only reachable inside the
Supervisor add-on network by default. The local API is fail-closed:
non-public paths require `Authorization: Bearer <local_api_token>`. When
the option is unset, those paths return `401 NO_TOKEN_CONFIGURED`. Only
`/health`, `/v1/health`, and `/v1/conversation/info` are reachable
without a token (HA add-on probe + HACS shim config-flow discovery).

## Docker base image

The Dockerfile uses Home Assistant's per-arch Python base images
(e.g. `ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.20`),
set via `BUILD_FROM` in `build.yaml`. Supervisor requires these HA
base images; a bare `python:3.13-alpine` is silently ignored by
Supervisor and causes a `pip: not found` failure. The Dockerfile
default matches the amd64 variant for standalone Docker builds.

## Standalone Docker

```bash
docker build -t openclaw-hass-node:dev addon/

docker run -d --name openclaw-hass-node \
  -e HASS_URL=http://homeassistant.local:8123 \
  -e HASS_TOKEN=... \
  -e GATEWAY_URL=wss://gateway.example.com/ws \
  -e PAIRING_TOKEN=... \
  -e OPENCLAW_LOCAL_API_TOKEN=... \
  -v openclaw-data:/data \
  openclaw-hass-node:dev
```

Entrypoint detects mode:
- `SUPERVISOR_TOKEN` present -> add-on (app) mode, talks to
  `http://supervisor/` and `http://homeassistant/`.
- Else -> standalone, uses `HASS_URL` + `HASS_TOKEN`.

## Versioning

PEP 440 date-based: `YYYY.M.Da<N>` during alpha (e.g. `2026.6.8a1`).
Post-beta: `YYYY.M.PATCH`. Version is enforced by
`test_version_sync.py` across 5 sources: `pyproject.toml`,
`addon/config.yaml`, `addon/build.yaml` label, `manifest.json`, and
the source-literal fallback in `__init__.py`.

See `docs/RELEASE.md` for the release process and changelog plan.
