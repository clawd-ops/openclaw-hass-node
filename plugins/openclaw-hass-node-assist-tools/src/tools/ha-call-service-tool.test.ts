// Tests for ha_call_service. The OC plugin-sdk imports inside
// node-tool-invoke.ts are mocked so these tests don't require a live
// gateway.

import { afterEach, describe, expect, it, vi } from "vitest";
import { HA_CALL_SERVICE_TOOL_DESCRIPTOR, HaCallServiceToolSchema } from "./descriptors.js";

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
  const mod = await import("./ha-call-service-tool.js");
  return mod.createHaCallServiceTool();
}

afterEach(() => {
  invokeMock.mockReset();
  resolveMock.mockReset();
});

describe("ha_call_service descriptor", () => {
  it("declares the expected tool name", () => {
    expect(HA_CALL_SERVICE_TOOL_DESCRIPTOR.name).toBe("ha_call_service");
  });
  it("declares a TypeBox parameters schema", () => {
    expect(HaCallServiceToolSchema).toBeDefined();
    expect(typeof HaCallServiceToolSchema).toBe("object");
  });
});

describe("ha_call_service execute", () => {
  it("invokes ha.call_service and forwards to the node", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: {},
    });
    invokeMock.mockResolvedValue({ ok: true });

    const tool = await loadTool();
    const result = await tool.execute(
      "call-1",
      {
        node: "hass",
        domain: "light",
        service: "turn_on",
        target: { entity_id: "light.living_room" },
      },
      new AbortController().signal,
      () => undefined,
    );

    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock.mock.calls[0]?.[0]).toMatchObject({
      nodeId: "hass-001",
      command: "ha.call_service",
      commandParams: expect.objectContaining({ domain: "light", service: "turn_on" }),
    });
    expect(result.isError).toBeUndefined();
  });

  it("throws when required params are missing", async () => {
    const tool = await loadTool();
    await expect(
      tool.execute(
        "call-4",
        { node: "hass", service: "turn_on" },
        new AbortController().signal,
        () => undefined,
      ),
    ).rejects.toThrow(/domain required/);
  });
});
