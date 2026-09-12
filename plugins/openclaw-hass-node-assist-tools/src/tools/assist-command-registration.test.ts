import { describe, expect, it, vi } from "vitest";
import {
  parseAssistCommandRegistration,
  resolvedAssistCommandRegistrations,
  type AssistCommandRegistration,
} from "./assist-command-registration.js";
import assistCommandContract from "./assist-command-contract.json" with {
  type: "json",
};
import pluginManifest from "../../openclaw.plugin.json" with { type: "json" };

const invokeHaCommandMock = vi.fn();
const resolveNodeAndPolicyMock = vi.fn();

vi.mock("./node-tool-invoke.js", () => ({
  PLUGIN_ID: "openclaw-hass-node-assist-tools",
  invokeHaCommand: (...args: unknown[]) => invokeHaCommandMock(...args),
  readGatewayCallOptions: () => ({}),
  readTrimmedString: (params: Record<string, unknown>, key: string) => {
    const v = params[key];
    return typeof v === "string" ? v.trim() : "";
  },
  resolveNodeAndPolicy: (...args: unknown[]) => resolveNodeAndPolicyMock(...args),
}));

// ---------------------------------------------------------------------------
// Contract structure and identity tests
// ---------------------------------------------------------------------------

describe("Assist executable command contract", () => {
  it("resolves every manifest row to one descriptor and factory", () => {
    const registrations = resolvedAssistCommandRegistrations();
    expect(registrations).toHaveLength(31);
    expect(new Set(registrations.map(({ contract }) => contract.tool_name)).size).toBe(31);
    expect(new Set(registrations.map(({ contract }) => contract.node_command)).size).toBe(31);
    for (const { contract, descriptor, loadTool } of registrations) {
      expect(descriptor.name).toBe(contract.tool_name);
      expect(typeof loadTool).toBe("function");
      expect(Object.keys(contract.emitted_params).sort()).toEqual(
        [...contract.accepted_tool_params].sort(),
      );
    }
  });

  it("loads every registered factory with the declared tool identity", async () => {
    const registrations = resolvedAssistCommandRegistrations();
    const tools = await Promise.all(
      registrations.map(async ({ contract, loadTool }) => ({
        contract,
        tool: await loadTool(),
      })),
    );
    for (const { contract, tool } of tools) {
      expect(tool.name).toBe(contract.tool_name);
    }
  });

  it("keeps lifecycle and admin parameter injection distinct", () => {
    const byCommand = new Map(
      resolvedAssistCommandRegistrations().map(({ contract }) => [
        contract.node_command,
        contract,
      ]),
    );
    for (const command of [
      "ha.addon_start",
      "ha.addon_stop",
      "ha.addon_restart",
      "ha.addon_update",
    ]) {
      expect(byCommand.get(command)?.injected_node_params).toEqual({});
      expect(byCommand.get(command)?.known_unaccepted_node_params).toEqual({});
    }
    for (const command of ["ha.reload_config", "ha.update_install"]) {
      expect(byCommand.get(command)?.injected_node_params).toEqual({
        "$policy.adminToken": "admin_token",
      });
    }
  });
});

// ---------------------------------------------------------------------------
// Manifest parity: openclaw.plugin.json contracts.tools must exactly match
// the executable Assist contract registrations.
// ---------------------------------------------------------------------------

describe("Manifest/contract tool parity", () => {
  it("openclaw.plugin.json contracts.tools matches Assist contract tool_names exactly", () => {
    const registrations = resolvedAssistCommandRegistrations();
    const contractTools = new Set(registrations.map(({ contract }) => contract.tool_name));
    const manifestTools = new Set(pluginManifest.contracts.tools);
    expect(contractTools).toEqual(manifestTools);
  });

  it("30-tool count is explicit", () => {
    expect(pluginManifest.contracts.tools).toHaveLength(31);
    expect(assistCommandContract.registrations).toHaveLength(31);
  });
});

