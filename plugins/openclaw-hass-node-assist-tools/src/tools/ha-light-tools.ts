// ha_light_turn_on / ha_light_turn_off convenience wrappers. Forwards a
// target block { entity_id?, area_id?, device_id? } to the addon.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import {
  HA_LIGHT_TURN_OFF_TOOL_DESCRIPTOR,
  HA_LIGHT_TURN_ON_TOOL_DESCRIPTOR,
} from "./descriptors.js";
import {
  invokeHaCommand,
  readGatewayCallOptions,
  readTrimmedString,
  resolveNodeAndPolicy,
} from "./node-tool-invoke.js";

function pickTarget(params: Record<string, unknown>): Record<string, unknown> {
  const target: Record<string, unknown> = {};
  for (const key of ["entity_id", "area_id", "device_id"] as const) {
    const v = params[key];
    if (typeof v === "string" && v.trim()) target[key] = v.trim();
    else if (Array.isArray(v) && v.length > 0) target[key] = v;
  }
  return target;
}

function createLightActionTool(input: {
  descriptor: Pick<AnyAgentTool, "label" | "name" | "description" | "parameters">;
  command: "ha.light_turn_on" | "ha.light_turn_off";
  label: string;
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

      const target = pickTarget(params);
      if (Object.keys(target).length === 0) {
        return {
          content: [
            {
              type: "text",
              text: `${input.command} requires at least one of entity_id / area_id / device_id.`,
            },
          ],
          isError: true,
        };
      }

      const payload = await invokeHaCommand({
        nodeId,
        command: input.command,
        commandParams: target,
        gatewayOpts,
      });

      return {
        content: [
          {
            type: "text",
            text:
              `${input.label} on ${nodeDisplayName} (${nodeId}) with ${JSON.stringify(target)}:\n\n` +
              `${JSON.stringify(payload, null, 2)}`,
          },
        ],
      };
    },
  };
}

export const createHaLightTurnOnTool = (): AnyAgentTool =>
  createLightActionTool({
    descriptor: HA_LIGHT_TURN_ON_TOOL_DESCRIPTOR,
    command: "ha.light_turn_on",
    label: "light.turn_on",
  });

export const createHaLightTurnOffTool = (): AnyAgentTool =>
  createLightActionTool({
    descriptor: HA_LIGHT_TURN_OFF_TOOL_DESCRIPTOR,
    command: "ha.light_turn_off",
    label: "light.turn_off",
  });
