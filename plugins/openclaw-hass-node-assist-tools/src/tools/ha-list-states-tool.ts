// ha_list_states tool handler — STUB. Real implementation lands in PR B.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import { HA_LIST_STATES_TOOL_DESCRIPTOR } from "./descriptors.js";

export function createHaListStatesTool(): AnyAgentTool {
  return {
    ...HA_LIST_STATES_TOOL_DESCRIPTOR,
    execute: async () => ({
      content: [
        {
          type: "text",
          text: "ha_list_states: NOT_YET_IMPLEMENTED (scheduled for PR B; descriptor + manifest entry are committed so the registry shape is complete).",
        },
      ],
      isError: true,
    }),
  };
}