// ---------------------------------------------------------------------------
// Executable drift gate — exercise each factory's real execute() path and
// verify the actual invokeHaCommand call matches the contract's
// node_command, emitted_params, value_transforms, and injected_node_params.
//
// Every tool-param sentinel is unique per source field so that swapping two
// mapping targets (e.g. start->start_time vs start->end_time) fails the
// value comparison even when the key sets are identical.
//
// Value transforms (e.g. ha_history entity_id -> [entity_id]) are read from
// the contract schema, not hard-coded in the test.
// ---------------------------------------------------------------------------

const ADMIN_TOKEN_SENTINEL = "test-token-sentinel-admin";

/** Unique sentinel per accepted tool param — never reuse across fields. */
function buildTestArgs(contract: AssistCommandRegistration): Record<string, unknown> {
  const args: Record<string, unknown> = {};
  for (const key of contract.accepted_tool_params) {
    switch (key) {
      case "node":
        args[key] = "test-hass";
        break;
      case "target":
        args[key] = { entity_id: `sentinel_target_${contract.tool_name}` };
        break;
      case "data":
        args[key] = { sentinel_data: contract.tool_name };
        break;
      case "service_data":
        // Only set when data is NOT also accepted (avoid alias conflict)
        if (!contract.accepted_tool_params.includes("data")) {
          args[key] = { sentinel_service_data: contract.tool_name };
        }
        break;
      case "include_traces":
        args[key] = true;
        break;
      case "lines":
        args[key] = 42;
        break;
      case "backup":
        args[key] = true;
        break;
      default:
        args[key] = `sentinel_${key}_${contract.tool_name}`;
    }
  }
  return args;
}

function expectedCommandParams(
  contract: AssistCommandRegistration,
  args: Record<string, unknown>,
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  const transformedToolKeys = new Set<string>();

  for (const [nodeKey, transform] of Object.entries(contract.value_transforms)) {
    const sourceKeys = transform.source_tool_key.split("|").map((key) => key.trim());
    sourceKeys.forEach((key) => transformedToolKeys.add(key));
    if (transform.transform === "wrap_array") {
      const sourceKey = sourceKeys[0];
      if (sourceKey !== undefined && args[sourceKey] !== undefined) {
        result[nodeKey] = [args[sourceKey]];
      }
    } else if (transform.transform === "alias_merge") {
      const sourceKey = sourceKeys.find(
        (key) => args[key] !== undefined && args[key] !== "",
      );
      if (sourceKey !== undefined) result[nodeKey] = args[sourceKey];
    } else {
      throw new Error(`Unknown transform ${transform.transform}`);
    }
  }

  for (const [toolKey, nodeKey] of Object.entries(contract.emitted_params)) {
    if (nodeKey === null) continue;
    if (transformedToolKeys.has(toolKey)) continue;
    if (args[toolKey] === undefined || args[toolKey] === "") continue;
    result[nodeKey] = args[toolKey];
  }

  // Injected params: verify exact source semantics
  for (const [policySource, nodeKey] of Object.entries(contract.injected_node_params)) {
    if (policySource === "$policy.adminToken") {
      result[nodeKey] = ADMIN_TOKEN_SENTINEL;
    } else {
      throw new Error(`Unknown injected source ${policySource} for ${contract.tool_name}`);
    }
  }

  return result;
}

function setupMocks(contract: AssistCommandRegistration): void {
  invokeHaCommandMock.mockReset();
  resolveNodeAndPolicyMock.mockReset();
  resolveNodeAndPolicyMock.mockResolvedValue({
    nodeId: "test-node",
    nodeDisplayName: "Test HA",
    policy: { allowAdminOps: true, adminToken: ADMIN_TOKEN_SENTINEL },
  });
  if (contract.tool_name === "ha_list_states") {
    invokeHaCommandMock.mockResolvedValue([
      { entity_id: "light.test", state: "on" },
      { entity_id: "switch.test", state: "off" },
    ]);
  } else {
    invokeHaCommandMock.mockResolvedValue({ ok: true });
  }
}

