// TypeBox schemas + AnyAgentTool descriptors for the openclaw-hass-node-assist-tools plugin.
//
// Each schema includes a `node` field naming the paired node these tools
// operate against (typically the openclaw-hass-node-app instance running
// in HA Supervisor). Other fields are tool-specific.
//
// Routing-only design: access control is delegated to the hass node's
// tier/allowCommands policy and HA's own auth layer. Tier B admin tools
// (reload_config, addon_start/stop/restart) additionally require
// allowAdminOps + adminToken in the per-node plugin config.

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
    "Call a Home Assistant service (e.g. light.turn_on, switch.toggle) on the paired hass node. Access control is enforced by the hass node's allowCommands tier policy and HA's own auth layer.",
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
    "Read the current state of a Home Assistant entity from the paired hass node. Access control is enforced by the hass node's allowCommands tier policy and HA's own auth layer.",
  parameters: HaGetStateToolSchema,
};

// --- ha_list_states ---

export const HaListStatesToolSchema = Type.Object({
  node: Type.String({ description: PAIRED_NODE_DESCRIPTION }),
  entity_filter: Type.Optional(
    Type.String({
      description:
        "Optional entity_id glob to filter the result (e.g. 'light.*').",
    }),
  ),
});

export const HA_LIST_STATES_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: list entity states",
  name: "ha_list_states",
  description:
    "List current entity states from the paired hass node. Access control is enforced by the hass node's allowCommands tier policy and HA's own auth layer.",
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
    "Fetch events from a HA calendar entity in [start, end). Access control is enforced by the hass node's allowCommands tier policy and HA's own auth layer.",
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

// --- Simple bulk-metadata reads (bind check only) ---

const NodeOnlySchema = () =>
  Type.Object({ node: Type.String({ description: PAIRED_NODE_DESCRIPTION }) });

export const HA_LIST_SERVICES_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: list services",
  name: "ha_list_services",
  description:
    "Enumerate all HA services (domain/service catalog) available on the paired hass node.",
  parameters: NodeOnlySchema(),
};

export const HA_GET_CONFIG_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: get core config",
  name: "ha_get_config",
  description:
    "Read HA core configuration (location, unit system, version, components) from the paired hass node.",
  parameters: NodeOnlySchema(),
};

export const HA_LIST_EVENTS_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: list events",
  name: "ha_list_events",
  description:
    "Enumerate active event listeners on the paired hass node (event bus listener summary).",
  parameters: NodeOnlySchema(),
};

export const HA_LIST_CONFIG_ENTRIES_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: list config entries",
  name: "ha_list_config_entries",
  description:
    "Enumerate integration config entries on the paired hass node.",
  parameters: NodeOnlySchema(),
};

export const HA_LIST_AUTOMATIONS_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: list automations",
  name: "ha_list_automations",
  description:
    "Enumerate automations (entities with the 'automation.' prefix) on the paired hass node.",
  parameters: Type.Object({
    node: Type.String({ description: PAIRED_NODE_DESCRIPTION }),
    include_traces: Type.Optional(
      Type.Boolean({
        description: "Include recent traces per automation. Larger payload.",
      }),
    ),
  }),
};

export const HA_CHECK_CONFIG_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: check config",
  name: "ha_check_config",
  description:
    "Validate HA core configuration on the paired hass node without reloading.",
  parameters: NodeOnlySchema(),
};

// --- Log reads (bounded on the addon side) ---

export const HA_CORE_LOGS_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: core logs",
  name: "ha_core_logs",
  description:
    "Read HA core logs from the paired hass node (Supervisor-backed, bounded tail).",
  parameters: Type.Object({
    node: Type.String({ description: PAIRED_NODE_DESCRIPTION }),
    lines: Type.Optional(
      Type.Number({
        description: "Trailing lines to return, 1–5000 (default 200).",
      }),
    ),
  }),
};

export const HA_ADDON_LOGS_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: addon logs",
  name: "ha_addon_logs",
  description:
    "Read Supervisor add-on logs (bounded 1 MiB tail) from the paired hass node.",
  parameters: Type.Object({
    node: Type.String({ description: PAIRED_NODE_DESCRIPTION }),
    slug: Type.String({ description: "Supervisor add-on slug." }),
    lines: Type.Optional(
      Type.Number({
        description: "Trailing lines to return, 1–5000 (default 200).",
      }),
    ),
  }),
};

// --- Entity-scoped history/activity reads ---

export const HA_LOGBOOK_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: logbook",
  name: "ha_logbook",
  description:
    "Read HA logbook entries. Access control is enforced by the hass node's allowCommands tier policy and HA's own auth layer.",
  parameters: Type.Object({
    node: Type.String({ description: PAIRED_NODE_DESCRIPTION }),
    entity_id: Type.Optional(
      Type.String({ description: "Filter to a single entity_id." }),
    ),
    start: Type.Optional(
      Type.String({ description: "ISO-8601 start (inclusive)." }),
    ),
    end: Type.Optional(
      Type.String({ description: "ISO-8601 end (exclusive)." }),
    ),
  }),
};

