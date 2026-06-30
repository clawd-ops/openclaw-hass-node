// Per-node policy loader for openclaw-hass-node-assist-tools.
//
// The plugin's configSchema (see openclaw.plugin.json) places per-node
// policy under `nodes.<nodeId>`. Each entry can declare:
//   - allowServices / denyServices (globs over '<domain>.<service>')
//   - allowReadEntities / denyReadEntities (globs over entity_id)
//   - allowCalendars (exact entity_ids)
//   - ask: 'off' | 'on-miss' | 'always'   (reserved for future use)

export type PerNodePolicy = {
  ask?: "off" | "on-miss" | "always";
  allowServices?: ReadonlyArray<string>;
  denyServices?: ReadonlyArray<string>;
  allowReadEntities?: ReadonlyArray<string>;
  denyReadEntities?: ReadonlyArray<string>;
  allowCalendars?: ReadonlyArray<string>;
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
