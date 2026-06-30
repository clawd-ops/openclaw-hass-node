// ha_get_state tool handler. Wraps ha.get_state on the bound hass node,
// gated by per-node allowReadEntities / denyReadEntities policy.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import { decideGlobPolicy } from "../shared/glob-policy.js";
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
      const { nodeId, nodeDisplayName, policy } = await resolveNodeAndPolicy({
        nodeIdentifier,
        gatewayOpts,
      });

      const decision = decideGlobPolicy({
        candidate: entityId,
        allow: policy?.allowReadEntities,
        deny: policy?.denyReadEntities,
        subject: "entity",
      });

      if (!decision.allowed) {
        return {
          content: [
            {
              type: "text",
              text:
                `Refused to read ${entityId} on ${nodeDisplayName} (${nodeId}): ${decision.reason}.\n` +
                `Configure plugins.entries.openclaw-hass-node-assist-tools.config.nodes.${nodeIdentifier}.allowReadEntities ` +
                `to allow it, or .denyReadEntities to keep it blocked.`,
            },
          ],
          isError: true,
        };
      }

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
