// Tier B admin handlers: ha_reload_config / ha_addon_start /
// ha_addon_stop / ha_addon_restart. Require per-node allowAdminOps=true
// AND adminToken configured. The token is pulled from the plugin config,
// never from the tool caller. Addon lifecycle also enforces a slug
// deny-list (homeassistant / supervisor / core_*).

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import {
  HA_ADDON_RESTART_TOOL_DESCRIPTOR,
  HA_ADDON_START_TOOL_DESCRIPTOR,
  HA_ADDON_STOP_TOOL_DESCRIPTOR,
  HA_ADDON_UPDATE_TOOL_DESCRIPTOR,
  HA_RELOAD_CONFIG_TOOL_DESCRIPTOR,
} from "./descriptors.js";
import {
  invokeHaCommand,
  readGatewayCallOptions,
  readTrimmedString,
  resolveNodeAndPolicy,
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
    | "ha.addon_start"
    | "ha.addon_stop"
    | "ha.addon_restart"
    | "ha.addon_update";
  requireSlug: boolean;
  label: string;
};

function createAdminTool(input: AdminInput): AnyAgentTool {
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

      if (!policy?.allowAdminOps) {
        return {
          content: [
            {
              type: "text",
              text:
                `Refused ${input.command} on ${nodeDisplayName} (${nodeId}): ` +
                `allowAdminOps is not set. ` +
                `Set plugins.entries.openclaw-hass-node-assist-tools.config.nodes.${nodeIdentifier}.allowAdminOps = true.`,
            },
          ],
          isError: true,
        };
      }
      if (typeof policy.adminToken !== "string" || !policy.adminToken) {
        return {
          content: [
            {
              type: "text",
              text:
                `Refused ${input.command} on ${nodeDisplayName} (${nodeId}): ` +
                `adminToken is not configured. Set nodes.${nodeIdentifier}.adminToken to the OPENCLAW_ADMIN_TOKEN the addon expects.`,
            },
          ],
          isError: true,
        };
      }

      const commandParams: Record<string, unknown> = {
        admin_token: policy.adminToken,
      };

      if (input.requireSlug) {
        const slug = readTrimmedString(params, "slug");
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
        commandParams.slug = slug;
      } else if (input.command === "ha.reload_config") {
        const domain = readTrimmedString(params, "domain");
        if (!domain) throw new Error("domain required");
        commandParams.domain = domain;
      }

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
    requireSlug: false,
    label: "Reload config",
  });

export const createHaAddonStartTool = (): AnyAgentTool =>
  createAdminTool({
    descriptor: HA_ADDON_START_TOOL_DESCRIPTOR,
    command: "ha.addon_start",
    requireSlug: true,
    label: "Add-on start",
  });

export const createHaAddonStopTool = (): AnyAgentTool =>
  createAdminTool({
    descriptor: HA_ADDON_STOP_TOOL_DESCRIPTOR,
    command: "ha.addon_stop",
    requireSlug: true,
    label: "Add-on stop",
  });

export const createHaAddonRestartTool = (): AnyAgentTool =>
  createAdminTool({
    descriptor: HA_ADDON_RESTART_TOOL_DESCRIPTOR,
    command: "ha.addon_restart",
    requireSlug: true,
    label: "Add-on restart",
  });

export const createHaAddonUpdateTool = (): AnyAgentTool =>
  createAdminTool({
    descriptor: HA_ADDON_UPDATE_TOOL_DESCRIPTOR,
    command: "ha.addon_update",
    requireSlug: true,
    label: "Add-on update",
  });
