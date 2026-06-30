// Lazy node-invoke policy for openclaw-hass-node-assist-tools.
//
// This plugin does not register its own node-host commands (the
// openclaw-hass-node-app already exposes the ha.* / fs.* command surface).
// Instead it registers a node-invoke policy that lets the plugin's tool
// handlers reach those commands via node.invoke against the bound node,
// gated by per-node config.
//
// Modeled on /app/extensions/file-transfer/src/shared/lazy-node-invoke-policy.ts.

import type { OpenClawPluginNodeInvokePolicy } from "openclaw/plugin-sdk/plugin-entry";

export const ASSIST_TOOLS_NODE_INVOKE_COMMANDS: ReadonlyArray<string> = [
  "ha.call_service",
  "ha.get_state",
  "ha.list_states",
  "ha.calendar_get_events",
  "ha.list_areas",
  "ha.list_devices",
  "ha.list_entity_registry",
];

type LoadAssistToolsNodeInvokePolicy = () => Promise<OpenClawPluginNodeInvokePolicy>;

const loadAssistToolsNodeInvokePolicy: LoadAssistToolsNodeInvokePolicy = async () => {
  const { createAssistToolsNodeInvokePolicy } = await import(
    "./node-invoke-policy.js"
  );
  return createAssistToolsNodeInvokePolicy();
};

export function createLazyAssistToolsNodeInvokePolicy(
  loadPolicy: LoadAssistToolsNodeInvokePolicy = loadAssistToolsNodeInvokePolicy,
): OpenClawPluginNodeInvokePolicy {
  let policyPromise: Promise<OpenClawPluginNodeInvokePolicy> | undefined;

  return {
    commands: [...ASSIST_TOOLS_NODE_INVOKE_COMMANDS],
    async handle(ctx) {
      let policy: OpenClawPluginNodeInvokePolicy;
      try {
        policyPromise ??= loadPolicy();
        policy = await policyPromise;
      } catch (error) {
        const message =
          error instanceof Error && error.message ? error.message : String(error);
        return {
          ok: false,
          code: "PLUGIN_POLICY_UNAVAILABLE",
          message: `openclaw-hass-node-assist-tools PLUGIN_POLICY_UNAVAILABLE: ${message}`,
          unavailable: true,
        };
      }
      return await policy.handle(ctx);
    },
  };
}
