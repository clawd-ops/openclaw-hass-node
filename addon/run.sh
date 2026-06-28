#!/usr/bin/with-contenv sh
# Entrypoint for the HA add-on. Reads options from /data/options.json using
# Python (already present in the image) and exports them before launching the
# node process.
#
# The `#!/usr/bin/with-contenv sh` shebang (vs the previous `#!/usr/bin/env sh`)
# is what makes Supervisor's injected env vars — most importantly
# SUPERVISOR_TOKEN — visible to this script. Without with-contenv the s6
# container-environment overlay isn't applied, and `os.environ.get("SUPERVISOR_TOKEN")`
# inside the Python process returns None, surfacing as HA_NOT_CONFIGURED on
# every ha.* invoke (Issue #109; verified 2026-06-19 fix). The
# `hassio_api`/`homeassistant_api` flags in config.yaml are necessary but
# not sufficient — Supervisor only sets the env, it's the entrypoint
# shebang that brings it through to user code.

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
identity = data.get('identity') or {}
if isinstance(identity, dict):
    super_admins = identity.get('super_admins') or []
    if isinstance(super_admins, list):
        values['OPENCLAW_IDENTITY_SUPER_ADMINS'] = json.dumps(super_admins)
    # user_agent_map is configured as a list of {ha_username, agent_id}
    # objects (the HA addon-schema canonical shape for arbitrary
    # key-value maps; raw `dict?` is not in Supervisor's validator set).
    # The addon's internal contract is still dict[str, str], so flatten
    # the list to a dict here before serializing to the env var.
    user_agent_map = identity.get('user_agent_map') or []
    if isinstance(user_agent_map, list):
        flat = {}
        for entry in user_agent_map:
            if not isinstance(entry, dict):
                continue
            ha_user = entry.get('ha_username')
            agent_id = entry.get('agent_id')
            if isinstance(ha_user, str) and isinstance(agent_id, str) and ha_user.strip() and agent_id.strip():
                flat[ha_user.strip()] = agent_id.strip()
        if flat:
            values['OPENCLAW_IDENTITY_USER_AGENT_MAP'] = json.dumps(flat)
    default_agent_id = identity.get('default_agent_id') or ''
    if default_agent_id:
        values['OPENCLAW_IDENTITY_DEFAULT_AGENT_ID'] = str(default_agent_id)
    forbidden_commands = identity.get('forbidden_commands') or ''
    if isinstance(forbidden_commands, dict):
        values['OPENCLAW_IDENTITY_FORBIDDEN_COMMANDS'] = json.dumps(forbidden_commands)
    elif isinstance(forbidden_commands, str) and forbidden_commands.strip():
        values['OPENCLAW_IDENTITY_FORBIDDEN_COMMANDS'] = forbidden_commands.strip()
addon_lifecycle = data.get('addon_lifecycle') or {}
if isinstance(addon_lifecycle, dict):
    allowlist = addon_lifecycle.get('allowlist') or []
    if isinstance(allowlist, list):
        values['OPENCLAW_ADDON_LIFECYCLE_ALLOWLIST'] = json.dumps(allowlist)
    denylist = addon_lifecycle.get('denylist') or []
    if isinstance(denylist, list):
        values['OPENCLAW_ADDON_LIFECYCLE_DENYLIST'] = json.dumps(denylist)
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

# Surface Supervisor-injection status so future regressions of Issue #109
# (or a different `with-contenv` failure mode) are visible at first glance
# in the addon log without leaking the token value itself.
if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
  echo "[run.sh] SUPERVISOR_TOKEN injected (len=${#SUPERVISOR_TOKEN})"
else
  echo "[run.sh] WARNING: SUPERVISOR_TOKEN NOT set — ha.* calls will fail HA_NOT_CONFIGURED" \
       "unless hass_token+hass_url are configured. Check the with-contenv shebang." >&2
fi

exec python -m openclaw_node
