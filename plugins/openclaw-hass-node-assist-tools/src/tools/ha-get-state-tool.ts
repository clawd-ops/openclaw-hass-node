// ha_get_state tool handler. Wraps ha.get_state on the bound hass node.
// Parameter validation only; access control is at the node layer.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import { HA_GET_STATE_TOOL_DESCRIPTOR } from "./descriptors.js";
import {
  invokeHaCommand,
  readGatewayCallOptions,
  readTrimmedString,
  resolveNodeAndPolicy,
} from "./node-tool-invoke.js";

export function createHaGetStateTool(): AnyAgentTool {
  return {
    ...HA_GET_STATE_TOOL_DESCRIPTOR,
    execute: async (_toolCallId, args) => {
      const params = args as Record<string, unknown>;
      const nodeIdentifier = readTrimmedString(params, "node");
      const entityId = readTrimmedString(params, "entity_id");
      if (!nodeIdentifier) throw new Error("node required");
      if (!entityId) throw new Error("entity_id required");

      const gatewayOpts = readGatewayCallOptions(params);
      const { nodeId, nodeDisplayName } = await resolveNodeAndPolicy({
        nodeIdentifier,
        gatewayOpts,
      });

      const payload = await invokeHaCommand({
        nodeId,
        command: "ha.get_state",
        commandParams: { entity_id: entityId },
        gatewayOpts,
      });

      return {
        content: [
          {
            type: "text",
            text:
              `${entityId} on ${nodeDisplayName} (${nodeId}):\n\n` +
              `${JSON.stringify(payload, null, 2)}`,
          },
        ],
      };
    },
  };
}
