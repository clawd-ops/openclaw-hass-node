// Per-node policy loader for openclaw-hass-node-assist-tools.
//
// The plugin's configSchema (see openclaw.plugin.json) places per-node
// policy under `nodes.<nodeId>`. Entity/service/calendar access control
// lives at the hass node's own tier/allowCommands + HA's auth layer —
// not here. The only plugin-scoped policy is the Tier B admin gate.

export type PerNodePolicy = {
  // Tier B admin gate. Both flags must be set to invoke reload_config /
  // addon_start / addon_stop / addon_restart. `adminToken` is forwarded
  // to the addon's admin_token check.
  allowAdminOps?: boolean;
  adminToken?: string;
};

export type AssistToolsPluginConfig = {
  nodes?: Record<string, PerNodePolicy>;
};

export function readPerNodePolicy(
  pluginConfig: unknown,
  nodeIdentifier: string,
): PerNodePolicy | undefined {
  if (!pluginConfig || typeof pluginConfig !== "object") return undefined;
  const cfg = pluginConfig as AssistToolsPluginConfig;
  if (!cfg.nodes || typeof cfg.nodes !== "object") return undefined;
  const direct = cfg.nodes[nodeIdentifier];
  if (direct) return direct;
  // Allow a wildcard '*' entry as a default policy applied to any node.
  return cfg.nodes["*"];
}
