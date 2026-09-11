// openclaw-hass-node-assist-tools plugin entrypoint.
//
// Bridges HA Assist sessions (which lack the operator-only `nodes.invoke`
// tool) to the paired node command surface through scoped wrappers. The JSON
// registration contract is the executable source of truth consumed here and
// by the Python command-coverage generator.

import {
  definePluginEntry,
  type AnyAgentTool,
} from "openclaw/plugin-sdk/plugin-entry";
import { createLazyAssistToolsNodeInvokePolicy } from "./src/shared/lazy-node-invoke-policy.js";
import {
  resolvedAssistCommandRegistrations,
  type AssistToolDescriptor,
} from "./src/tools/assist-command-registration.js";

function createLazyTool(
  descriptor: AssistToolDescriptor,
  loadTool: () => Promise<AnyAgentTool>,
): AnyAgentTool {
  let toolPromise: Promise<AnyAgentTool> | undefined;
  const loadOnce = () => {
    toolPromise ??= loadTool();
    return toolPromise;
  };
  return {
    ...descriptor,
    async execute(toolCallId, args, signal, onUpdate) {
      const tool = await loadOnce();
      return await tool.execute(toolCallId, args, signal, onUpdate);
    },
  };
}

export default definePluginEntry({
  id: "openclaw-hass-node-assist-tools",
  name: "OpenClaw HA Node — Assist Tools",
  description:
    "Scoped tool wrappers so HA Assist sessions can operate the paired Home Assistant node without the operator-only nodes.invoke tool.",
  register(api) {
    api.registerNodeInvokePolicy(createLazyAssistToolsNodeInvokePolicy());
    for (const registration of resolvedAssistCommandRegistrations()) {
      api.registerTool(createLazyTool(registration.descriptor, registration.loadTool));
    }
  },
});
