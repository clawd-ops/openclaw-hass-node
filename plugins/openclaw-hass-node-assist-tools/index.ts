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
import { normalizeAssistSessionKey, runWithCallerContext } from "./src/shared/caller-context.js";
import { ADMIN_TOOL_COMMANDS, beforeToolCall } from "./src/shared/node-approval.js";
import { createLazyAssistToolsNodeInvokePolicy } from "./src/shared/lazy-node-invoke-policy.js";
import {
  resolvedAssistCommandRegistrations,
  type AssistToolDescriptor,
} from "./src/tools/assist-command-registration.js";

// The bundled SDK stub in types/ predates per-run factory registration; the
// real host accepts `registerTool(factory, { name })` and passes the run's
// tool context (`sessionKey?: string`) to the factory. A factory returning
// null offers no tool for that run.
type ToolFactoryApi = {
  registerTool(
    factory: (toolContext: { sessionKey?: string }) => AnyAgentTool | null,
    opts: { name: string },
  ): void;
};

type HookApi = {
  on(
    hook: "before_tool_call",
    handler: (event: Parameters<typeof beforeToolCall>[0], ctx?: unknown) => ReturnType<typeof beforeToolCall>,
    opts: { matcher: string[] },
  ): void;
};

function createLazyTool(
  descriptor: AssistToolDescriptor,
  loadTool: () => Promise<AnyAgentTool>,
  sessionKey: unknown,
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
      // The host session key for this run reaches invokeHaCommand through the
      // async caller context (lookup hint only, never identity).
      return await runWithCallerContext(sessionKey, () =>
        tool.execute(toolCallId, args, signal, onUpdate),
      );
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
    // The host calls handlers as (event, ctx); pass only ctx.sessionKey, never ctx itself, so it cannot reach the clock param.
    (api as unknown as HookApi).on("before_tool_call", (event, ctx) => beforeToolCall(event, undefined, (ctx as { sessionKey?: unknown } | undefined)?.sessionKey), {
      matcher: ["nodes", ...Object.keys(ADMIN_TOOL_COMMANDS)],
    });
    for (const registration of resolvedAssistCommandRegistrations()) {
      (api as unknown as ToolFactoryApi).registerTool(
        (toolContext) => {
          // Offer ha_* tools only in Assist sessions; every other session
          // (main, chat, cron, sub-agent) uses the core nodes tool instead.
          const sessionKey = normalizeAssistSessionKey(toolContext?.sessionKey);
          return sessionKey === undefined
            ? null
            : createLazyTool(registration.descriptor, registration.loadTool, sessionKey);
        },
        { name: registration.descriptor.name },
      );
    }
  },
});
