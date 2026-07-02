// Tests for ha_get_state.

import { afterEach, describe, expect, it, vi } from "vitest";
import { HA_GET_STATE_TOOL_DESCRIPTOR } from "./descriptors.js";

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

async function loadTool() {
  const mod = await import("./ha-get-state-tool.js");
  return mod.createHaGetStateTool();
}

afterEach(() => {
  invokeMock.mockReset();
  resolveMock.mockReset();
});

describe("ha_get_state descriptor", () => {
  it("declares the expected tool name", () => {
    expect(HA_GET_STATE_TOOL_DESCRIPTOR.name).toBe("ha_get_state");
  });
});

describe("ha_get_state execute", () => {
  it("reads state and forwards to the node", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: {},
    });
    invokeMock.mockResolvedValue({ state: "on", attributes: {} });

    const tool = await loadTool();
    const result = await tool.execute(
      "call-1",
      { node: "hass", entity_id: "light.living_room" },
      new AbortController().signal,
      () => undefined,
    );

    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock.mock.calls[0]?.[0]).toMatchObject({
      command: "ha.get_state",
      commandParams: { entity_id: "light.living_room" },
    });
    expect(result.isError).toBeUndefined();
  });

  it("forwards any valid entity_id (no policy filter at tool layer)", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: {},
    });
    invokeMock.mockResolvedValue({ state: "home" });

    const tool = await loadTool();
    const result = await tool.execute(
      "call-2",
      { node: "hass", entity_id: "person.rob" },
      new AbortController().signal,
      () => undefined,
    );

    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(result.isError).toBeUndefined();
  });

  it("throws when entity_id missing", async () => {
    const tool = await loadTool();
    await expect(
      tool.execute(
        "call-4",
        { node: "hass" },
        new AbortController().signal,
        () => undefined,
      ),
    ).rejects.toThrow(/entity_id required/);
  });
});
