// Shared implementation for the three "metadata read" handlers:
//   ha_list_areas, ha_list_devices, ha_list_entity_registry.
//
// These have no per-entity policy gate (HA doesn't return entity_id-keyed
// payloads for areas/devices, and the registry list is bulk metadata).
// We require the node to have *some* policy entry (or a wildcard '*'
// entry) as a coarse "plugin is bound to this node" opt-in.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import {
  invokeHaCommand,
  readGatewayCallOptions,
  readTrimmedString,
  resolveNodeAndPolicy,
} from "./node-tool-invoke.js";

type Descriptor = Pick<
  AnyAgentTool,
  "label" | "name" | "description" | "parameters"
>;

export function createHaMetadataReadTool(input: {
  descriptor: Descriptor;
  command: "ha.list_areas" | "ha.list_devices" | "ha.list_entity_registry";
  label: string;
}): AnyAgentTool {
  return {
    ...input.descriptor,
    execute: async (_toolCallId, args) => {
      const params = args as Record<string, unknown>;
      const nodeIdentifier = readTrimmedString(params, "node");
      if (!nodeIdentifier) throw new Error("node required");

      const gatewayOpts = readGatewayCallOptions(params);
      const { nodeId, nodeDisplayName, policy } = await resolveNodeAndPolicy({
        nodeIdentifier,
        gatewayOpts,
      });

      if (!policy) {
        return {
          content: [
            {
              type: "text",
              text:
                `Refused ${input.command} on ${nodeDisplayName} (${nodeId}): ` +
                `no per-node policy configured. ` +
                `Add plugins.entries.openclaw-hass-node-assist-tools.config.nodes.${nodeIdentifier} ` +
                `(or a '*' default entry) to opt this node in.`,
            },
          ],
          isError: true,
        };
      }

      const payload = await invokeHaCommand({
        nodeId,
        command: input.command,
        commandParams: {},
        gatewayOpts,
      });

      return {
        content: [
          {
            type: "text",
            text:
              `${input.label} on ${nodeDisplayName} (${nodeId}):\n\n` +
              `${JSON.stringify(payload, null, 2)}`,
          },
        ],
      };
    },
  };
}
