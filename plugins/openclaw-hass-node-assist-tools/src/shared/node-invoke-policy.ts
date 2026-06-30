// Node-invoke policy for openclaw-hass-node-assist-tools.
//
// Allows the plugin's own tool handlers to invoke the bound node's
// `ha.*` commands. Per-tool, per-node access checks live in the tool
// handlers themselves (using src/shared/glob-policy.ts against the
// per-node policy from src/shared/per-node-policy.ts); this policy is
// a top-level allow for the command names this plugin is concerned
// with.

import type { OpenClawPluginNodeInvokePolicy } from "openclaw/plugin-sdk/plugin-entry";
import { ASSIST_TOOLS_NODE_INVOKE_COMMANDS } from "./lazy-node-invoke-policy.js";

export function createAssistToolsNodeInvokePolicy(): OpenClawPluginNodeInvokePolicy {
  const allowed = new Set(ASSIST_TOOLS_NODE_INVOKE_COMMANDS);
  return {
    commands: [...ASSIST_TOOLS_NODE_INVOKE_COMMANDS],
    handle(ctx) {
      if (!allowed.has(ctx.command)) {
        return {
          ok: false,
          code: "COMMAND_NOT_ALLOWED",
          message: `openclaw-hass-node-assist-tools COMMAND_NOT_ALLOWED: ${ctx.command} is not in the plugin allowlist`,
        };
      }
      return { ok: true };
    },
  };
}