async function executeFactory(
  contract: AssistCommandRegistration,
  loadTool: () => Promise<{ execute: Function }>,
  args: Record<string, unknown>,
): Promise<{
  command: string;
  commandParams: Record<string, unknown>;
  result: unknown;
}> {
  const tool = await loadTool();
  const result = await tool.execute(
    "test-call",
    args,
    new AbortController().signal,
    () => undefined,
  );
  expect(invokeHaCommandMock).toHaveBeenCalledTimes(1);
  return {
    ...(invokeHaCommandMock.mock.calls[0][0] as {
    command: string;
    commandParams: Record<string, unknown>;
    }),
    result,
  };
}

describe("Assist executable mapping drift gate", () => {
  const registrations = resolvedAssistCommandRegistrations();

  for (const { contract, loadTool } of registrations) {
    it(`${contract.tool_name}: complete commandParams match contract mapping`, async () => {
      setupMocks(contract);
      const args = buildTestArgs(contract);
      const call = await executeFactory(contract, loadTool, args);

      expect(call.command).toBe(contract.node_command);
      expect(call.commandParams).toEqual(expectedCommandParams(contract, args));
    });
  }

  // --- ha_call_service alias_merge: both data and service_data map to node "data" ---

  it("ha_call_service: service_data normalized to data per contract emitted_params", async () => {
    const reg = registrations.find((r) => r.contract.tool_name === "ha_call_service")!;
    setupMocks(reg.contract);
    const args = {
      node: "test-hass",
      domain: "sentinel_domain",
      service: "sentinel_service",
      service_data: { sentinel_key: "from_service_data" },
    };
    const call = await executeFactory(reg.contract, reg.loadTool, args);
    expect(call.command).toBe(reg.contract.node_command);
    expect(call.commandParams).toEqual(expectedCommandParams(reg.contract, args));
    expect(call.commandParams.service_data).toBeUndefined();
  });

  // --- ha_list_states: all emitted_params are null-mapped ---

  it("ha_list_states: entity_filter not forwarded (null-mapped), complete params verified", async () => {
    const reg = registrations.find((r) => r.contract.tool_name === "ha_list_states")!;
    expect(reg.contract.client_side_params.entity_filter?.behavior).toBe(
      "glob_filter_result_by_entity_id",
    );
    expect(reg.contract.emitted_params.entity_filter).toBeNull();
    expect(reg.contract.emitted_params.node).toBeNull();

    setupMocks(reg.contract);
    const call = await executeFactory(reg.contract, reg.loadTool, {
      node: "test-hass",
      entity_filter: "light.*",
    });
    expect(call.command).toBe("ha.list_states");
    expect(call.commandParams).toEqual({});
    const result = call.result as { content?: Array<{ text?: string }> };
    expect(result.content?.[0]?.text).toContain("1/2 entities");
    expect(result.content?.[0]?.text).toContain("light.test");
    expect(result.content?.[0]?.text).not.toContain("switch.test");
  });
});

// ---------------------------------------------------------------------------
// Genuine mutation tests: clone and mutate contract mappings / transforms,
// prove the verifier would fail if the real contract or factory drifted.
// ---------------------------------------------------------------------------

