// ha_list_devices tool handler. Wraps ha.list_devices on the bound hass node.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import { HA_LIST_DEVICES_TOOL_DESCRIPTOR } from "./descriptors.js";
import { createHaMetadataReadTool } from "./ha-metadata-read-tool.js";

export function createHaListDevicesTool(): AnyAgentTool {
  return createHaMetadataReadTool({
    descriptor: HA_LIST_DEVICES_TOOL_DESCRIPTOR,
    command: "ha.list_devices",
    label: "Devices",
  });
}
