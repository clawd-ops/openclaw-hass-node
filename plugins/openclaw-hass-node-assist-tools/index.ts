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
  HA_ADDON_CHANGELOG_TOOL_DESCRIPTOR,
  HA_ADDON_DOCUMENTATION_TOOL_DESCRIPTOR,
  HA_ADDON_INFO_TOOL_DESCRIPTOR,
  HA_ADDON_LOGS_TOOL_DESCRIPTOR,
  HA_ADDON_RESTART_TOOL_DESCRIPTOR,
  HA_ADDON_START_TOOL_DESCRIPTOR,
  HA_ADDON_STATS_TOOL_DESCRIPTOR,
  HA_ADDON_STOP_TOOL_DESCRIPTOR,
  HA_ADDON_UPDATE_TOOL_DESCRIPTOR,
  HA_UPDATE_INSTALL_TOOL_DESCRIPTOR,
  HA_CALENDAR_GET_EVENTS_TOOL_DESCRIPTOR,
  HA_CALL_SERVICE_TOOL_DESCRIPTOR,
  HA_CHECK_CONFIG_TOOL_DESCRIPTOR,
  HA_CORE_LOGS_TOOL_DESCRIPTOR,
  HA_GET_CONFIG_TOOL_DESCRIPTOR,
  HA_GET_STATE_TOOL_DESCRIPTOR,
  HA_HISTORY_TOOL_DESCRIPTOR,
  HA_LIGHT_TURN_OFF_TOOL_DESCRIPTOR,
  HA_LIGHT_TURN_ON_TOOL_DESCRIPTOR,
  HA_LIST_ADDONS_TOOL_DESCRIPTOR,
  HA_LIST_AREAS_TOOL_DESCRIPTOR,
  HA_LIST_AUTOMATIONS_TOOL_DESCRIPTOR,
  HA_LIST_CONFIG_ENTRIES_TOOL_DESCRIPTOR,
  HA_LIST_DEVICES_TOOL_DESCRIPTOR,
  HA_LIST_ENTITY_REGISTRY_TOOL_DESCRIPTOR,
  HA_LIST_EVENTS_TOOL_DESCRIPTOR,
  HA_LIST_SERVICES_TOOL_DESCRIPTOR,
  HA_LIST_STATES_TOOL_DESCRIPTOR,
  HA_LOGBOOK_TOOL_DESCRIPTOR,
  HA_RELOAD_CONFIG_TOOL_DESCRIPTOR,
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

    // --- simple bulk / metadata reads (bound-node check only) ---
    const simpleReadRegs: Array<
      [AssistToolDescriptor, keyof typeof import("./src/tools/ha-simple-read-tools.js")]
    > = [
      [HA_LIST_SERVICES_TOOL_DESCRIPTOR, "createHaListServicesTool"],
      [HA_GET_CONFIG_TOOL_DESCRIPTOR, "createHaGetConfigTool"],
      [HA_LIST_EVENTS_TOOL_DESCRIPTOR, "createHaListEventsTool"],
      [HA_LIST_CONFIG_ENTRIES_TOOL_DESCRIPTOR, "createHaListConfigEntriesTool"],
      [HA_LIST_AUTOMATIONS_TOOL_DESCRIPTOR, "createHaListAutomationsTool"],
      [HA_CHECK_CONFIG_TOOL_DESCRIPTOR, "createHaCheckConfigTool"],
      [HA_CORE_LOGS_TOOL_DESCRIPTOR, "createHaCoreLogsTool"],
      [HA_ADDON_LOGS_TOOL_DESCRIPTOR, "createHaAddonLogsTool"],
      [HA_LIST_ADDONS_TOOL_DESCRIPTOR, "createHaListAddonsTool"],
      [HA_ADDON_INFO_TOOL_DESCRIPTOR, "createHaAddonInfoTool"],
      [HA_ADDON_STATS_TOOL_DESCRIPTOR, "createHaAddonStatsTool"],
      [HA_ADDON_CHANGELOG_TOOL_DESCRIPTOR, "createHaAddonChangelogTool"],
      [HA_ADDON_DOCUMENTATION_TOOL_DESCRIPTOR, "createHaAddonDocumentationTool"],
    ];
    for (const [descriptor, factoryName] of simpleReadRegs) {
      api.registerTool(
        createLazyTool(descriptor, async () => {
          const mod = await import("./src/tools/ha-simple-read-tools.js");
          return mod[factoryName]();
        }),
      );
    }

    // --- entity-scoped reads (logbook / history) ---
    api.registerTool(
      createLazyTool(HA_LOGBOOK_TOOL_DESCRIPTOR, async () => {
        const { createHaLogbookTool } = await import(
          "./src/tools/ha-entity-scoped-read-tools.js"
        );
        return createHaLogbookTool();
      }),
    );
    api.registerTool(
      createLazyTool(HA_HISTORY_TOOL_DESCRIPTOR, async () => {
        const { createHaHistoryTool } = await import(
          "./src/tools/ha-entity-scoped-read-tools.js"
        );
        return createHaHistoryTool();
      }),
    );

    // --- convenience light actions (allowServices-gated) ---
    api.registerTool(
      createLazyTool(HA_LIGHT_TURN_ON_TOOL_DESCRIPTOR, async () => {
        const { createHaLightTurnOnTool } = await import(
          "./src/tools/ha-light-tools.js"
        );
        return createHaLightTurnOnTool();
      }),
    );
    api.registerTool(
      createLazyTool(HA_LIGHT_TURN_OFF_TOOL_DESCRIPTOR, async () => {
        const { createHaLightTurnOffTool } = await import(
          "./src/tools/ha-light-tools.js"
        );
        return createHaLightTurnOffTool();
      }),
    );

    // --- Tier B admin (allowAdminOps + adminToken gated) ---
    api.registerTool(
      createLazyTool(HA_RELOAD_CONFIG_TOOL_DESCRIPTOR, async () => {
        const { createHaReloadConfigTool } = await import(
          "./src/tools/ha-admin-tools.js"
        );
        return createHaReloadConfigTool();
      }),
    );
    api.registerTool(
      createLazyTool(HA_ADDON_START_TOOL_DESCRIPTOR, async () => {
        const { createHaAddonStartTool } = await import(
          "./src/tools/ha-admin-tools.js"
        );
        return createHaAddonStartTool();
      }),
    );
    api.registerTool(
      createLazyTool(HA_ADDON_STOP_TOOL_DESCRIPTOR, async () => {
        const { createHaAddonStopTool } = await import(
          "./src/tools/ha-admin-tools.js"
        );
        return createHaAddonStopTool();
      }),
    );
    api.registerTool(
      createLazyTool(HA_ADDON_RESTART_TOOL_DESCRIPTOR, async () => {
        const { createHaAddonRestartTool } = await import(
          "./src/tools/ha-admin-tools.js"
        );
        return createHaAddonRestartTool();
      }),
    );
    api.registerTool(
      createLazyTool(HA_ADDON_UPDATE_TOOL_DESCRIPTOR, async () => {
        const { createHaAddonUpdateTool } = await import(
          "./src/tools/ha-admin-tools.js"
        );
        return createHaAddonUpdateTool();
      }),
    );
    api.registerTool(
      createLazyTool(HA_UPDATE_INSTALL_TOOL_DESCRIPTOR, async () => {
        const { createHaUpdateInstallTool } = await import(
          "./src/tools/ha-admin-tools.js"
        );
        return createHaUpdateInstallTool();
      }),
    );
  },
});