export const HA_HISTORY_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: history",
  name: "ha_history",
  description:
    "Read historical state changes. Access control is enforced by the hass node's allowCommands tier policy and HA's own auth layer.",
  parameters: Type.Object({
    node: Type.String({ description: PAIRED_NODE_DESCRIPTION }),
    entity_id: Type.Optional(
      Type.String({ description: "Filter to a single entity_id." }),
    ),
    start: Type.Optional(
      Type.String({ description: "ISO-8601 start (inclusive)." }),
    ),
    end: Type.Optional(
      Type.String({ description: "ISO-8601 end (exclusive)." }),
    ),
  }),
};

// --- Addon metadata reads ---

const AddonSlugSchema = () =>
  Type.Object({
    node: Type.String({ description: PAIRED_NODE_DESCRIPTION }),
    slug: Type.String({ description: "Supervisor add-on slug." }),
  });

export const HA_LIST_ADDONS_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: list addons",
  name: "ha_list_addons",
  description:
    "Enumerate Supervisor add-ons on the paired hass node (slug, name, state, version, version_latest, update_available). Read-only.",
  parameters: NodeOnlySchema(),
};

export const HA_ADDON_INFO_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: addon info",
  name: "ha_addon_info",
  description:
    "Per-addon metadata (name, state, version, boot, startup, ingress). Sensitive fields (options, schema, repository) are dropped by the addon.",
  parameters: AddonSlugSchema(),
};

export const HA_ADDON_STATS_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: addon stats",
  name: "ha_addon_stats",
  description:
    "Allowlisted utilisation metrics for an add-on (cpu_percent, memory_usage/limit/percent, network_rx/tx, blk_read/write). Read-only.",
  parameters: AddonSlugSchema(),
};

export const HA_ADDON_CHANGELOG_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: addon changelog",
  name: "ha_addon_changelog",
  description:
    "Add-on changelog markdown (bounded 1 MiB tail).",
  parameters: AddonSlugSchema(),
};

export const HA_ADDON_DOCUMENTATION_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: addon documentation",
  name: "ha_addon_documentation",
  description:
    "Add-on documentation markdown (bounded 1 MiB tail).",
  parameters: AddonSlugSchema(),
};

// --- Convenience action wrappers (gated by allowServices) ---

const LightTargetSchema = () =>
  Type.Object({
    node: Type.String({ description: PAIRED_NODE_DESCRIPTION }),
    entity_id: Type.Optional(
      Type.Union([Type.String(), Type.Array(Type.String())]),
    ),
    area_id: Type.Optional(
      Type.Union([Type.String(), Type.Array(Type.String())]),
    ),
    device_id: Type.Optional(
      Type.Union([Type.String(), Type.Array(Type.String())]),
    ),
  });

export const HA_LIGHT_TURN_ON_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: light turn on",
  name: "ha_light_turn_on",
  description:
    "Convenience wrapper for light.turn_on. Routes to the hass node; access control is enforced by the node's allowCommands tier policy.",
  parameters: LightTargetSchema(),
};

export const HA_LIGHT_TURN_OFF_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: light turn off",
  name: "ha_light_turn_off",
  description:
    "Convenience wrapper for light.turn_off. Routes to the hass node; access control is enforced by the node's allowCommands tier policy.",
  parameters: LightTargetSchema(),
};

// --- Tier B admin (require allowAdminOps + adminToken in per-node config) ---

export const HA_RELOAD_CONFIG_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: reload config",
  name: "ha_reload_config",
  description:
    "Reload a HA config domain (e.g. 'automation', 'template'). Tier B: requires allowAdminOps + adminToken configured on the node.",
  parameters: Type.Object({
    node: Type.String({ description: PAIRED_NODE_DESCRIPTION }),
    domain: Type.String({
      description: "HA config domain to reload (e.g. 'automation').",
    }),
  }),
};

export const HA_ADDON_START_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: addon start",
  name: "ha_addon_start",
  description:
    "Start a Supervisor add-on. Tier B: requires allowAdminOps + adminToken; always denied for 'homeassistant', 'supervisor', 'core_*'.",
  parameters: AddonSlugSchema(),
};

export const HA_ADDON_STOP_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: addon stop",
  name: "ha_addon_stop",
  description:
    "Stop a Supervisor add-on. Tier B: requires allowAdminOps + adminToken; always denied for 'homeassistant', 'supervisor', 'core_*'.",
  parameters: AddonSlugSchema(),
};

export const HA_ADDON_RESTART_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: addon restart",
  name: "ha_addon_restart",
  description:
    "Restart a Supervisor add-on. Tier B: requires allowAdminOps + adminToken; always denied for 'homeassistant', 'supervisor', 'core_*'.",
  parameters: AddonSlugSchema(),
};

export const HA_ADDON_UPDATE_TOOL_DESCRIPTOR: AssistToolDescriptor = {
  label: "Home Assistant: addon update",
  name: "ha_addon_update",
  description:
    "On the paired Home Assistant node: update a Supervisor add-on to the latest available version. Tier B: requires allowAdminOps + adminToken; always denied for 'homeassistant', 'supervisor', 'core_*'.",
  parameters: AddonSlugSchema(),
};
