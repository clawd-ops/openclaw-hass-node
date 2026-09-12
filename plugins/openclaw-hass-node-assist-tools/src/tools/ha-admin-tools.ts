// Tier B handlers split into two authorization levels:
//
// Lifecycle ops (ha_addon_start/stop/restart/update): require per-node
// allowAdminOps=true only. The node authenticates via the established
// pairing session plus slug allowlist/denylist policy. No admin token.
//
// Admin ops (ha_reload_config, ha_update_install): require per-node
// allowAdminOps=true AND adminToken configured. The token is pulled from
// the plugin config, never from the tool caller.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
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

// --- Lifecycle tools (no admin token) ---

type LifecycleInput = {
  descriptor: Pick<AnyAgentTool, "label" | "name" | "description" | "parameters">;
  command: "ha.addon_start" | "ha.addon_stop" | "ha.addon_restart" | "ha.addon_update";
  label: string;
};

function createLifecycleTool(input: LifecycleInput): AnyAgentTool {
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

      // Lifecycle ops use pairing auth + slug policy on the node.
      // No admin_token is sent.
      const commandParams: Record<string, unknown> = { slug };

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

// --- Admin tools (require adminToken) ---

type AdminInput = {
  descriptor: Pick<AnyAgentTool, "label" | "name" | "description" | "parameters">;
  command: "ha.reload_config" | "ha.update_install";
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

      if (input.command === "ha.reload_config") {
        // `domain` is optional and only "core" is supported. Forward it when
        // supplied so the node, not the plugin, owns the rejection message for
        // an unsupported domain.
        const domain = readTrimmedString(params, "domain");
        if (domain) commandParams.domain = domain;
      } else if (input.command === "ha.update_install") {
        const entityId = readTrimmedString(params, "entity_id");
        if (!entityId) throw new Error("entity_id required");
        commandParams.entity_id = entityId;
        if (typeof params.backup === "boolean") {
          commandParams.backup = params.backup;
        }
        if (typeof params.version === "string" && params.version) {
          commandParams.version = params.version;
        }
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
    label: "Reload config",
  });

export const createHaAddonStartTool = (): AnyAgentTool =>
  createLifecycleTool({
    descriptor: HA_ADDON_START_TOOL_DESCRIPTOR,
    command: "ha.addon_start",
    label: "Add-on start",
  });

export const createHaAddonStopTool = (): AnyAgentTool =>
  createLifecycleTool({
    descriptor: HA_ADDON_STOP_TOOL_DESCRIPTOR,
    command: "ha.addon_stop",
    label: "Add-on stop",
  });

export const createHaAddonRestartTool = (): AnyAgentTool =>
  createLifecycleTool({
    descriptor: HA_ADDON_RESTART_TOOL_DESCRIPTOR,
    command: "ha.addon_restart",
    label: "Add-on restart",
  });

export const createHaAddonUpdateTool = (): AnyAgentTool =>
  createLifecycleTool({
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
