// ha_logbook / ha_history handlers. Forwards to the node after
// param translation (entity_id/start/end → node shape).

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
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
      const { nodeId, nodeDisplayName } = await resolveNodeAndPolicy({
        nodeIdentifier,
        gatewayOpts,
      });

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
