// ha_list_areas tool handler. Wraps ha.list_areas on the bound hass node.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import { HA_LIST_AREAS_TOOL_DESCRIPTOR } from "./descriptors.js";
import { createHaMetadataReadTool } from "./ha-metadata-read-tool.js";

export function createHaListAreasTool(): AnyAgentTool {
  return createHaMetadataReadTool({
    descriptor: HA_LIST_AREAS_TOOL_DESCRIPTOR,
    command: "ha.list_areas",
    label: "Areas",
  });
}
