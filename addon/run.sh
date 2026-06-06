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
    'GATEWAY_URL': data.get('gateway_url') or 'wss://oc.landry.me/ws',
    'PAIRING_TOKEN': data.get('pairing_token') or '',
    'NODE_NAME': data.get('node_name') or '',
}
for key, value in values.items():
    print(f"export {key}={shlex.quote(str(value))}")
PY
)"
fi

exec python -m openclaw_node
