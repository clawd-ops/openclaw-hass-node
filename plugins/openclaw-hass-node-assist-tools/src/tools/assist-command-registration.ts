// Executable Assist registration contract shared by the plugin entrypoint and tests.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import assistCommandContract from "./assist-command-contract.json" with {
  type: "json",
};
import * as descriptors from "./descriptors.js";

export type AssistToolDescriptor = Pick<
  AnyAgentTool,
  "label" | "name" | "description" | "parameters"
>;

export type AssistValueTransform = {
  source_tool_key: string;
  transform: string;
  description: string;
};

export type AssistClientSideParam = {
  behavior: "glob_filter_result_by_entity_id";
  description: string;
};

export type AssistCommandRegistration = {
  tool_name: string;
  descriptor: string;
  factory: string;
  node_command: string;
  accepted_tool_params: string[];
  emitted_params: Record<string, string | null>;
  injected_node_params: Record<string, string>;
  known_unaccepted_node_params: Record<string, { issue: string; reason: string }>;
  value_transforms: Record<string, AssistValueTransform>;
  client_side_params: Record<string, AssistClientSideParam>;
};

export type ResolvedAssistCommandRegistration = {
  contract: AssistCommandRegistration;
  descriptor: AssistToolDescriptor;
  loadTool: () => Promise<AnyAgentTool>;
};

