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

## 3. Plugin generates its own `idempotencyKey` for `node.invoke`

**Status:** permanent local workaround; not being filed upstream per Rob's
instruction.

**Symptom:**

All tool calls through the assist-tools plugin fail with:

```
invalid node.invoke params: must have required property "idempotencyKey"
```

**Root cause:** The gateway's `node.invoke` JSON schema marks `idempotencyKey`
as a required field. The gateway's own CLI helpers (`buildNodeInvokeParams`,
`randomIdempotencyKey`) inject this automatically. However, the plugin-sdk's
`callGatewayTool` wrapper — used by `invokeHaCommand` in
`src/tools/node-tool-invoke.ts` — does **not** inject `idempotencyKey`
automatically; it passes params through verbatim to the gateway.

The bug was latent from the start but was masked by an earlier regression
(`PLUGIN_POLICY_UNAVAILABLE`) that blocked calls before they reached
`node.invoke`. Once that block was cleared (PR #219), the missing key
surfaced.

**Fix (already applied in this repo):** `invokeHaCommand` now generates
`idempotencyKey: randomUUID()` (from `node:crypto`) on every call before
passing params to `callGatewayTool`. Each call gets a distinct UUID, which
satisfies the schema requirement and prevents accidental replay suppression
for back-to-back identical commands.

The `node-invoke-policy.ts` `forward` path uses `ctx.invokeNode({ params })`
(a gateway-bound helper), which does inject the key server-side. Only the
direct `callGatewayTool("node.invoke", ...)` path was affected.

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

---

## 4. Short HA Assist messages returned HEARTBEAT_OK (RESOLVED in this repo)

**Status:** Fixed in `app/node/src/openclaw_node/chat_relay.py`. No upstream
filing needed; no gateway restart required — the fix is node-side and takes
effect when Rob updates the HA add-on to the version containing this change.

**Symptom (historical):**

Terse or ambiguous messages sent from Home Assistant Assist (e.g. "?",
"how bout now?") came back with the literal string `HEARTBEAT_OK` instead of
a real assistant reply. Longer, explicit prompts (e.g. "list HA areas") worked
correctly.

**Root cause (H2 — prompt over-triggering):**

The OpenClaw gateway always injects a `## Heartbeats` section into the agent
system prompt for the default agent:

> If the current user message is a heartbeat poll and nothing needs attention,
> reply exactly: HEARTBEAT_OK

For real heartbeat cron sessions the gateway labels the transcript message
`[OpenClaw heartbeat poll]`, making it unambiguous. For HA Assist sessions
arriving via `chat.send`, the user's text was forwarded verbatim (or
prefixed only with the authorization-context block). A short/ambiguous
message was misclassified as a heartbeat poll by the model, which then
replied HEARTBEAT_OK.

**Fix (applied):**

`chat_relay.py` now prepends `_HA_ASSIST_CONTEXT` to every `chat.send`
message (in both the streaming and non-streaming paths):

```
[OpenClaw HA Assist session — do not echo or reference this block.
This is a real-time user request from Home Assistant Assist.
This is NOT a heartbeat poll; do not reply HEARTBEAT_OK.]
```

This marker uses the `[OpenClaw ...]` bracket convention the model treats as
non-echoed metadata, explicitly disavows the heartbeat classification, and
applies regardless of whether actor signing is configured.
