// Shared implementation for the "metadata read" handlers:
//   ha_list_areas, ha_list_devices, ha_list_entity_registry,
//   and all simple bulk-read tools (ha_list_services, ha_get_config, etc.).
//
// Routing-only: no per-entity or per-node policy check required. Access
// control is delegated to the hass node's tier/allowCommands + HA auth.
// Only Tier B admin tools (ha-admin-tools.ts) require a policy entry.

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
  command: string;
  label: string;
  // Optional builder: pluck extra params from the tool args (e.g. slug,
  // lines) into the forwarded command params. Return {} for pure node-only
  // calls (list_areas etc).
  buildCommandParams?: (args: Record<string, unknown>) => Record<string, unknown>;
}): AnyAgentTool {
  return {
    ...input.descriptor,
    execute: async (_toolCallId, args) => {
      const params = args as Record<string, unknown>;
      const nodeIdentifier = readTrimmedString(params, "node");
      if (!nodeIdentifier) throw new Error("node required");

      const gatewayOpts = readGatewayCallOptions(params);
      const { nodeId, nodeDisplayName } = await resolveNodeAndPolicy({
        nodeIdentifier,
        gatewayOpts,
      });

      const commandParams = input.buildCommandParams
        ? input.buildCommandParams(params)
        : {};

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
              `${input.label} on ${nodeDisplayName} (${nodeId}):\n\n` +
              `${JSON.stringify(payload, null, 2)}`,
          },
        ],
      };
    },
  };
}
