// Parametrized tests for every simple bulk / metadata read tool built
// on createHaMetadataReadTool. Verifies name/command wiring, the
// coarse-bind refusal path, and that extra params (slug, lines,
// include_traces) get forwarded correctly.

import { afterEach, describe, expect, it, vi } from "vitest";
import { Value } from "typebox/value";
import { HA_LIST_AUTOMATIONS_TOOL_DESCRIPTOR } from "./descriptors.js";

const invokeMock = vi.fn();
const resolveMock = vi.fn();

vi.mock("./node-tool-invoke.js", () => ({
  PLUGIN_ID: "openclaw-hass-node-assist-tools",
  invokeHaCommand: (...args: unknown[]) => invokeMock(...args),
  resolveNodeAndPolicy: (...args: unknown[]) => resolveMock(...args),
  readGatewayCallOptions: () => ({}),
  readTrimmedString: (params: Record<string, unknown>, key: string) => {
    const v = params[key];
    return typeof v === "string" ? v.trim() : "";
  },
}));

afterEach(() => {
  invokeMock.mockReset();
  resolveMock.mockReset();
});

type Case = {
  name: string;
  command: string;
  factory: keyof typeof import("./ha-simple-read-tools.js");
  args: Record<string, unknown>;
  expectedParams: Record<string, unknown>;
};

const CASES: Case[] = [
  { name: "ha_list_services", command: "ha.list_services", factory: "createHaListServicesTool", args: {}, expectedParams: {} },
  { name: "ha_get_config", command: "ha.get_config", factory: "createHaGetConfigTool", args: {}, expectedParams: {} },
  { name: "ha_list_events", command: "ha.list_events", factory: "createHaListEventsTool", args: {}, expectedParams: {} },
  { name: "ha_list_config_entries", command: "ha.list_config_entries", factory: "createHaListConfigEntriesTool", args: {}, expectedParams: {} },
  { name: "ha_check_config", command: "ha.check_config", factory: "createHaCheckConfigTool", args: {}, expectedParams: {} },
  { name: "ha_list_addons", command: "ha.list_addons", factory: "createHaListAddonsTool", args: {}, expectedParams: {} },
  { name: "ha_list_automations", command: "ha.list_automations", factory: "createHaListAutomationsTool", args: { include_traces: true, entity_filter: "automation.morning_*", state_filter: "on" }, expectedParams: { include_traces: true, entity_filter: "automation.morning_*", state_filter: "on" } },
  { name: "ha_core_logs", command: "ha.core_logs", factory: "createHaCoreLogsTool", args: { lines: 500 }, expectedParams: { lines: 500 } },
  { name: "ha_addon_logs", command: "ha.addon_logs", factory: "createHaAddonLogsTool", args: { slug: "openclaw-hass-node", lines: 200 }, expectedParams: { slug: "openclaw-hass-node", lines: 200 } },
  { name: "ha_addon_info", command: "ha.addon_info", factory: "createHaAddonInfoTool", args: { slug: "openclaw-hass-node" }, expectedParams: { slug: "openclaw-hass-node" } },
  { name: "ha_addon_stats", command: "ha.addon_stats", factory: "createHaAddonStatsTool", args: { slug: "openclaw-hass-node" }, expectedParams: { slug: "openclaw-hass-node" } },
  { name: "ha_addon_changelog", command: "ha.addon_changelog", factory: "createHaAddonChangelogTool", args: { slug: "openclaw-hass-node" }, expectedParams: { slug: "openclaw-hass-node" } },
  { name: "ha_addon_documentation", command: "ha.addon_documentation", factory: "createHaAddonDocumentationTool", args: { slug: "openclaw-hass-node" }, expectedParams: { slug: "openclaw-hass-node" } },
  { name: "ha_supervisor_info", command: "ha.supervisor_info", factory: "createHaSupervisorInfoTool", args: {}, expectedParams: {} },
];

async function load(factory: string) {
  const mod = await import("./ha-simple-read-tools.js");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (mod as any)[factory]();
}

for (const c of CASES) {
  describe(c.name, () => {
    it("declares the expected tool name", async () => {
      const tool = await load(c.factory);
      expect(tool.name).toBe(c.name);
    });

    it("forwards the command with the expected params when bound", async () => {
      resolveMock.mockResolvedValue({
        nodeId: "hass-001",
        nodeDisplayName: "Hass",
        policy: {},
      });
      invokeMock.mockResolvedValue({ ok: true });
      const tool = await load(c.factory);
      const result = await tool.execute(
        "call-1",
        { node: "hass", ...c.args },
        new AbortController().signal,
        () => undefined,
      );
      expect(invokeMock).toHaveBeenCalledTimes(1);
      expect(invokeMock.mock.calls[0]?.[0]).toMatchObject({
        command: c.command,
        commandParams: c.expectedParams,
      });
      expect(result.isError).toBeUndefined();
    });

    it("forwards even when no per-node policy is configured (routing-only)", async () => {
      resolveMock.mockResolvedValue({
        nodeId: "hass-001",
        nodeDisplayName: "Hass",
        policy: undefined,
      });
      invokeMock.mockResolvedValue({ ok: true });
      const tool = await load(c.factory);
      const result = await tool.execute(
        "call-2",
        { node: "hass", ...c.args },
        new AbortController().signal,
        () => undefined,
      );
      expect(invokeMock).toHaveBeenCalledTimes(1);
      expect(result.isError).toBeUndefined();
    });
  });
}

