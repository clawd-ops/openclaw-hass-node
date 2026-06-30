// ha_list_devices tool handler — STUB. Real implementation lands in PR B.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import { HA_LIST_DEVICES_TOOL_DESCRIPTOR } from "./descriptors.js";

export function createHaListDevicesTool(): AnyAgentTool {
  return {
    ...HA_LIST_DEVICES_TOOL_DESCRIPTOR,
    execute: async () => ({
      content: [
        {
          type: "text",
          text: "ha_list_devices: NOT_YET_IMPLEMENTED (scheduled for PR B).",
        },
      ],
      isError: true,
    }),
  };
}
