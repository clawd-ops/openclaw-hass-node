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

// Resolves the policy for a node from its **canonical node ID only**.
//
// This parameter is deliberately a single canonical ID rather than a list of
// identifiers that may name the node. Display names are self-declared by the
// node and are not revalidated on reconnect, so they are not authorization
// identities: a caller-supplied alias must never select the policy entry that
// decides a Tier B lifecycle or admin operation. Accepting further identifiers
// here would let a permissive entry keyed by an alias shadow an explicit
// `nodes["<canonical-id>"]` deny, which is the escalation #322 describes.
// Keeping the signature narrow means that call shape cannot be reintroduced
// without changing this contract.
//
// The exact canonical entry is tried before the wildcard default, so a
// permissive `nodes["*"]` can never shadow an explicit per-ID deny.
export function readPerNodePolicy(
  pluginConfig: unknown,
  nodeId: string,
): PerNodePolicy | undefined {
  if (!pluginConfig || typeof pluginConfig !== "object") return undefined;
  const cfg = pluginConfig as AssistToolsPluginConfig;
  if (!cfg.nodes || typeof cfg.nodes !== "object") return undefined;
  // `*` is the default-policy key, never a canonical node ID. Excluded from
  // the exact lookup so the wildcard keeps a single meaning.
  if (nodeId !== "*") {
    const direct = cfg.nodes[nodeId];
    if (direct) return direct;
  }
  // Allow a wildcard '*' entry as a default policy applied to any node.
  return cfg.nodes["*"];
}
