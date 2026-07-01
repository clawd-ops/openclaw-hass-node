// Parametrized tests for every simple bulk / metadata read tool built
// on createHaMetadataReadTool. Verifies name/command wiring, the
// coarse-bind refusal path, and that extra params (slug, lines,
// include_traces) get forwarded correctly.

import { afterEach, describe, expect, it, vi } from "vitest";

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
  { name: "ha_list_automations", command: "ha.list_automations", factory: "createHaListAutomationsTool", args: { include_traces: true }, expectedParams: { include_traces: true } },
  { name: "ha_core_logs", command: "ha.core_logs", factory: "createHaCoreLogsTool", args: { lines: 500 }, expectedParams: { lines: 500 } },
  { name: "ha_addon_logs", command: "ha.addon_logs", factory: "createHaAddonLogsTool", args: { slug: "openclaw-hass-node", lines: 200 }, expectedParams: { slug: "openclaw-hass-node", lines: 200 } },
  { name: "ha_addon_info", command: "ha.addon_info", factory: "createHaAddonInfoTool", args: { slug: "openclaw-hass-node" }, expectedParams: { slug: "openclaw-hass-node" } },
  { name: "ha_addon_stats", command: "ha.addon_stats", factory: "createHaAddonStatsTool", args: { slug: "openclaw-hass-node" }, expectedParams: { slug: "openclaw-hass-node" } },
  { name: "ha_addon_changelog", command: "ha.addon_changelog", factory: "createHaAddonChangelogTool", args: { slug: "openclaw-hass-node" }, expectedParams: { slug: "openclaw-hass-node" } },
  { name: "ha_addon_documentation", command: "ha.addon_documentation", factory: "createHaAddonDocumentationTool", args: { slug: "openclaw-hass-node" }, expectedParams: { slug: "openclaw-hass-node" } },
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

    it("refuses when no per-node policy is configured", async () => {
      resolveMock.mockResolvedValue({
        nodeId: "hass-001",
        nodeDisplayName: "Hass",
        policy: undefined,
      });
      const tool = await load(c.factory);
      const result = await tool.execute(
        "call-2",
        { node: "hass", ...c.args },
        new AbortController().signal,
        () => undefined,
      );
      expect(invokeMock).not.toHaveBeenCalled();
      expect(result.isError).toBe(true);
    });
  });
}

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