const FACTORY_LOADERS: Record<string, () => Promise<AnyAgentTool>> = {
  createHaCallServiceTool: async () =>
    (await import("./ha-call-service-tool.js")).createHaCallServiceTool(),
  createHaGetStateTool: async () =>
    (await import("./ha-get-state-tool.js")).createHaGetStateTool(),
  createHaListStatesTool: async () =>
    (await import("./ha-list-states-tool.js")).createHaListStatesTool(),
  createHaCalendarGetEventsTool: async () =>
    (await import("./ha-calendar-get-events-tool.js")).createHaCalendarGetEventsTool(),
  createHaListAreasTool: async () =>
    (await import("./ha-list-areas-tool.js")).createHaListAreasTool(),
  createHaListDevicesTool: async () =>
    (await import("./ha-list-devices-tool.js")).createHaListDevicesTool(),
  createHaListEntityRegistryTool: async () =>
    (await import("./ha-list-entity-registry-tool.js")).createHaListEntityRegistryTool(),
  createHaListServicesTool: async () =>
    (await import("./ha-simple-read-tools.js")).createHaListServicesTool(),
  createHaGetConfigTool: async () =>
    (await import("./ha-simple-read-tools.js")).createHaGetConfigTool(),
  createHaListEventsTool: async () =>
    (await import("./ha-simple-read-tools.js")).createHaListEventsTool(),
  createHaListConfigEntriesTool: async () =>
    (await import("./ha-simple-read-tools.js")).createHaListConfigEntriesTool(),
  createHaListAutomationsTool: async () =>
    (await import("./ha-simple-read-tools.js")).createHaListAutomationsTool(),
  createHaCheckConfigTool: async () =>
    (await import("./ha-simple-read-tools.js")).createHaCheckConfigTool(),
  createHaCoreLogsTool: async () =>
    (await import("./ha-simple-read-tools.js")).createHaCoreLogsTool(),
  createHaAddonLogsTool: async () =>
    (await import("./ha-simple-read-tools.js")).createHaAddonLogsTool(),
  createHaListAddonsTool: async () =>
    (await import("./ha-simple-read-tools.js")).createHaListAddonsTool(),
  createHaAddonInfoTool: async () =>
    (await import("./ha-simple-read-tools.js")).createHaAddonInfoTool(),
  createHaAddonStatsTool: async () =>
    (await import("./ha-simple-read-tools.js")).createHaAddonStatsTool(),
  createHaAddonChangelogTool: async () =>
    (await import("./ha-simple-read-tools.js")).createHaAddonChangelogTool(),
  createHaAddonDocumentationTool: async () =>
    (await import("./ha-simple-read-tools.js")).createHaAddonDocumentationTool(),
  createHaLogbookTool: async () =>
    (await import("./ha-entity-scoped-read-tools.js")).createHaLogbookTool(),
  createHaHistoryTool: async () =>
    (await import("./ha-entity-scoped-read-tools.js")).createHaHistoryTool(),
  createHaLightTurnOnTool: async () =>
    (await import("./ha-light-tools.js")).createHaLightTurnOnTool(),
  createHaLightTurnOffTool: async () =>
    (await import("./ha-light-tools.js")).createHaLightTurnOffTool(),
  createHaReloadConfigTool: async () =>
    (await import("./ha-admin-tools.js")).createHaReloadConfigTool(),
  createHaAddonStartTool: async () =>
    (await import("./ha-admin-tools.js")).createHaAddonStartTool(),
  createHaAddonStopTool: async () =>
    (await import("./ha-admin-tools.js")).createHaAddonStopTool(),
  createHaAddonRestartTool: async () =>
    (await import("./ha-admin-tools.js")).createHaAddonRestartTool(),
  createHaAddonUpdateTool: async () =>
    (await import("./ha-admin-tools.js")).createHaAddonUpdateTool(),
  createHaUpdateInstallTool: async () =>
    (await import("./ha-admin-tools.js")).createHaUpdateInstallTool(),
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringMap(value: unknown, allowNull: boolean): Record<string, string | null> {
  if (!isRecord(value)) {
    throw new Error("Assist command contract map must be an object");
  }
  for (const item of Object.values(value)) {
    if (typeof item !== "string" && !(allowNull && item === null)) {
      throw new Error("Assist command contract map values have the wrong type");
    }
  }
  return value as Record<string, string | null>;
}

function mismatchMap(value: unknown): Record<string, { issue: string; reason: string }> {
  if (!isRecord(value)) {
    throw new Error("Assist known mismatch map must be an object");
  }
  for (const mismatch of Object.values(value)) {
    if (
      !isRecord(mismatch) ||
      typeof mismatch.issue !== "string" ||
      mismatch.issue.length === 0 ||
      typeof mismatch.reason !== "string" ||
      mismatch.reason.length === 0
    ) {
      throw new Error("Assist known mismatch entries require issue and reason strings");
    }
  }
  return value as Record<string, { issue: string; reason: string }>;
}

function exactNames(label: string, actual: string[], expected: string[]): void {
  const actualSorted = [...actual].sort();
  const expectedSorted = [...expected].sort();
  if (
    new Set(actualSorted).size !== actualSorted.length ||
    new Set(expectedSorted).size !== expectedSorted.length ||
    JSON.stringify(actualSorted) !== JSON.stringify(expectedSorted)
  ) {
    throw new Error(
      `${label} differs: actual=${actualSorted.join(",")} expected=${expectedSorted.join(",")}`,
    );
  }
}

function descriptorParameterNames(descriptor: AssistToolDescriptor): string[] {
  const parameters = descriptor.parameters as { properties?: Record<string, unknown> };
  return Object.keys(parameters.properties ?? {});
}

function resolveDescriptor(name: string): AssistToolDescriptor {
  const value: unknown = (descriptors as Record<string, unknown>)[name];
  if (!isRecord(value)) {
    throw new Error(`Assist command contract references unknown descriptor ${name}`);
  }
  if (
    typeof value.name !== "string" ||
    typeof value.description !== "string" ||
    !("parameters" in value)
  ) {
    throw new Error(`Assist command contract descriptor ${name} is malformed`);
  }
  return value as AssistToolDescriptor;
}

const ALLOWED_TRANSFORMS = new Set(["wrap_array", "alias_merge"]);
const ALLOWED_INJECTED_SOURCES = new Set(["$policy.adminToken"]);
const ALLOWED_CLIENT_SIDE_BEHAVIORS = new Set(["glob_filter_result_by_entity_id"]);

function parseSourceToolKeys(source: string): string[] {
  return source.split("|").map((s) => s.trim()).filter(Boolean);
}

function parseValueTransforms(
  value: unknown,
  emitted: Record<string, string | null>,
  accepted: string[],
): Record<string, AssistValueTransform> {
  if (!isRecord(value)) {
    throw new Error("Assist value_transforms must be an object");
  }
  const emittedNodeKeys = new Set(
    Object.values(emitted).filter((v): v is string => v !== null),
  );
  const acceptedSet = new Set(accepted);
  const result: Record<string, AssistValueTransform> = {};
  for (const [nodeKey, entry] of Object.entries(value)) {
    if (
      !isRecord(entry) ||
      typeof entry.source_tool_key !== "string" ||
      !entry.source_tool_key ||
      typeof entry.transform !== "string" ||
      !entry.transform ||
      typeof entry.description !== "string" ||
      !entry.description
    ) {
      throw new Error(
        `Assist value_transforms[${nodeKey}] must have source_tool_key, transform, and description strings`,
      );
    }
    const transform = entry.transform as string;
    if (!ALLOWED_TRANSFORMS.has(transform)) {
      throw new Error(
        `Assist value_transforms[${nodeKey}] transform '${transform}' is not in allowed set: ${[...ALLOWED_TRANSFORMS].join(", ")}`,
      );
    }
    if (!emittedNodeKeys.has(nodeKey)) {
      throw new Error(
        `Assist value_transforms[${nodeKey}] target is not an emitted non-null node key`,
      );
    }
    const sourceKeys = parseSourceToolKeys(entry.source_tool_key as string);
    if (sourceKeys.length === 0) {
      throw new Error(
        `Assist value_transforms[${nodeKey}] source_tool_key is empty after parsing`,
      );
    }
    for (const key of sourceKeys) {
      if (!acceptedSet.has(key)) {
        throw new Error(
          `Assist value_transforms[${nodeKey}] source_tool_key '${key}' is not an accepted tool param`,
        );
      }
    }
    if (transform === "wrap_array" && sourceKeys.length !== 1) {
      throw new Error(
        `Assist value_transforms[${nodeKey}] wrap_array requires exactly one source key`,
      );
    }
    if (transform === "wrap_array") {
      const sourceKey = sourceKeys[0];
      if (sourceKey === undefined) {
        throw new Error(
          `Assist value_transforms[${nodeKey}] wrap_array source is missing`,
        );
      }
      // wrap_array source must map to this target in emitted_params
      if (emitted[sourceKey] !== nodeKey) {
        throw new Error(
          `Assist value_transforms[${nodeKey}] wrap_array source '${sourceKey}' must map to '${nodeKey}' in emitted_params`,
        );
      }
    }
    if (transform === "alias_merge" && sourceKeys.length < 2) {
      throw new Error(
        `Assist value_transforms[${nodeKey}] alias_merge requires at least two source keys`,
      );
    }
    if (transform === "alias_merge") {
      // All alias_merge sources must map to the same target
      for (const key of sourceKeys) {
        if (emitted[key] !== nodeKey) {
          throw new Error(
            `Assist value_transforms[${nodeKey}] alias_merge source '${key}' must map to '${nodeKey}' in emitted_params`,
          );
        }
      }
    }
    result[nodeKey] = {
      source_tool_key: entry.source_tool_key as string,
      transform,
      description: entry.description as string,
    };
  }
  return result;
}

function parseInjectedNodeParams(value: unknown): Record<string, string> {
  const injected = stringMap(value, false) as Record<string, string>;
  for (const [source, target] of Object.entries(injected)) {
    if (!ALLOWED_INJECTED_SOURCES.has(source)) {
      throw new Error(`Assist injected source '${source}' is not supported`);
    }
    if (!target) {
      throw new Error(`Assist injected source '${source}' requires a nonempty node target`);
    }
  }
  return injected;
}

function parseClientSideParams(
  value: unknown,
  emitted: Record<string, string | null>,
  accepted: string[],
): Record<string, AssistClientSideParam> {
  if (!isRecord(value)) {
    throw new Error("Assist client_side_params must be an object");
  }
  const acceptedSet = new Set(accepted);
  const result: Record<string, AssistClientSideParam> = {};
  for (const [toolKey, entry] of Object.entries(value)) {
    if (
      !isRecord(entry) ||
      typeof entry.behavior !== "string" ||
      !ALLOWED_CLIENT_SIDE_BEHAVIORS.has(entry.behavior) ||
      typeof entry.description !== "string" ||
      !entry.description
    ) {
      throw new Error(`Assist client_side_params[${toolKey}] is malformed or unsupported`);
    }
    if (!acceptedSet.has(toolKey) || emitted[toolKey] !== null) {
      throw new Error(
        `Assist client_side_params[${toolKey}] must name an accepted null-mapped tool param`,
      );
    }
    result[toolKey] = {
      behavior: entry.behavior as AssistClientSideParam["behavior"],
      description: entry.description,
    };
  }
  for (const [toolKey, nodeKey] of Object.entries(emitted)) {
    if (toolKey !== "node" && nodeKey === null && !(toolKey in result)) {
      throw new Error(
        `Assist null-mapped tool param '${toolKey}' requires client_side_params metadata`,
      );
    }
  }
  return result;
}

export function parseAssistCommandRegistration(value: unknown): AssistCommandRegistration {
  if (!isRecord(value)) {
    throw new Error("Assist command contract registration must be an object");
  }
  const stringField = (field: "tool_name" | "descriptor" | "factory" | "node_command") => {
    const item = value[field];
    if (typeof item !== "string" || item.length === 0) {
      throw new Error(`Assist command contract field ${field} must be a nonempty string`);
    }
    return item;
  };
  const toolName = stringField("tool_name");
  const descriptor = stringField("descriptor");
  const factory = stringField("factory");
  const nodeCommand = stringField("node_command");
  if (
    !Array.isArray(value.accepted_tool_params) ||
    !value.accepted_tool_params.every((item) => typeof item === "string")
  ) {
    throw new Error("Assist command contract accepted_tool_params must be a string array");
  }
  const emitted = stringMap(value.emitted_params, true);
  const injected = parseInjectedNodeParams(value.injected_node_params);
  const known = mismatchMap(value.known_unaccepted_node_params ?? {});
  const transforms = parseValueTransforms(
    value.value_transforms ?? {},
    emitted,
    value.accepted_tool_params as string[],
  );
  const clientSideParams = parseClientSideParams(
    value.client_side_params ?? {},
    emitted,
    value.accepted_tool_params as string[],
  );
  return {
    tool_name: toolName,
    descriptor,
    factory,
    node_command: nodeCommand,
    accepted_tool_params: value.accepted_tool_params,
    emitted_params: emitted,
    injected_node_params: injected,
    known_unaccepted_node_params: known,
    value_transforms: transforms,
    client_side_params: clientSideParams,
  };
}

export function resolvedAssistCommandRegistrations(): ResolvedAssistCommandRegistration[] {
  if (assistCommandContract.schema_version !== 1) {
    throw new Error("Unsupported Assist command contract schema version");
  }
  const registrations = assistCommandContract.registrations.map(parseAssistCommandRegistration);
  const uniqueness = ["tool_name", "descriptor", "factory", "node_command"] as const;
  for (const field of uniqueness) {
    exactNames(
      `Assist command contract ${field}`,
      registrations.map((entry) => entry[field]),
      [...new Set(registrations.map((entry) => entry[field]))],
    );
  }
  exactNames(
    "Assist factory registration",
    registrations.map((entry) => entry.factory),
    Object.keys(FACTORY_LOADERS),
  );
  return registrations.map((contract) => {
    const descriptor = resolveDescriptor(contract.descriptor);
    if (descriptor.name !== contract.tool_name) {
      throw new Error(
        `Assist command contract tool ${contract.tool_name} does not match descriptor ${descriptor.name}`,
      );
    }
    exactNames(
      `Assist command contract parameters for ${contract.tool_name}`,
      descriptorParameterNames(descriptor),
      contract.accepted_tool_params,
    );
    exactNames(
      `Assist command contract emitted keys for ${contract.tool_name}`,
      Object.keys(contract.emitted_params),
      contract.accepted_tool_params,
    );
    const loadTool = FACTORY_LOADERS[contract.factory];
    if (!loadTool) {
      throw new Error(`Assist command contract references unknown factory ${contract.factory}`);
    }
    return { contract, descriptor, loadTool };
  });
}
