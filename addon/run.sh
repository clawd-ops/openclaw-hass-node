#!/usr/bin/env sh
# Entrypoint for the HA add-on. Reads options from /data/options.json using
# Python (already present in the image) and exports them before launching the
# node process.

set -eu

CONFIG_PATH=/data/options.json

if [ -f "$CONFIG_PATH" ]; then
  eval "$(python - <<'PY'
import json
import shlex
from pathlib import Path

path = Path('/data/options.json')
try:
    data = json.loads(path.read_text(encoding='utf-8'))
except Exception:
    data = {}

values = {
    'GATEWAY_URL': data.get('gateway_url') or 'wss://gateway.example.com/ws',
    'PAIRING_TOKEN': data.get('pairing_token') or '',
    'NODE_NAME': data.get('node_name') or '',
    'OPENCLAW_LOCAL_API_TOKEN': data.get('local_api_token') or '',
}
# Optional HA credentials fallback. ha_client.py already reads HASS_URL
# and HASS_TOKEN from env when SUPERVISOR_TOKEN is missing; surface them
# only when the user actually filled the option in, so we don't clobber a
# valid Supervisor-injected setup with an empty string.
hass_url = data.get('hass_url') or ''
if hass_url:
    values['HASS_URL'] = hass_url
hass_token = data.get('hass_token') or ''
if hass_token:
    values['HASS_TOKEN'] = hass_token
for key, value in values.items():
    print(f"export {key}={shlex.quote(str(value))}")
PY
)"
fi

exec python -m openclaw_node
