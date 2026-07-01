// Tests for ha_list_states.

import { afterEach, describe, expect, it, vi } from "vitest";
import { HA_LIST_STATES_TOOL_DESCRIPTOR } from "./descriptors.js";

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
  const mod = await import("./ha-list-states-tool.js");
  return mod.createHaListStatesTool();
}

afterEach(() => {
  invokeMock.mockReset();
  resolveMock.mockReset();
});

const SAMPLE_STATES = [
  { entity_id: "light.kitchen", state: "on" },
  { entity_id: "light.bedroom", state: "off" },
  { entity_id: "sensor.outdoor_temp", state: "72" },
  { entity_id: "sensor.secret_token", state: "xyz" },
  { entity_id: "lock.front_door", state: "locked" },
];

describe("ha_list_states descriptor", () => {
  it("declares the expected tool name", () => {
    expect(HA_LIST_STATES_TOOL_DESCRIPTOR.name).toBe("ha_list_states");
  });
});

describe("ha_list_states execute", () => {
  it("refuses when no allowReadEntities configured", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: { allowReadEntities: [] },
    });

    const tool = await loadTool();
    const result = await tool.execute(
      "call-1",
      { node: "hass" },
      new AbortController().signal,
      () => undefined,
    );

    expect(invokeMock).not.toHaveBeenCalled();
    expect(result.isError).toBe(true);
  });

  it("filters results through allowReadEntities + denyReadEntities", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: {
        allowReadEntities: ["light.*", "sensor.*"],
        denyReadEntities: ["sensor.secret_*"],
      },
    });
    invokeMock.mockResolvedValue(SAMPLE_STATES);

    const tool = await loadTool();
    const result = await tool.execute(
      "call-2",
      { node: "hass" },
      new AbortController().signal,
      () => undefined,
    );

    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(result.isError).toBeUndefined();
    const text = result.content
      .map((c) => (c.type === "text" ? c.text : ""))
      .join("");
    expect(text).toContain("light.kitchen");
    expect(text).toContain("light.bedroom");
    expect(text).toContain("sensor.outdoor_temp");
    expect(text).not.toContain("sensor.secret_token");
    expect(text).not.toContain("lock.front_door");
  });

  it("applies optional entity_filter on top of allowReadEntities", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: { allowReadEntities: ["light.*", "sensor.*"] },
    });
    invokeMock.mockResolvedValue(SAMPLE_STATES);

    const tool = await loadTool();
    const result = await tool.execute(
      "call-3",
      { node: "hass", entity_filter: "light.*" },
      new AbortController().signal,
      () => undefined,
    );

    const text = result.content
      .map((c) => (c.type === "text" ? c.text : ""))
      .join("");
    expect(text).toContain("light.kitchen");
    expect(text).toContain("light.bedroom");
    expect(text).not.toContain("sensor.outdoor_temp");
  });

  it("unwraps payload.states when the node returns a wrapped object", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: { allowReadEntities: ["light.*"] },
    });
    invokeMock.mockResolvedValue({ states: SAMPLE_STATES });

    const tool = await loadTool();
    const result = await tool.execute(
      "call-4",
      { node: "hass" },
      new AbortController().signal,
      () => undefined,
    );

    const text = result.content
      .map((c) => (c.type === "text" ? c.text : ""))
      .join("");
    expect(text).toContain("light.kitchen");
    expect(text).toContain("light.bedroom");
    expect(text).not.toContain("sensor.outdoor_temp");
  });
});
