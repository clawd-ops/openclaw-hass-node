// Per-node policy loader for openclaw-hass-node-assist-tools.
//
// The plugin's configSchema (see openclaw.plugin.json) places per-node
// policy under `nodes.<nodeId>`. Entity/service/calendar access control
// lives at the hass node's own tier/allowCommands + HA's auth layer —
// not here. The plugin-scoped Tier B gate has two levels:
//  - Lifecycle ops (addon_start/stop/restart/update): allowAdminOps only.
//  - Admin ops (reload_config, update_install): allowAdminOps + adminToken.

export type PerNodePolicy = {
  // Tier B gate. Must be true for both lifecycle and admin operations.
  allowAdminOps?: boolean;
  // Admin token forwarded for ha.reload_config and ha.update_install.
  // Not required or used for lifecycle ops (addon_start/stop/restart/update).
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
