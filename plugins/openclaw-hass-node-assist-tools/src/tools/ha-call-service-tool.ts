// ha_call_service tool handler. Wraps ha.call_service on the bound
// hass node. Parameter validation only; access control is at the node layer.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import { HA_CALL_SERVICE_TOOL_DESCRIPTOR } from "./descriptors.js";
import {
  invokeHaCommand,
  readGatewayCallOptions,
  readTrimmedString,
  resolveNodeAndPolicy,
} from "./node-tool-invoke.js";

type HaCallServiceArgs = {
  node: string;
  domain: string;
  service: string;
  target?: {
    entity_id?: string | string[];
    area_id?: string | string[];
    device_id?: string | string[];
  };
  service_data?: Record<string, unknown>;
};

export function createHaCallServiceTool(): AnyAgentTool {
  return {
    ...HA_CALL_SERVICE_TOOL_DESCRIPTOR,
    execute: async (_toolCallId, args) => {
      const params = args as Record<string, unknown>;
      const callArgs = args as HaCallServiceArgs;
      const nodeIdentifier = readTrimmedString(params, "node");
      const domain = readTrimmedString(params, "domain");
      const service = readTrimmedString(params, "service");
      if (!nodeIdentifier) throw new Error("node required");
      if (!domain) throw new Error("domain required");
      if (!service) throw new Error("service required");

      const gatewayOpts = readGatewayCallOptions(params);
      const { nodeId, nodeDisplayName } = await resolveNodeAndPolicy({
        nodeIdentifier,
        gatewayOpts,
      });

      const commandParams: Record<string, unknown> = {
        domain,
        service,
      };
      if (callArgs.target !== undefined) commandParams.target = callArgs.target;
      if (callArgs.service_data !== undefined) {
        commandParams.service_data = callArgs.service_data;
      }

      const payload = await invokeHaCommand({
        nodeId,
        command: "ha.call_service",
        commandParams,
        gatewayOpts,
      });

      return {
        content: [
          {
            type: "text",
            text:
              `Called ${domain}.${service} on ${nodeDisplayName} (${nodeId}).\n\n` +
              `--- payload ---\n${JSON.stringify(payload, null, 2)}`,
          },
        ],
      };
    },
  };
}
