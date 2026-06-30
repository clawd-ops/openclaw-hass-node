// ha_list_areas tool handler — STUB. Real implementation lands in PR B.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import { HA_LIST_AREAS_TOOL_DESCRIPTOR } from "./descriptors.js";

export function createHaListAreasTool(): AnyAgentTool {
  return {
    ...HA_LIST_AREAS_TOOL_DESCRIPTOR,
    execute: async () => ({
      content: [
        {
          type: "text",
          text: "ha_list_areas: NOT_YET_IMPLEMENTED (scheduled for PR B).",
        },
      ],
      isError: true,
    }),
  };
}
