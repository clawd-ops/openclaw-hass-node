import { describe, expect, it, vi } from "vitest";

vi.mock("openclaw/plugin-sdk/plugin-config", () => ({
  readPluginConfig: async () => ({}),
}));

const { createAssistToolsNodeInvokePolicy } = await import(
  "./node-invoke-policy.js"
);

type Ctx = {
  command: string;
  nodeId: string;
  params?: unknown;
  pluginConfig?: unknown;
};

const nodeConfig = {
  nodes: {
    "node-1": {
      allowServices: ["light.*"],
      denyServices: ["light.delete"],
      allowReadEntities: ["sensor.*", "light.kitchen"],
      denyReadEntities: ["sensor.secret_*"],
      allowCalendars: ["calendar.family"],
    },
  },
};

function runPolicy(ctx: Ctx) {
  const policy = createAssistToolsNodeInvokePolicy();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return policy.handle(ctx as any);
}

describe("createAssistToolsNodeInvokePolicy", () => {
  it("rejects commands outside the allowlist", async () => {
    const result = await runPolicy({
      command: "ha.unknown",
      nodeId: "node-1",
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("COMMAND_NOT_ALLOWED");
  });

  it("denies ha.call_service when no policy is configured", async () => {
    const result = await runPolicy({
      command: "ha.call_service",
      nodeId: "node-1",
      params: { domain: "light", service: "turn_on" },
      pluginConfig: {},
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("SERVICE_DENIED");
  });

  it("allows ha.call_service when service matches allowServices", async () => {
    const result = await runPolicy({
      command: "ha.call_service",
      nodeId: "node-1",
      params: { domain: "light", service: "turn_on" },
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(true);
  });

  it("denies ha.call_service when service matches denyServices (deny wins)", async () => {
    const result = await runPolicy({
      command: "ha.call_service",
      nodeId: "node-1",
      params: { domain: "light", service: "delete" },
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("SERVICE_DENIED");
  });

  it("denies ha.get_state for entities not in allowReadEntities", async () => {
    const result = await runPolicy({
      command: "ha.get_state",
      nodeId: "node-1",
      params: { entity_id: "lock.front_door" },
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("ENTITY_DENIED");
  });

  it("allows ha.get_state for entities matching allowReadEntities", async () => {
    const result = await runPolicy({
      command: "ha.get_state",
      nodeId: "node-1",
      params: { entity_id: "sensor.outdoor_temp" },
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(true);
  });

  it("denies ha.get_state when entity matches denyReadEntities", async () => {
    const result = await runPolicy({
      command: "ha.get_state",
      nodeId: "node-1",
      params: { entity_id: "sensor.secret_token" },
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(false);
  });

  it("denies ha.list_states without allowReadEntities configured", async () => {
    const result = await runPolicy({
      command: "ha.list_states",
      nodeId: "node-1",
      pluginConfig: { nodes: { "node-1": {} } },
    });
    expect(result.ok).toBe(false);
  });

  it("allows ha.list_states when allowReadEntities is non-empty", async () => {
    const result = await runPolicy({
      command: "ha.list_states",
      nodeId: "node-1",
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(true);
  });

  it("denies ha.calendar_get_events for calendars not in allowCalendars", async () => {
    const result = await runPolicy({
      command: "ha.calendar_get_events",
      nodeId: "node-1",
      params: { entity_id: "calendar.work" },
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(false);
  });

  it("allows ha.calendar_get_events for an allowed calendar", async () => {
    const result = await runPolicy({
      command: "ha.calendar_get_events",
      nodeId: "node-1",
      params: { entity_id: "calendar.family" },
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(true);
  });

  it("allows metadata reads (ha.list_areas)", async () => {
    const result = await runPolicy({
      command: "ha.list_areas",
      nodeId: "node-1",
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(true);
  });

  it("falls back to wildcard '*' node entry", async () => {
    const result = await runPolicy({
      command: "ha.get_state",
      nodeId: "any-node",
      params: { entity_id: "sensor.x" },
      pluginConfig: { nodes: { "*": { allowReadEntities: ["sensor.*"] } } },
    });
    expect(result.ok).toBe(true);
  });
});