describe("Assist mapping mutation regression", () => {
  const registrations = resolvedAssistCommandRegistrations();

  it("swapping ha_logbook start/end time targets produces wrong values", async () => {
    const reg = registrations.find((r) => r.contract.tool_name === "ha_logbook")!;
    setupMocks(reg.contract);
    const args = buildTestArgs(reg.contract);
    const call = await executeFactory(reg.contract, reg.loadTool, args);

    // Distinct sentinels:
    expect(args.start).not.toBe(args.end);
    // Correct (real factory):
    expect(call.commandParams.start_time).toBe(args.start);
    expect(call.commandParams.end_time).toBe(args.end);

    // Mutated contract would produce wrong expectations:
    const mutated = structuredClone(reg.contract);
    mutated.emitted_params.start = "end_time"; // swap!
    mutated.emitted_params.end = "start_time"; // swap!
    const mutatedExpected = expectedCommandParams(mutated, args);
    // The mutated expectation disagrees with reality:
    expect(mutatedExpected.start_time).toBe(args.end);
    expect(mutatedExpected.end_time).toBe(args.start);
    expect(call.commandParams).not.toEqual(mutatedExpected);
  });

  it("swapping ha_history start/end time targets produces wrong values", async () => {
    const reg = registrations.find((r) => r.contract.tool_name === "ha_history")!;
    setupMocks(reg.contract);
    const args = buildTestArgs(reg.contract);
    const call = await executeFactory(reg.contract, reg.loadTool, args);

    expect(args.start).not.toBe(args.end);
    expect(call.commandParams.start_time).toBe(args.start);
    expect(call.commandParams.end_time).toBe(args.end);

    const mutated = structuredClone(reg.contract);
    mutated.emitted_params.start = "end_time";
    mutated.emitted_params.end = "start_time";
    const mutatedExpected = expectedCommandParams(mutated, args);
    expect(call.commandParams).not.toEqual(mutatedExpected);
  });

  it("removing ha_history wrap_array transform produces flat value instead of array", async () => {
    const reg = registrations.find((r) => r.contract.tool_name === "ha_history")!;
    setupMocks(reg.contract);
    const args = buildTestArgs(reg.contract);
    const call = await executeFactory(reg.contract, reg.loadTool, args);

    // Real behavior: array wrapping
    expect(call.commandParams.entity_ids).toEqual([args.entity_id]);

    // Mutated: remove the transform, would expect flat rename
    const mutated = structuredClone(reg.contract);
    mutated.value_transforms = {};
    const mutatedExpected = expectedCommandParams(mutated, args);
    // Mutated expects flat string, real factory produces array:
    expect(mutatedExpected.entity_ids).toBe(args.entity_id);
    expect(call.commandParams).not.toEqual(mutatedExpected);
  });

  it("swapping ha_light_turn_on entity_id and area_id targets produces wrong values", async () => {
    const reg = registrations.find((r) => r.contract.tool_name === "ha_light_turn_on")!;
    setupMocks(reg.contract);
    const args = buildTestArgs(reg.contract);
    const call = await executeFactory(reg.contract, reg.loadTool, args);

    expect(args.entity_id).not.toBe(args.area_id);
    expect(call.commandParams.entity_id).toBe(args.entity_id);
    expect(call.commandParams.area_id).toBe(args.area_id);

    const mutated = structuredClone(reg.contract);
    mutated.emitted_params.entity_id = "area_id"; // swap!
    mutated.emitted_params.area_id = "entity_id"; // swap!
    const mutatedExpected = expectedCommandParams(mutated, args);
    expect(call.commandParams).not.toEqual(mutatedExpected);
  });

  it("swapping ha_calendar_get_events start/end_date_time targets produces wrong values", async () => {
    const reg = registrations.find((r) => r.contract.tool_name === "ha_calendar_get_events")!;
    setupMocks(reg.contract);
    const args = buildTestArgs(reg.contract);
    const call = await executeFactory(reg.contract, reg.loadTool, args);

    expect(args.start_date_time).not.toBe(args.end_date_time);
    const mutated = structuredClone(reg.contract);
    mutated.emitted_params.start_date_time = "end_date_time";
    mutated.emitted_params.end_date_time = "start_date_time";
    const mutatedExpected = expectedCommandParams(mutated, args);
    expect(call.commandParams).not.toEqual(mutatedExpected);
  });

  it("injected $policy.adminToken verified with exact sentinel, not expect.anything()", async () => {
    const reg = registrations.find((r) => r.contract.tool_name === "ha_reload_config")!;
    expect(reg.contract.injected_node_params["$policy.adminToken"]).toBe("admin_token");
    setupMocks(reg.contract);
    const args = buildTestArgs(reg.contract);
    const call = await executeFactory(reg.contract, reg.loadTool, args);
    // Exact value from policy, not expect.anything():
    expect(call.commandParams.admin_token).toBe(ADMIN_TOKEN_SENTINEL);
  });
});

