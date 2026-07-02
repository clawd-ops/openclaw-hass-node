#!/usr/bin/env bash
# Install openclaw-hass-node-assist-tools locally as a workaround for two
# packaging limitations that block the standard `openclaw plugins install` path:
#
#   A) The plugin ships TypeScript source; the gateway expects compiled JS.
#   B) pnpm's workspace node_modules hoisting creates symlinks that escape
#      the plugin root, triggering the gateway's link-install safety scan.
#
# This script copies the plugin to a stable path outside the pnpm workspace,
# runs a self-contained `npm install` there, then links it into the gateway.
#
# Usage: bash scripts/install-plugin-local.sh [DEST_DIR]
#
#   DEST_DIR  Directory to copy the plugin into.
#             Default: ~/.openclaw/plugins/openclaw-hass-node-assist-tools
#
# See docs/known-workarounds.md for the full context and fix tracking.

set -euo pipefail

PLUGIN_NAME="openclaw-hass-node-assist-tools"
PLUGIN_SRC="$(cd "$(dirname "$0")/.." && pwd)/plugins/${PLUGIN_NAME}"
DEST_DIR="${1:-"${HOME}/.openclaw/plugins/${PLUGIN_NAME}"}"

echo "==> Installing ${PLUGIN_NAME} via local workaround"
echo "    Source : ${PLUGIN_SRC}"
echo "    Dest   : ${DEST_DIR}"

if [[ ! -d "${PLUGIN_SRC}" ]]; then
  echo "ERROR: plugin source not found at ${PLUGIN_SRC}" >&2
  exit 1
fi

# Step 1 — copy to a stable path outside the pnpm workspace so there are
# no workspace-managed symlinks in node_modules.
echo "==> Step 1/3 — copying plugin to ${DEST_DIR}"
rm -rf "${DEST_DIR}"
cp -r "${PLUGIN_SRC}" "${DEST_DIR}"

# Step 2 — install dependencies with npm (not pnpm) so node_modules is
# fully self-contained with no symlinks outside the plugin tree.
echo "==> Step 2/3 — npm install in ${DEST_DIR}"
(cd "${DEST_DIR}" && npm install --prefer-offline 2>&1)

# Step 3 — link-install into the gateway.
echo "==> Step 3/3 — openclaw plugins install --link ${DEST_DIR}"
openclaw plugins install --link "${DEST_DIR}"

echo ""
echo "Done. Restart the gateway for the plugin to take effect:"
echo "  openclaw gateway restart"
echo ""
echo "Then configure per-node policy in ~/.openclaw/openclaw.json."
echo "See plugins/${PLUGIN_NAME}/examples/policy-hass-starter.json for a starter config."
