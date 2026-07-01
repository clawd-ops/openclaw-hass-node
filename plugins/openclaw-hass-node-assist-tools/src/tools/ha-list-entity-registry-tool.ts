// ha_list_entity_registry tool handler. Wraps ha.list_entity_registry on
// the bound hass node.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import { HA_LIST_ENTITY_REGISTRY_TOOL_DESCRIPTOR } from "./descriptors.js";
import { createHaMetadataReadTool } from "./ha-metadata-read-tool.js";

export function createHaListEntityRegistryTool(): AnyAgentTool {
  return createHaMetadataReadTool({
    descriptor: HA_LIST_ENTITY_REGISTRY_TOOL_DESCRIPTOR,
    command: "ha.list_entity_registry",
    label: "Entity registry",
  });
}
