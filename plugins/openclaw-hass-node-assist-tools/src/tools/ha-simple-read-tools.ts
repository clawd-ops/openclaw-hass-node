// Concrete factory calls for every simple bulk/metadata read tool that
// gates on "plugin bound to this node" and forwards a fixed shape.
// Kept as one file to avoid a wall of near-identical single-tool files.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import {
  HA_ADDON_CHANGELOG_TOOL_DESCRIPTOR,
  HA_ADDON_DOCUMENTATION_TOOL_DESCRIPTOR,
  HA_ADDON_INFO_TOOL_DESCRIPTOR,
  HA_ADDON_LOGS_TOOL_DESCRIPTOR,
  HA_ADDON_STATS_TOOL_DESCRIPTOR,
  HA_CHECK_CONFIG_TOOL_DESCRIPTOR,
  HA_CORE_LOGS_TOOL_DESCRIPTOR,
  HA_GET_CONFIG_TOOL_DESCRIPTOR,
  HA_LIST_ADDONS_TOOL_DESCRIPTOR,
  HA_LIST_AUTOMATIONS_TOOL_DESCRIPTOR,
  HA_LIST_CONFIG_ENTRIES_TOOL_DESCRIPTOR,
  HA_LIST_EVENTS_TOOL_DESCRIPTOR,
  HA_LIST_SERVICES_TOOL_DESCRIPTOR,
} from "./descriptors.js";
import { createHaMetadataReadTool } from "./ha-metadata-read-tool.js";

function readNumber(
  params: Record<string, unknown>,
  key: string,
): number | undefined {
  const v = params[key];
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

function readString(
  params: Record<string, unknown>,
  key: string,
): string | undefined {
  const v = params[key];
  return typeof v === "string" && v.trim() ? v.trim() : undefined;
}

function readBoolean(
  params: Record<string, unknown>,
  key: string,
): boolean | undefined {
  const v = params[key];
  return typeof v === "boolean" ? v : undefined;
}

// --- pure node-only reads ---

export const createHaListServicesTool = (): AnyAgentTool =>
  createHaMetadataReadTool({
    descriptor: HA_LIST_SERVICES_TOOL_DESCRIPTOR,
    command: "ha.list_services",
    label: "Services",
  });

export const createHaGetConfigTool = (): AnyAgentTool =>
  createHaMetadataReadTool({
    descriptor: HA_GET_CONFIG_TOOL_DESCRIPTOR,
    command: "ha.get_config",
    label: "Core config",
  });

export const createHaListEventsTool = (): AnyAgentTool =>
  createHaMetadataReadTool({
    descriptor: HA_LIST_EVENTS_TOOL_DESCRIPTOR,
    command: "ha.list_events",
    label: "Event listeners",
  });

export const createHaListConfigEntriesTool = (): AnyAgentTool =>
  createHaMetadataReadTool({
    descriptor: HA_LIST_CONFIG_ENTRIES_TOOL_DESCRIPTOR,
    command: "ha.list_config_entries",
    label: "Config entries",
  });

export const createHaCheckConfigTool = (): AnyAgentTool =>
  createHaMetadataReadTool({
    descriptor: HA_CHECK_CONFIG_TOOL_DESCRIPTOR,
    command: "ha.check_config",
    label: "Config check",
  });

export const createHaListAddonsTool = (): AnyAgentTool =>
  createHaMetadataReadTool({
    descriptor: HA_LIST_ADDONS_TOOL_DESCRIPTOR,
    command: "ha.list_addons",
    label: "Add-ons",
  });

// --- reads with extra params ---

export const createHaListAutomationsTool = (): AnyAgentTool =>
  createHaMetadataReadTool({
    descriptor: HA_LIST_AUTOMATIONS_TOOL_DESCRIPTOR,
    command: "ha.list_automations",
    label: "Automations",
    buildCommandParams: (args) => {
      const include_traces = readBoolean(args, "include_traces");
      return include_traces === undefined ? {} : { include_traces };
    },
  });

export const createHaCoreLogsTool = (): AnyAgentTool =>
  createHaMetadataReadTool({
    descriptor: HA_CORE_LOGS_TOOL_DESCRIPTOR,
    command: "ha.core_logs",
    label: "Core logs",
    buildCommandParams: (args) => {
      const lines = readNumber(args, "lines");
      return lines === undefined ? {} : { lines };
    },
  });

export const createHaAddonLogsTool = (): AnyAgentTool =>
  createHaMetadataReadTool({
    descriptor: HA_ADDON_LOGS_TOOL_DESCRIPTOR,
    command: "ha.addon_logs",
    label: "Add-on logs",
    buildCommandParams: (args) => {
      const slug = readString(args, "slug");
      if (!slug) throw new Error("slug required");
      const lines = readNumber(args, "lines");
      return lines === undefined ? { slug } : { slug, lines };
    },
  });

function slugOnly(args: Record<string, unknown>): Record<string, unknown> {
  const slug = readString(args, "slug");
  if (!slug) throw new Error("slug required");
  return { slug };
}

export const createHaAddonInfoTool = (): AnyAgentTool =>
  createHaMetadataReadTool({
    descriptor: HA_ADDON_INFO_TOOL_DESCRIPTOR,
    command: "ha.addon_info",
    label: "Add-on info",
    buildCommandParams: slugOnly,
  });

export const createHaAddonStatsTool = (): AnyAgentTool =>
  createHaMetadataReadTool({
    descriptor: HA_ADDON_STATS_TOOL_DESCRIPTOR,
    command: "ha.addon_stats",
    label: "Add-on stats",
    buildCommandParams: slugOnly,
  });

export const createHaAddonChangelogTool = (): AnyAgentTool =>
  createHaMetadataReadTool({
    descriptor: HA_ADDON_CHANGELOG_TOOL_DESCRIPTOR,
    command: "ha.addon_changelog",
    label: "Add-on changelog",
    buildCommandParams: slugOnly,
  });

export const createHaAddonDocumentationTool = (): AnyAgentTool =>
  createHaMetadataReadTool({
    descriptor: HA_ADDON_DOCUMENTATION_TOOL_DESCRIPTOR,
    command: "ha.addon_documentation",
    label: "Add-on documentation",
    buildCommandParams: slugOnly,
  });
