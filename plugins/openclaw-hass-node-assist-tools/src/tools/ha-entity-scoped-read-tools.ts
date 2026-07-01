// ha_logbook / ha_history handlers. entity_id is optional; when provided
// it's gated through allowReadEntities/denyReadEntities; when omitted the
// call requires a non-empty allowReadEntities (bulk-read opt-in).

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import { decideGlobPolicy } from "../shared/glob-policy.js";
import {
  HA_HISTORY_TOOL_DESCRIPTOR,
  HA_LOGBOOK_TOOL_DESCRIPTOR,
} from "./descriptors.js";
import {
  invokeHaCommand,
  readGatewayCallOptions,
  readTrimmedString,
  resolveNodeAndPolicy,
} from "./node-tool-invoke.js";

function createEntityScopedReadTool(input: {
  descriptor: Pick<AnyAgentTool, "label" | "name" | "description" | "parameters">;
  command: "ha.logbook" | "ha.history";
  label: string;
}): AnyAgentTool {
  return {
    ...input.descriptor,
    execute: async (_toolCallId, args) => {
      const params = args as Record<string, unknown>;
      const nodeIdentifier = readTrimmedString(params, "node");
      if (!nodeIdentifier) throw new Error("node required");
      const entityId = readTrimmedString(params, "entity_id");
      const start = readTrimmedString(params, "start");
      const end = readTrimmedString(params, "end");

      const gatewayOpts = readGatewayCallOptions(params);
      const { nodeId, nodeDisplayName, policy } = await resolveNodeAndPolicy({
        nodeIdentifier,
        gatewayOpts,
      });

      if (entityId) {
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
                  `Refused ${input.command} for ${entityId} on ${nodeDisplayName} (${nodeId}): ${decision.reason}. ` +
                  `Configure plugins.entries.openclaw-hass-node-assist-tools.config.nodes.${nodeIdentifier}.allowReadEntities.`,
              },
            ],
            isError: true,
          };
        }
      } else {
        if (
          !policy?.allowReadEntities ||
          policy.allowReadEntities.length === 0
        ) {
          return {
            content: [
              {
                type: "text",
                text:
                  `Refused ${input.command} (no entity_id) on ${nodeDisplayName} (${nodeId}): ` +
                  `no allowReadEntities configured for this node.`,
              },
            ],
            isError: true,
          };
        }
      }

      // Translate Assist-shape params to the node's expected shape:
      //   ha.logbook  → { entity_id, start_time, end_time }
      //   ha.history  → { entity_ids: [entity_id], start_time, end_time }
      // The node's HA handler ignores `entity_id`/`start`/`end`; sending
      // those unfiltered would return the full history for the node.
      const commandParams: Record<string, unknown> = {};
      if (input.command === "ha.history") {
        if (entityId) commandParams.entity_ids = [entityId];
      } else if (entityId) {
        commandParams.entity_id = entityId;
      }
      if (start) commandParams.start_time = start;
      if (end) commandParams.end_time = end;

      const payload = await invokeHaCommand({
        nodeId,
        command: input.command,
        commandParams,
        gatewayOpts,
      });

      return {
        content: [
          {
            type: "text",
            text:
              `${input.label} on ${nodeDisplayName} (${nodeId}) ` +
              `(entity=${entityId || "*"}, start=${start || "-"}, end=${end || "-"}):\n\n` +
              `${JSON.stringify(payload, null, 2)}`,
          },
        ],
      };
    },
  };
}

export const createHaLogbookTool = (): AnyAgentTool =>
  createEntityScopedReadTool({
    descriptor: HA_LOGBOOK_TOOL_DESCRIPTOR,
    command: "ha.logbook",
    label: "Logbook",
  });

export const createHaHistoryTool = (): AnyAgentTool =>
  createEntityScopedReadTool({
    descriptor: HA_HISTORY_TOOL_DESCRIPTOR,
    command: "ha.history",
    label: "History",
  });