// Fail-closed regression tests for #281 review finding #1:
// Empty/whitespace/wrong-type narrowing params must never be silently
// dropped by the Assist wrapper. The wrapper forwards each caller-supplied
// value verbatim so the Python handler is the single source of truth and
// rejects with INVALID_PARAM.
describe("ha_list_automations forwards narrowing params verbatim", () => {
  const failClosedCases: Array<{
    label: string;
    args: Record<string, unknown>;
    expectedParams: Record<string, unknown>;
  }> = [
    {
      label: "empty entity_filter is forwarded (not dropped)",
      args: { entity_filter: "" },
      expectedParams: { entity_filter: "" },
    },
    {
      label: "whitespace entity_filter is forwarded verbatim (no trim)",
      args: { entity_filter: "   " },
      expectedParams: { entity_filter: "   " },
    },
    {
      label: "empty state_filter is forwarded (not dropped)",
      args: { state_filter: "" },
      expectedParams: { state_filter: "" },
    },
    {
      label: "whitespace state_filter is forwarded verbatim (no trim)",
      args: { state_filter: "   " },
      expectedParams: { state_filter: "   " },
    },
    {
      label: "padded entity_filter is forwarded without normalization",
      args: { entity_filter: "  automation.morning_*  " },
      expectedParams: { entity_filter: "  automation.morning_*  " },
    },
    {
      label: "non-boolean include_traces is forwarded (not silently dropped)",
      args: { include_traces: "true" },
      expectedParams: { include_traces: "true" },
    },
  ];

  for (const c of failClosedCases) {
    it(c.label, async () => {
      resolveMock.mockResolvedValue({
        nodeId: "hass-001",
        nodeDisplayName: "Hass",
        policy: {},
      });
      invokeMock.mockResolvedValue({ ok: true });
      const tool = await load("createHaListAutomationsTool");
      await tool.execute(
        "call-forward",
        { node: "hass", ...c.args },
        new AbortController().signal,
        () => undefined,
      );
      expect(invokeMock).toHaveBeenCalledTimes(1);
      expect(invokeMock.mock.calls[0]?.[0]).toMatchObject({
        command: "ha.list_automations",
        commandParams: c.expectedParams,
      });
    });
  }

  it("omits filters that were not supplied by the caller", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: {},
    });
    invokeMock.mockResolvedValue({ ok: true });
    const tool = await load("createHaListAutomationsTool");
    await tool.execute(
      "call-absent",
      { node: "hass" },
      new AbortController().signal,
      () => undefined,
    );
    const commandParams = invokeMock.mock.calls[0]?.[0]?.commandParams;
    expect(commandParams).toEqual({});
  });

  it("rejects unknown arguments at the schema boundary", () => {
    expect(
      Value.Check(HA_LIST_AUTOMATIONS_TOOL_DESCRIPTOR.parameters, {
        node: "hass",
        entity_fiter: "automation.__openclaw_audit_no_match__",
      }),
    ).toBe(false);
  });

  it("rejects a misspelled filter before invoking an unfiltered node command", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: {},
    });
    invokeMock.mockResolvedValue({ ok: true });
    const tool = await load("createHaListAutomationsTool");

    await expect(
      tool.execute(
        "call-unknown-filter",
        {
          node: "hass",
          entity_fiter: "automation.__openclaw_audit_no_match__",
        },
        new AbortController().signal,
        () => undefined,
      ),
    ).rejects.toThrow(/INVALID_PARAM.*unknown.*entity_fiter/i);
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("rejects an oversize state_filter at the schema boundary", () => {
    expect(
      Value.Check(HA_LIST_AUTOMATIONS_TOOL_DESCRIPTOR.parameters, {
        node: "hass",
        state_filter: "x".repeat(257),
      }),
    ).toBe(false);
  });

  it("rejects an oversize state_filter before invoking the node command", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: {},
    });
    invokeMock.mockResolvedValue({ ok: true });
    const tool = await load("createHaListAutomationsTool");

    await expect(
      tool.execute(
        "call-oversize-state-filter",
        { node: "hass", state_filter: "x".repeat(257) },
        new AbortController().signal,
        () => undefined,
      ),
    ).rejects.toThrow(/INVALID_PARAM.*state_filter exceeds 256 chars/);
    expect(invokeMock).not.toHaveBeenCalled();
  });
});

describe("addon tools reject missing slug", () => {
  it("ha_addon_logs throws when slug missing", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: {},
    });
    const tool = await load("createHaAddonLogsTool");
    await expect(
      tool.execute(
        "c",
        { node: "hass" },
        new AbortController().signal,
        () => undefined,
      ),
    ).rejects.toThrow(/slug required/);
  });
});
