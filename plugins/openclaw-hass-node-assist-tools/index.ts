// openclaw-hass-node-assist-tools plugin entrypoint.
//
// Bridges HA Assist sessions (which lack the operator-only `nodes.invoke`
// tool, per OC ≥ 2026.3.31 reduced trusted surface for node-originated
// sessions) to the paired openclaw-hass-node-app command surface, via
// scoped per-tool wrappers (`ha_call_service`, `ha_get_state`, ...).
//
// Modeled on OC core's file-transfer plugin (/app/extensions/file-transfer/).
//
// Chat / cron / sub-agent sessions do NOT require this plugin; they have
// `nodes.invoke` and use `openclaw-hass-node-skill` on top.

import {
  definePluginEntry,
  type AnyAgentTool,
} from "openclaw/plugin-sdk/plugin-entry";
import { createLazyAssistToolsNodeInvokePolicy } from "./src/shared/lazy-node-invoke-policy.js";
import {
  HA_CALENDAR_GET_EVENTS_TOOL_DESCRIPTOR,
  HA_CALL_SERVICE_TOOL_DESCRIPTOR,
  HA_GET_STATE_TOOL_DESCRIPTOR,
  HA_LIST_AREAS_TOOL_DESCRIPTOR,
  HA_LIST_DEVICES_TOOL_DESCRIPTOR,
  HA_LIST_ENTITY_REGISTRY_TOOL_DESCRIPTOR,
  HA_LIST_STATES_TOOL_DESCRIPTOR,
} from "./src/tools/descriptors.js";

type AssistToolDescriptor = Pick<
  AnyAgentTool,
  "label" | "name" | "description" | "parameters"
>;

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
    "Scoped tool wrappers (ha_call_service, ha_get_state, ...) so HA Assist sessions can operate the paired openclaw-hass-node-app without the operator-only nodes.invoke tool.",
  register(api) {
    api.registerNodeInvokePolicy(createLazyAssistToolsNodeInvokePolicy());

    api.registerTool(
      createLazyTool(HA_CALL_SERVICE_TOOL_DESCRIPTOR, async () => {
        const { createHaCallServiceTool } = await import(
          "./src/tools/ha-call-service-tool.js"
        );
        return createHaCallServiceTool();
      }),
    );

    api.registerTool(
      createLazyTool(HA_GET_STATE_TOOL_DESCRIPTOR, async () => {
        const { createHaGetStateTool } = await import(
          "./src/tools/ha-get-state-tool.js"
        );
        return createHaGetStateTool();
      }),
    );

    api.registerTool(
      createLazyTool(HA_LIST_STATES_TOOL_DESCRIPTOR, async () => {
        const { createHaListStatesTool } = await import(
          "./src/tools/ha-list-states-tool.js"
        );
        return createHaListStatesTool();
      }),
    );

    api.registerTool(
      createLazyTool(HA_CALENDAR_GET_EVENTS_TOOL_DESCRIPTOR, async () => {
        const { createHaCalendarGetEventsTool } = await import(
          "./src/tools/ha-calendar-get-events-tool.js"
        );
        return createHaCalendarGetEventsTool();
      }),
    );

    api.registerTool(
      createLazyTool(HA_LIST_AREAS_TOOL_DESCRIPTOR, async () => {
        const { createHaListAreasTool } = await import(
          "./src/tools/ha-list-areas-tool.js"
        );
        return createHaListAreasTool();
      }),
    );

    api.registerTool(
      createLazyTool(HA_LIST_DEVICES_TOOL_DESCRIPTOR, async () => {
        const { createHaListDevicesTool } = await import(
          "./src/tools/ha-list-devices-tool.js"
        );
        return createHaListDevicesTool();
      }),
    );

    api.registerTool(
      createLazyTool(HA_LIST_ENTITY_REGISTRY_TOOL_DESCRIPTOR, async () => {
        const { createHaListEntityRegistryTool } = await import(
          "./src/tools/ha-list-entity-registry-tool.js"
        );
        return createHaListEntityRegistryTool();
      }),
    );
  },
});
