# Known Workarounds

Operational workarounds for issues that require a local fix today but have
a proper upstream fix tracked. Each entry includes exact commands, why the
issue occurs, and where the fix is tracked.

---

## 1. Plugin local install (packaging gap)

**Status:** tracked in `docs/TODO.md` item #37.

**Symptoms:**

- `openclaw plugins install <path>` fails — the gateway expects compiled JS
  but the plugin ships only TypeScript source (`dist/` does not exist).
- `openclaw plugins install --link <repo>/plugins/openclaw-hass-node-assist-tools`
  fails the gateway's safety scan — pnpm workspace hoisting creates symlinks
  in `node_modules` that resolve outside the plugin root.

**One-command workaround (from the repo root):**

```sh
bash scripts/install-plugin-local.sh
```

The script copies the plugin to `~/.openclaw/plugins/openclaw-hass-node-assist-tools`,
runs a self-contained `npm install` there (no pnpm symlinks), then calls
`openclaw plugins install --link` on the stable copy.

**Step-by-step if you prefer to run it manually:**

```sh
# 1. Copy to a stable path outside the pnpm workspace
cp -r ~/.openclaw/projects/openclaw-hass-node/plugins/openclaw-hass-node-assist-tools \
      ~/.openclaw/plugins/openclaw-hass-node-assist-tools

# 2. Install dependencies with npm (no pnpm workspace symlinks)
cd ~/.openclaw/plugins/openclaw-hass-node-assist-tools
npm install

# 3. Link-install into the gateway
openclaw plugins install --link ~/.openclaw/plugins/openclaw-hass-node-assist-tools
```

**After installing:**

```sh
# Restart the gateway so the plugin is loaded
openclaw gateway restart
```

Then configure the plugin (see
`plugins/openclaw-hass-node-assist-tools/examples/policy-hass-starter.json`
for a minimal routing-only example — only `nodeId: "hass"` is required;
no entity/service allowlists needed).

**Fix path:** add `"build": "tsc --outDir dist"` to the plugin's `package.json`
and compile in CI / the release workflow. Long-term, publish to npm so
`openclaw plugins install <package-name>` works from any fresh clone.

---

## 2. Plugin config cannot be patched via `gateway config.patch`

**Status:** upstream gateway bug; workaround is permanent until fixed upstream.

**Symptom:**

Patching `plugins.entries.openclaw-hass-node-assist-tools.config.nodes.*`
via `openclaw gateway config.patch` (or the MCP `config.patch` tool) is
rejected:

```
Error: protected node config path
```

**Root cause:** The gateway's path-protection guard was written for
`gateway.nodes.<id>.*` (the node registry). The pattern over-matches any
config key that contains `nodes.<something>`, including the plugin-internal
`config.nodes.*` sub-tree. Per Rob's instruction this is not being filed
upstream; route around it locally.

**Workaround — edit `~/.openclaw/openclaw.json` directly:**

```sh
# 1. Open and edit
$EDITOR ~/.openclaw/openclaw.json
# Set keys under plugins.entries.openclaw-hass-node-assist-tools.config.nodes

# 2. Validate
openclaw config validate

# 3. Restart for the new config to take effect
openclaw gateway restart
```

Do NOT use `gateway config.patch` for any path containing a `nodes.<id>`
segment beneath a plugin entry. Direct JSON edit + validate + restart is
the only safe path until the upstream guard is narrowed to `gateway.nodes.*`.

---

## Re-running the install after a plugin update

When a new version of the plugin ships:

```sh
# Re-run the install script — it removes the old copy before copying
bash scripts/install-plugin-local.sh

# Restart the gateway
openclaw gateway restart
```

If the plugin's command surface changed (tools added or removed), also update
the per-node policy in `~/.openclaw/openclaw.json` and restart again.
