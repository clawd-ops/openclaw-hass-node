// ha_list_entity_registry tool handler — STUB. Real implementation lands in PR B.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import { HA_LIST_ENTITY_REGISTRY_TOOL_DESCRIPTOR } from "./descriptors.js";

export function createHaListEntityRegistryTool(): AnyAgentTool {
  return {
    ...HA_LIST_ENTITY_REGISTRY_TOOL_DESCRIPTOR,
    execute: async () => ({
      content: [
        {
          type: "text",
          text: "ha_list_entity_registry: NOT_YET_IMPLEMENTED (scheduled for PR B).",
        },
      ],
      isError: true,
    }),
  };
}