// ---------------------------------------------------------------------------
// Contract validation mutation tests: clone the real contract JSON, mutate it,
// and prove resolvedAssistCommandRegistrations() rejects the mutation.
// ---------------------------------------------------------------------------

type RawRegistration = {
  tool_name: string;
  accepted_tool_params: string[];
  emitted_params: Record<string, string | null>;
  injected_node_params: Record<string, string>;
  value_transforms: Record<
    string,
    { source_tool_key: string; transform: string; description: string }
  >;
  client_side_params?: Record<string, { behavior: string; description: string }>;
};

function rawRegistration(toolName: string): RawRegistration {
  const registration = assistCommandContract.registrations.find(
    (entry) => entry.tool_name === toolName,
  );
  if (!registration) throw new Error(`Missing test registration ${toolName}`);
  return structuredClone(registration) as unknown as RawRegistration;
}

describe("Contract schema validation rejects mutations", () => {
  it("rejects an unsupported transform name", () => {
    const registration = rawRegistration("ha_history");
    registration.value_transforms.entity_ids!.transform = "unsupported";
    expect(() => parseAssistCommandRegistration(registration)).toThrow(/not in allowed set/);
  });

  it("rejects an orphan transform target", () => {
    const registration = rawRegistration("ha_history");
    registration.value_transforms.orphan = registration.value_transforms.entity_ids!;
    delete registration.value_transforms.entity_ids;
    expect(() => parseAssistCommandRegistration(registration)).toThrow(
      /not an emitted non-null node key/,
    );
  });

  it("rejects a transform source that is not accepted", () => {
    const registration = rawRegistration("ha_history");
    registration.value_transforms.entity_ids!.source_tool_key = "not_accepted";
    expect(() => parseAssistCommandRegistration(registration)).toThrow(
      /not an accepted tool param/,
    );
  });

  it("rejects wrap_array whose source maps to another target", () => {
    const registration = rawRegistration("ha_history");
    registration.value_transforms.entity_ids!.source_tool_key = "start";
    expect(() => parseAssistCommandRegistration(registration)).toThrow(
      /must map to 'entity_ids'/,
    );
  });

  it("rejects alias_merge whose source maps to another target", () => {
    const registration = rawRegistration("ha_call_service");
    registration.value_transforms.data!.source_tool_key = "data|target";
    expect(() => parseAssistCommandRegistration(registration)).toThrow(
      /alias_merge source 'target' must map to 'data'/,
    );
  });

  it("rejects an unsupported injected policy source", () => {
    const registration = rawRegistration("ha_reload_config");
    registration.injected_node_params = { "$policy.notAdminToken": "admin_token" };
    expect(() => parseAssistCommandRegistration(registration)).toThrow(
      /injected source.*not supported/,
    );
  });

  it("rejects removal of required client-side semantics metadata", () => {
    const registration = rawRegistration("ha_list_states");
    registration.client_side_params = {};
    expect(() => parseAssistCommandRegistration(registration)).toThrow(
      /null-mapped tool param 'entity_filter' requires/,
    );
  });

  it("rejects unsupported client-side behavior", () => {
    const registration = rawRegistration("ha_list_states");
    registration.client_side_params!.entity_filter!.behavior = "ignore_filter";
    expect(() => parseAssistCommandRegistration(registration)).toThrow(
      /malformed or unsupported/,
    );
  });
});
