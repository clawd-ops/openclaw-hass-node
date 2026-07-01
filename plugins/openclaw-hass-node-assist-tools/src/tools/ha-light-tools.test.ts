// Tests for ha_light_turn_on / ha_light_turn_off.

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

const T = [
  {
    name: "ha_light_turn_on",
    service: "light.turn_on",
    command: "ha.light_turn_on",
    load: async () =>
      (await import("./ha-light-tools.js")).createHaLightTurnOnTool(),
  },
  {
    name: "ha_light_turn_off",
    service: "light.turn_off",
    command: "ha.light_turn_off",
    load: async () =>
      (await import("./ha-light-tools.js")).createHaLightTurnOffTool(),
  },
];

for (const t of T) {
  describe(t.name, () => {
    it("declares the expected tool name", async () => {
      const tool = await t.load();
      expect(tool.name).toBe(t.name);
    });

    it("forwards when allowServices permits the service", async () => {
      resolveMock.mockResolvedValue({
        nodeId: "hass-001",
        nodeDisplayName: "Hass",
        policy: { allowServices: ["light.*"] },
      });
      invokeMock.mockResolvedValue({ ok: true });
      const tool = await t.load();
      const result = await tool.execute(
        "c",
        { node: "hass", entity_id: "light.kitchen" },
        new AbortController().signal,
        () => undefined,
      );
      expect(invokeMock).toHaveBeenCalledTimes(1);
      expect(invokeMock.mock.calls[0]?.[0]).toMatchObject({
        command: t.command,
        commandParams: { entity_id: "light.kitchen" },
      });
      expect(result.isError).toBeUndefined();
    });

    it("refuses when service is not permitted", async () => {
      resolveMock.mockResolvedValue({
        nodeId: "hass-001",
        nodeDisplayName: "Hass",
        policy: { allowServices: ["switch.*"] },
      });
      const tool = await t.load();
      const result = await tool.execute(
        "c",
        { node: "hass", entity_id: "light.kitchen" },
        new AbortController().signal,
        () => undefined,
      );
      expect(invokeMock).not.toHaveBeenCalled();
      expect(result.isError).toBe(true);
    });

    it("refuses when no target (entity_id/area_id/device_id) provided", async () => {
      resolveMock.mockResolvedValue({
        nodeId: "hass-001",
        nodeDisplayName: "Hass",
        policy: { allowServices: ["light.*"] },
      });
      const tool = await t.load();
      const result = await tool.execute(
        "c",
        { node: "hass" },
        new AbortController().signal,
        () => undefined,
      );
      expect(invokeMock).not.toHaveBeenCalled();
      expect(result.isError).toBe(true);
    });
  });
}
