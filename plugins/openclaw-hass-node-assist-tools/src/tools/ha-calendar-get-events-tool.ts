// ha_calendar_get_events tool handler — STUB. Real implementation lands in PR B.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import { HA_CALENDAR_GET_EVENTS_TOOL_DESCRIPTOR } from "./descriptors.js";

export function createHaCalendarGetEventsTool(): AnyAgentTool {
  return {
    ...HA_CALENDAR_GET_EVENTS_TOOL_DESCRIPTOR,
    execute: async () => ({
      content: [
        {
          type: "text",
          text: "ha_calendar_get_events: NOT_YET_IMPLEMENTED (scheduled for PR B).",
        },
      ],
      isError: true,
    }),
  };
}
