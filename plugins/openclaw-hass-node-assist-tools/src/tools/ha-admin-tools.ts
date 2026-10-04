// Tier B tools: add-on lifecycle (start/stop/restart/update), core config
// reload, and update install. Every call needs native approval: the
// before_tool_call hook (see shared/node-approval.ts) asks for it and puts the
// approval marker in the tool arguments; the tool forwards it to the node, which
// verifies it. Add-on slugs are also checked against the node's lifecycle policy.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import { adminCommandParams, APPROVAL_PARAM } from "../shared/node-approval.js";
import {
  HA_ADDON_RESTART_TOOL_DESCRIPTOR,
  HA_ADDON_START_TOOL_DESCRIPTOR,
  HA_ADDON_STOP_TOOL_DESCRIPTOR,
  HA_ADDON_UPDATE_TOOL_DESCRIPTOR,
  HA_RELOAD_CONFIG_TOOL_DESCRIPTOR,
  HA_UPDATE_INSTALL_TOOL_DESCRIPTOR,
} from "./descriptors.js";
import {
  invokeHaCommand,
  readGatewayCallOptions,
  readTrimmedString,
  resolveNode,
} from "./node-tool-invoke.js";

const ADMIN_ADDON_SLUG_DENYLIST: ReadonlyArray<string> = [
  "homeassistant",
  "supervisor",
];

function isAdminAddonSlugDenied(slug: string): boolean {
  if (!slug) return true;
  if (ADMIN_ADDON_SLUG_DENYLIST.includes(slug)) return true;
  return slug.startsWith("core_");
}

type AdminInput = {
  descriptor: Pick<AnyAgentTool, "label" | "name" | "description" | "parameters">;
  command:
    | "ha.reload_config"
    | "ha.update_install"
    | "ha.addon_start"
    | "ha.addon_stop"
    | "ha.addon_restart"
    | "ha.addon_update";
  label: string;
};

function createAdminTool(input: AdminInput): AnyAgentTool {
  const isLifecycle = input.command.startsWith("ha.addon_");
  return {
    ...input.descriptor,
    execute: async (_toolCallId, args) => {
      const params = args as Record<string, unknown>;
      const nodeIdentifier = readTrimmedString(params, "node");
      if (!nodeIdentifier) throw new Error("node required");

      const gatewayOpts = readGatewayCallOptions(params);
      const { nodeId, nodeDisplayName } = await resolveNode({ nodeIdentifier, gatewayOpts });

      const commandParams = adminCommandParams(input.command, params);
      if (isLifecycle) {
        const slug = commandParams.slug as string;
        if (!slug) throw new Error("slug required");
        if (isAdminAddonSlugDenied(slug)) {
          return {
            content: [
              {
                type: "text",
                text:
                  `Refused ${input.command}: slug '${slug}' is on the always-deny list ` +
                  `(homeassistant / supervisor / core_*).`,
              },
            ],
            isError: true,
          };
        }
      } else if (input.command === "ha.update_install" && !commandParams.entity_id) {
        throw new Error("entity_id required");
      }
      const marker = params[APPROVAL_PARAM];
      if (marker !== undefined) commandParams[APPROVAL_PARAM] = marker;

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

export const createHaReloadConfigTool = (): AnyAgentTool =>
  createAdminTool({
    descriptor: HA_RELOAD_CONFIG_TOOL_DESCRIPTOR,
    command: "ha.reload_config",
    label: "Reload config",
  });

export const createHaAddonStartTool = (): AnyAgentTool =>
  createAdminTool({
    descriptor: HA_ADDON_START_TOOL_DESCRIPTOR,
    command: "ha.addon_start",
    label: "Add-on start",
  });

export const createHaAddonStopTool = (): AnyAgentTool =>
  createAdminTool({
    descriptor: HA_ADDON_STOP_TOOL_DESCRIPTOR,
    command: "ha.addon_stop",
    label: "Add-on stop",
  });

export const createHaAddonRestartTool = (): AnyAgentTool =>
  createAdminTool({
    descriptor: HA_ADDON_RESTART_TOOL_DESCRIPTOR,
    command: "ha.addon_restart",
    label: "Add-on restart",
  });

export const createHaAddonUpdateTool = (): AnyAgentTool =>
  createAdminTool({
    descriptor: HA_ADDON_UPDATE_TOOL_DESCRIPTOR,
    command: "ha.addon_update",
    label: "Add-on update",
  });

export const createHaUpdateInstallTool = (): AnyAgentTool =>
  createAdminTool({
    descriptor: HA_UPDATE_INSTALL_TOOL_DESCRIPTOR,
    command: "ha.update_install",
    label: "Install update",
  });
