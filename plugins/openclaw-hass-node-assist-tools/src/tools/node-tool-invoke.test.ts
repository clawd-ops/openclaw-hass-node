// Tests for node-tool-invoke helpers.
// Mocks the plugin-sdk so no live gateway is needed.

import { afterEach, describe, expect, it, vi } from "vitest";

const callGatewayToolMock = vi.fn();
const listNodesMock = vi.fn();
const resolveNodeIdFromListMock = vi.fn();
const resolvePluginConfigObjectMock = vi.fn();

vi.mock("openclaw/plugin-sdk/agent-harness-runtime", () => ({
  callGatewayTool: (...args: unknown[]) => callGatewayToolMock(...args),
  listNodes: (...args: unknown[]) => listNodesMock(...args),
  resolveNodeIdFromList: (...args: unknown[]) => resolveNodeIdFromListMock(...args),
}));

vi.mock("openclaw/plugin-sdk/plugin-config-runtime", () => ({
  resolvePluginConfigObject: (...args: unknown[]) => resolvePluginConfigObjectMock(...args),
}));

async function loadModule() {
  return await import("./node-tool-invoke.js");
}

afterEach(() => {
  callGatewayToolMock.mockReset();
});

describe("invokeHaCommand", () => {
  it("includes idempotencyKey in the node.invoke call", async () => {
    callGatewayToolMock.mockResolvedValue({ payload: { state: "on" } });

    const { invokeHaCommand } = await loadModule();
    await invokeHaCommand({
      nodeId: "hass-001",
      command: "ha.get_state",
      commandParams: { entity_id: "sensor.date" },
      gatewayOpts: {},
    });

    expect(callGatewayToolMock).toHaveBeenCalledTimes(1);
    const [toolName, , params] = callGatewayToolMock.mock.calls[0];
    expect(toolName).toBe("node.invoke");
    expect(params).toMatchObject({
      nodeId: "hass-001",
      command: "ha.get_state",
      params: { entity_id: "sensor.date" },
    });
    // idempotencyKey must be a UUID (8-4-4-4-12 hex groups)
    expect(typeof params.idempotencyKey).toBe("string");
    expect(params.idempotencyKey).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    );
  });

  it("generates a unique idempotencyKey for each call", async () => {
    callGatewayToolMock.mockResolvedValue({ payload: {} });

    const { invokeHaCommand } = await loadModule();
    const opts = { nodeId: "hass-001", command: "ha.list_states", commandParams: {}, gatewayOpts: {} };
    await invokeHaCommand(opts);
    await invokeHaCommand(opts);

    expect(callGatewayToolMock).toHaveBeenCalledTimes(2);
    const key1 = callGatewayToolMock.mock.calls[0][2].idempotencyKey;
    const key2 = callGatewayToolMock.mock.calls[1][2].idempotencyKey;
    expect(key1).not.toBe(key2);
  });

  it("returns payload from gateway response", async () => {
    callGatewayToolMock.mockResolvedValue({ payload: { state: "unavailable" } });

    const { invokeHaCommand } = await loadModule();
    const result = await invokeHaCommand({
      nodeId: "hass-001",
      command: "ha.get_state",
      commandParams: { entity_id: "sensor.date" },
      gatewayOpts: {},
    });

    expect(result).toEqual({ state: "unavailable" });
  });

  it("throws when gateway returns ok:false", async () => {
    callGatewayToolMock.mockResolvedValue({ ok: false, error: "node command failed" });

    const { invokeHaCommand } = await loadModule();
    await expect(
      invokeHaCommand({
        nodeId: "hass-001",
        command: "ha.get_state",
        commandParams: { entity_id: "sensor.date" },
        gatewayOpts: {},
      }),
    ).rejects.toThrow("node command failed");
  });
});
