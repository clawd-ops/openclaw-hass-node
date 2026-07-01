// ha_calendar_get_events tool handler. Wraps ha.calendar_get_events on
// the bound hass node, gated by per-node allowCalendars (exact entity_id
// match; missing/empty = deny all).

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
      const { nodeId, nodeDisplayName, policy } = await resolveNodeAndPolicy({
        nodeIdentifier,
        gatewayOpts,
      });

      const allowed = policy?.allowCalendars ?? [];
      if (!allowed.includes(entityId)) {
        return {
          content: [
            {
              type: "text",
              text:
                `Refused to read ${entityId} on ${nodeDisplayName} (${nodeId}): ` +
                `not in allowCalendars. ` +
                `Configure plugins.entries.openclaw-hass-node-assist-tools.config.nodes.${nodeIdentifier}.allowCalendars.`,
            },
          ],
          isError: true,
        };
      }

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
