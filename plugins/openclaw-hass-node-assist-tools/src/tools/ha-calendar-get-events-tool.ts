// ha_calendar_get_events tool handler. Wraps ha.calendar_get_events on
// the bound hass node. Parameter validation only; access control is at
// the node layer (hass node tier/allowCommands + HA auth).

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import { HA_CALENDAR_GET_EVENTS_TOOL_DESCRIPTOR } from "./descriptors.js";
import {
  invokeHaCommand,
  readGatewayCallOptions,
  readTrimmedString,
  resolveNodeAndPolicy,
} from "./node-tool-invoke.js";

export function createHaCalendarGetEventsTool(): AnyAgentTool {
  return {
    ...HA_CALENDAR_GET_EVENTS_TOOL_DESCRIPTOR,
    execute: async (_toolCallId, args) => {
      const params = args as Record<string, unknown>;
      const nodeIdentifier = readTrimmedString(params, "node");
      const entityId = readTrimmedString(params, "entity_id");
      const startDateTime = readTrimmedString(params, "start_date_time");
      const endDateTime = readTrimmedString(params, "end_date_time");
      if (!nodeIdentifier) throw new Error("node required");
      if (!entityId) throw new Error("entity_id required");
      if (!startDateTime) throw new Error("start_date_time required");
      if (!endDateTime) throw new Error("end_date_time required");

      const gatewayOpts = readGatewayCallOptions(params);
      const { nodeId, nodeDisplayName } = await resolveNodeAndPolicy({
        nodeIdentifier,
        gatewayOpts,
      });

      const payload = await invokeHaCommand({
        nodeId,
        command: "ha.calendar_get_events",
        commandParams: {
          entity_id: entityId,
          start_date_time: startDateTime,
          end_date_time: endDateTime,
        },
        gatewayOpts,
      });

      return {
        content: [
          {
            type: "text",
            text:
              `${entityId} events on ${nodeDisplayName} (${nodeId}) ` +
              `[${startDateTime} → ${endDateTime}):\n\n` +
              `${JSON.stringify(payload, null, 2)}`,
          },
        ],
      };
    },
  };
}
