// TypeBox schemas + AnyAgentTool descriptors for the openclaw-hass-node-assist-tools plugin.
//
// Each schema includes a `node` field naming the paired node these tools
// operate against (typically the openclaw-hass-node-app instance running
// in HA Supervisor). Other fields are tool-specific.
//
// All tools surface to Assist sessions; the per-node policy in the plugin
// config (allowServices, allowReadEntities, ...) gates which operations the
// session may actually invoke at execute-time.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import { Type } from "typebox";

type AssistToolDescriptor = Pick<
  AnyAgentTool,
  "label" | "name" | "description" | "parameters"
>;

export const PAIRED_NODE_DESCRIPTION =
  "Paired hass node id or display name from `nodes status`. Do not pass local/host/gateway/auto; this plugin only operates against the bound openclaw-hass-node-app.";

// --- ha_call_service ---

export const HaCallServiceToolSchema = Type.Object({
  node: Type.String({ description: PAIRED_NODE_DESCRIPTION }),
  domain: Type.String({
    description: "HA service domain, e.g. 'light', 'switch', 'homeassistant'.",
  }),
  service: Type.String({
    description: "HA service name, e.g. 'turn_on', 'turn_off', 'update_entity'.",
  }),
  target: Type.Optional(
    Type.Object(
      {
        entity_id: Type.Optional(
          Type.Union([Type.String(), Type.Array(Type.String())]),
        ),
        area_id: Type.Optional(
          Type.Union([Type.String(), Type.Array(Type.String())]),
        ),
        device_id: Type.Optional(
          Type.Union([Type.String(), Type.Array(Type.String())]),
        ),
      },
      { description: "HA service target (entity_id / area_id / device_id)." },
    ),
  ),
  service_data: Type.Optional(
    Type.Record(Type.String(), Type.Unknown(), {
      description: "HA service data payload, e.g. {brightness_pct: 50}.",
    }),
  ),
});

export const HA_CALL_SERVICE_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: call service",
  name: "ha_call_service",
  description:
    "Call a Home Assistant service (e.g. light.turn_on, switch.toggle) on the paired hass node. The plugin's per-node allowServices/denyServices gate which <domain>.<service> patterns are reachable; deny wins.",
  parameters: HaCallServiceToolSchema,
};

// --- ha_get_state ---

export const HaGetStateToolSchema = Type.Object({
  node: Type.String({ description: PAIRED_NODE_DESCRIPTION }),
  entity_id: Type.String({
    description: "HA entity_id to read, e.g. 'light.living_room' or 'sensor.outdoor_temp'.",
  }),
});

export const HA_GET_STATE_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: get entity state",
  name: "ha_get_state",
  description:
    "Read the current state of a Home Assistant entity from the paired hass node. The plugin's per-node allowReadEntities/denyReadEntities gate which entity_id patterns are readable; deny wins.",
  parameters: HaGetStateToolSchema,
};

// --- ha_list_states ---

export const HaListStatesToolSchema = Type.Object({
  node: Type.String({ description: PAIRED_NODE_DESCRIPTION }),
  entity_filter: Type.Optional(
    Type.String({
      description:
        "Optional entity_id glob to filter the result (e.g. 'light.*'). Applied in addition to per-node allowReadEntities.",
    }),
  ),
});

export const HA_LIST_STATES_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: list entity states",
  name: "ha_list_states",
  description:
    "List current entity states from the paired hass node. Results are filtered through the plugin's per-node allowReadEntities/denyReadEntities policy.",
  parameters: HaListStatesToolSchema,
};

// --- ha_calendar_get_events ---

export const HaCalendarGetEventsToolSchema = Type.Object({
  node: Type.String({ description: PAIRED_NODE_DESCRIPTION }),
  entity_id: Type.String({
    description: "HA calendar entity_id (e.g. 'calendar.personal').",
  }),
  start_date_time: Type.String({
    description: "ISO-8601 start (inclusive), e.g. '2026-06-30T00:00:00'.",
  }),
  end_date_time: Type.String({
    description: "ISO-8601 end (exclusive), e.g. '2026-07-07T00:00:00'.",
  }),
});

export const HA_CALENDAR_GET_EVENTS_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: calendar get events",
  name: "ha_calendar_get_events",
  description:
    "Fetch events from a HA calendar entity in [start, end). Restricted to calendars listed in per-node allowCalendars (if configured).",
  parameters: HaCalendarGetEventsToolSchema,
};

// --- ha_list_areas / ha_list_devices / ha_list_entity_registry ---

export const HaListAreasToolSchema = Type.Object({
  node: Type.String({ description: PAIRED_NODE_DESCRIPTION }),
});

export const HA_LIST_AREAS_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: list areas",
  name: "ha_list_areas",
  description: "Enumerate areas defined on the paired hass node.",
  parameters: HaListAreasToolSchema,
};

export const HaListDevicesToolSchema = Type.Object({
  node: Type.String({ description: PAIRED_NODE_DESCRIPTION }),
});

export const HA_LIST_DEVICES_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: list devices",
  name: "ha_list_devices",
  description: "Enumerate devices registered on the paired hass node.",
  parameters: HaListDevicesToolSchema,
};

export const HaListEntityRegistryToolSchema = Type.Object({
  node: Type.String({ description: PAIRED_NODE_DESCRIPTION }),
});

export const HA_LIST_ENTITY_REGISTRY_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: list entity registry",
  name: "ha_list_entity_registry",
  description:
    "Enumerate the full entity registry on the paired hass node (entity_id, platform, device_id, area_id, disabled state, etc.).",
  parameters: HaListEntityRegistryToolSchema,
};
