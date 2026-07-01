// Tests for ha_logbook / ha_history.

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

const TOOLS = [
  {
    name: "ha_logbook",
    command: "ha.logbook",
    load: async () =>
      (
        await import("./ha-entity-scoped-read-tools.js")
      ).createHaLogbookTool(),
  },
  {
    name: "ha_history",
    command: "ha.history",
    load: async () =>
      (
        await import("./ha-entity-scoped-read-tools.js")
      ).createHaHistoryTool(),
  },
];

for (const T of TOOLS) {
  describe(T.name, () => {
    it("declares the expected tool name", async () => {
      const tool = await T.load();
      expect(tool.name).toBe(T.name);
    });

    it("forwards when entity_id matches allowReadEntities", async () => {
      resolveMock.mockResolvedValue({
        nodeId: "hass-001",
        nodeDisplayName: "Hass",
        policy: { allowReadEntities: ["light.*"] },
      });
      invokeMock.mockResolvedValue([]);
      const tool = await T.load();
      const result = await tool.execute(
        "c",
        {
          node: "hass",
          entity_id: "light.kitchen",
          start: "2026-07-01T00:00:00",
          end: "2026-07-02T00:00:00",
        },
        new AbortController().signal,
        () => undefined,
      );
      expect(invokeMock).toHaveBeenCalledTimes(1);
      const expectedParams =
        T.command === "ha.history"
          ? {
              entity_ids: ["light.kitchen"],
              start_time: "2026-07-01T00:00:00",
              end_time: "2026-07-02T00:00:00",
            }
          : {
              entity_id: "light.kitchen",
              start_time: "2026-07-01T00:00:00",
              end_time: "2026-07-02T00:00:00",
            };
      expect(invokeMock.mock.calls[0]?.[0]).toMatchObject({
        command: T.command,
        commandParams: expectedParams,
      });
      // Sanity: the raw Assist keys must NOT be forwarded to the node.
      const forwarded = invokeMock.mock.calls[0]?.[0]?.commandParams ?? {};
      expect(forwarded).not.toHaveProperty("start");
      expect(forwarded).not.toHaveProperty("end");
      if (T.command === "ha.history") {
        expect(forwarded).not.toHaveProperty("entity_id");
      }
      expect(result.isError).toBeUndefined();
    });

    it("refuses when entity_id doesn't match allowReadEntities", async () => {
      resolveMock.mockResolvedValue({
        nodeId: "hass-001",
        nodeDisplayName: "Hass",
        policy: { allowReadEntities: ["light.*"] },
      });
      const tool = await T.load();
      const result = await tool.execute(
        "c",
        { node: "hass", entity_id: "person.rob" },
        new AbortController().signal,
        () => undefined,
      );
      expect(invokeMock).not.toHaveBeenCalled();
      expect(result.isError).toBe(true);
    });

    it("refuses no-entity_id call when allowReadEntities empty", async () => {
      resolveMock.mockResolvedValue({
        nodeId: "hass-001",
        nodeDisplayName: "Hass",
        policy: {},
      });
      const tool = await T.load();
      const result = await tool.execute(
        "c",
        { node: "hass" },
        new AbortController().signal,
        () => undefined,
      );
      expect(invokeMock).not.toHaveBeenCalled();
      expect(result.isError).toBe(true);
    });

    it("allows no-entity_id call when allowReadEntities is non-empty", async () => {
      resolveMock.mockResolvedValue({
        nodeId: "hass-001",
        nodeDisplayName: "Hass",
        policy: { allowReadEntities: ["light.*"] },
      });
      invokeMock.mockResolvedValue([]);
      const tool = await T.load();
      const result = await tool.execute(
        "c",
        { node: "hass" },
        new AbortController().signal,
        () => undefined,
      );
      expect(invokeMock).toHaveBeenCalledTimes(1);
      expect(result.isError).toBeUndefined();
    });
  });
}
