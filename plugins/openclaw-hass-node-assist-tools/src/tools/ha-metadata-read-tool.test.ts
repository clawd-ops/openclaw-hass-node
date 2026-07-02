// Tests for the metadata-read tools: ha_list_areas, ha_list_devices,
// ha_list_entity_registry — all use createHaMetadataReadTool().

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

const TOOL_LOADERS = [
  {
    name: "ha_list_areas",
    command: "ha.list_areas",
    load: async () =>
      (await import("./ha-list-areas-tool.js")).createHaListAreasTool(),
    sample: [{ area_id: "kitchen", name: "Kitchen" }],
  },
  {
    name: "ha_list_devices",
    command: "ha.list_devices",
    load: async () =>
      (await import("./ha-list-devices-tool.js")).createHaListDevicesTool(),
    sample: [{ id: "dev1", name: "Hue Bulb" }],
  },
  {
    name: "ha_list_entity_registry",
    command: "ha.list_entity_registry",
    load: async () =>
      (
        await import("./ha-list-entity-registry-tool.js")
      ).createHaListEntityRegistryTool(),
    sample: [{ entity_id: "light.kitchen", platform: "hue" }],
  },
];

for (const T of TOOL_LOADERS) {
  describe(`${T.name} (metadata read)`, () => {
    it("declares the expected tool name", async () => {
      const tool = await T.load();
      expect(tool.name).toBe(T.name);
    });

    it("invokes the corresponding ha.* command when bound to the node", async () => {
      resolveMock.mockResolvedValue({
        nodeId: "hass-001",
        nodeDisplayName: "Hass",
        policy: {},
      });
      invokeMock.mockResolvedValue(T.sample);

      const tool = await T.load();
      const result = await tool.execute(
        "call-1",
        { node: "hass" },
        new AbortController().signal,
        () => undefined,
      );

      expect(invokeMock).toHaveBeenCalledTimes(1);
      expect(invokeMock.mock.calls[0]?.[0]).toMatchObject({
        command: T.command,
        commandParams: {},
      });
      expect(result.isError).toBeUndefined();
    });

    it("forwards even when no per-node policy is configured (routing-only)", async () => {
      resolveMock.mockResolvedValue({
        nodeId: "hass-001",
        nodeDisplayName: "Hass",
        policy: undefined,
      });
      invokeMock.mockResolvedValue(T.sample);

      const tool = await T.load();
      const result = await tool.execute(
        "call-2",
        { node: "hass" },
        new AbortController().signal,
        () => undefined,
      );

      expect(invokeMock).toHaveBeenCalledTimes(1);
      expect(result.isError).toBeUndefined();
    });

    it("throws when node missing", async () => {
      const tool = await T.load();
      await expect(
        tool.execute(
          "call-3",
          {},
          new AbortController().signal,
          () => undefined,
        ),
      ).rejects.toThrow(/node required/);
    });
  });
}
