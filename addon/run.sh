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
# reset_pairing: the add-on UI exposes a bool toggle (true => "token" mode,
# false => "none"). Power users can set OPENCLAW_RESET_PAIRING=identity in
# the container environment (e.g. via a host-side override of the add-on's
# env) to request a full identity wipe. Only emit the export when the
# options-derived value is set; otherwise leave any pre-existing env var
# untouched so the identity-mode override survives.
reset_pairing_raw = data.get('reset_pairing')
# Only override OPENCLAW_RESET_PAIRING when the option asks for an actual
# wipe. Treat all no-op forms (bool False, "", "none", "false") as
# "do nothing AND do not clobber any out-of-band env override". This also
# handles the short-lived a9 string schema where users may have saved
# "none" before the schema reverted to bool? in a10.
_NOOP_FORMS = {'', 'none', 'false'}
if isinstance(reset_pairing_raw, str):
    if reset_pairing_raw.strip().casefold() not in _NOOP_FORMS:
        values['OPENCLAW_RESET_PAIRING'] = reset_pairing_raw
elif reset_pairing_raw is True:
    values['OPENCLAW_RESET_PAIRING'] = 'true'
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
