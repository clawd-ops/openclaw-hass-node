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
  data?: Record<string, unknown>;
};

const ALLOWED_CALL_SERVICE_ARGS = new Set([
  "node",
  "domain",
  "service",
  "target",
  "data",
  "service_data",
  "gatewayUrl",
  "gatewayToken",
]);
const ALLOWED_CALL_SERVICE_TARGET_ARGS = new Set(["entity_id", "area_id", "device_id"]);

// JSON numbers do not distinguish -0 from 0 (nor 1 from 1.0). Object key
// ordering is immaterial, while booleans must stay distinct from numbers.
function equalServiceData(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) && Array.isArray(right)) {
    return left.length === right.length && left.every((value, index) => equalServiceData(value, right[index]));
  }
  if (left === null || right === null || typeof left !== "object" || typeof right !== "object" || Array.isArray(left) || Array.isArray(right)) {
    return false;
  }
  const leftObject = left as Record<string, unknown>;
  const rightObject = right as Record<string, unknown>;
  const keys = Object.keys(leftObject);
  return keys.length === Object.keys(rightObject).length && keys.every(
    (key) => Object.hasOwn(rightObject, key) && equalServiceData(leftObject[key], rightObject[key]),
  );
}

export function createHaCallServiceTool(): AnyAgentTool {
  return {
    ...HA_CALL_SERVICE_TOOL_DESCRIPTOR,
    execute: async (_toolCallId, args) => {
      const params = args as Record<string, unknown>;
      const callArgs = args as HaCallServiceArgs;
      const unknown = Object.keys(params).filter((key) => !ALLOWED_CALL_SERVICE_ARGS.has(key));
      if (unknown.length > 0) {
        throw new Error(`INVALID_PARAM: unknown parameter(s): ${unknown.sort().join(", ")}`);
      }
      const nodeIdentifier = readTrimmedString(params, "node");
      const domain = readTrimmedString(params, "domain");
      const service = readTrimmedString(params, "service");
      if (!nodeIdentifier) throw new Error("node required");
      if (!domain) throw new Error("domain required");
      if (!service) throw new Error("service required");

      if ("target" in params) {
        if (params.target === null || typeof params.target !== "object" || Array.isArray(params.target)) {
          throw new Error("INVALID_PARAM: target must be an object");
        }
        const unknownTarget = Object.keys(params.target as Record<string, unknown>)
          .filter((key) => !ALLOWED_CALL_SERVICE_TARGET_ARGS.has(key));
        if (unknownTarget.length > 0) {
          throw new Error(`INVALID_PARAM: unknown target parameter(s): ${unknownTarget.sort().join(", ")}`);
        }
      }

      for (const key of ["data", "service_data"] as const) {
        if (key in params && (params[key] === null || typeof params[key] !== "object" || Array.isArray(params[key]))) {
          throw new Error(`INVALID_PARAM: ${key} must be an object`);
        }
      }
      if ("data" in params && "service_data" in params && !equalServiceData(callArgs.data, callArgs.service_data)) {
        throw new Error("INVALID_PARAM: data and service_data must agree when both are supplied");
      }

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
      const data = callArgs.data ?? callArgs.service_data;
      if (data !== undefined) {
        commandParams.data = data;
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
