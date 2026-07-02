import { describe, expect, it, vi } from "vitest";

const { createAssistToolsNodeInvokePolicy } = await import(
  "./node-invoke-policy.js"
);

type InvokeNodeResult =
  | { ok: true; payload?: unknown; details?: unknown }
  | { ok: false; code: string; message: string; details?: unknown };

type Ctx = {
  command: string;
  nodeId: string;
  params?: unknown;
  pluginConfig?: unknown;
  invokeNode?: (opts: {
    params: Record<string, unknown>;
  }) => Promise<InvokeNodeResult>;
};

// Minimal config — no entity/service/calendar lists needed after routing-only refactor.
const nodeConfig = {
  nodes: {
    "node-1": {},
  },
};

function defaultInvokeNode() {
  return vi.fn(async () => ({ ok: true, payload: { forwarded: true } }));
}

function runPolicy(ctx: Ctx) {
  const policy = createAssistToolsNodeInvokePolicy();
  const withInvoke: Ctx = ctx.invokeNode
    ? ctx
    : { ...ctx, invokeNode: defaultInvokeNode() };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return policy.handle(withInvoke as any);
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

  it("forwards ha.call_service with valid domain and service", async () => {
    const result = await runPolicy({
      command: "ha.call_service",
      nodeId: "node-1",
      params: { domain: "light", service: "turn_on" },
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(true);
  });

  it("forwards ha.get_state with valid entity_id", async () => {
    const result = await runPolicy({
      command: "ha.get_state",
      nodeId: "node-1",
      params: { entity_id: "sensor.outdoor_temp" },
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(true);
  });

  it("forwards ha.list_states without any config", async () => {
    const result = await runPolicy({
      command: "ha.list_states",
      nodeId: "node-1",
      pluginConfig: {},
    });
    expect(result.ok).toBe(true);
  });

  it("forwards ha.calendar_get_events with valid entity_id", async () => {
    const result = await runPolicy({
      command: "ha.calendar_get_events",
      nodeId: "node-1",
      params: { entity_id: "calendar.family" },
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(true);
  });

  it("forwards ha.list_areas without any per-node policy", async () => {
    const result = await runPolicy({
      command: "ha.list_areas",
      nodeId: "node-1",
      pluginConfig: {},
    });
    expect(result.ok).toBe(true);
  });

  it("forwards ha.list_services without any per-node policy", async () => {
    const result = await runPolicy({
      command: "ha.list_services",
      nodeId: "unpolicied",
      pluginConfig: { nodes: {} },
    });
    expect(result.ok).toBe(true);
  });

  it("forwards allowed ha.call_service to ctx.invokeNode and returns its payload", async () => {
    const invokeNode = vi.fn(async () => ({
      ok: true as const,
      payload: { context_id: "abc" },
    }));
    const result = await runPolicy({
      command: "ha.call_service",
      nodeId: "node-1",
      params: { domain: "light", service: "turn_on" },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(invokeNode).toHaveBeenCalledTimes(1);
    expect(invokeNode).toHaveBeenCalledWith({
      params: { domain: "light", service: "turn_on" },
    });
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.payload).toEqual({ context_id: "abc" });
  });

  it("forwards metadata reads (ha.list_areas) to ctx.invokeNode", async () => {
    const invokeNode = vi.fn(async () => ({
      ok: true as const,
      payload: { areas: [] },
    }));
    const result = await runPolicy({
      command: "ha.list_areas",
      nodeId: "node-1",
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(invokeNode).toHaveBeenCalledTimes(1);
    expect(result.ok).toBe(true);
  });

  it("propagates ctx.invokeNode failures", async () => {
    const invokeNode = vi.fn(async () => ({
      ok: false as const,
      code: "NODE_OFFLINE",
      message: "node not connected",
    }));
    const result = await runPolicy({
      command: "ha.get_state",
      nodeId: "node-1",
      params: { entity_id: "sensor.outdoor_temp" },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(invokeNode).toHaveBeenCalledTimes(1);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("NODE_OFFLINE");
  });

  it("denies with NODE_UNAVAILABLE when ctx.invokeNode is missing on an allowed call", async () => {
    const policy = createAssistToolsNodeInvokePolicy();
    const result = await policy.handle(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      {
        command: "ha.call_service",
        nodeId: "node-1",
        params: { domain: "light", service: "turn_on" },
        pluginConfig: nodeConfig,
      } as any,
    );
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("NODE_UNAVAILABLE");
  });

  it("does not invoke the node when param validation fails (invalid service)", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true as const }));
    const result = await runPolicy({
      command: "ha.call_service",
      nodeId: "node-1",
      params: { domain: "light", service: "delete?x" },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  // --- entity-scoped reads: logbook / history ---
  it("ha.logbook forwards with valid entity_id", async () => {
    const result = await runPolicy({
      command: "ha.logbook",
      nodeId: "node-1",
      params: { entity_id: "sensor.outdoor_temp" },
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(true);
  });

  it("ha.logbook forwards without entity_id", async () => {
    const result = await runPolicy({
      command: "ha.logbook",
      nodeId: "node-1",
      params: {},
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(true);
  });

  it("ha.history translates entity_id/start/end into node-shape params", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: { count: 0, history: [] } }));
    const result = await runPolicy({
      command: "ha.history",
      nodeId: "node-1",
      params: {
        entity_id: "sensor.outdoor_temp",
        start: "2026-07-01T00:00:00",
        end: "2026-07-02T00:00:00",
      },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(true);
    expect(invokeNode).toHaveBeenCalledTimes(1);
    const forwarded = invokeNode.mock.calls[0]?.[0]?.params ?? {};
    expect(forwarded).toMatchObject({
      entity_ids: ["sensor.outdoor_temp"],
      start_time: "2026-07-01T00:00:00",
      end_time: "2026-07-02T00:00:00",
    });
    expect(forwarded).not.toHaveProperty("entity_id");
    expect(forwarded).not.toHaveProperty("start");
    expect(forwarded).not.toHaveProperty("end");
  });

  it("ha.logbook translates start/end but keeps singular entity_id", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: { count: 0, entries: [] } }));
    const result = await runPolicy({
      command: "ha.logbook",
      nodeId: "node-1",
      params: {
        entity_id: "sensor.outdoor_temp",
        start: "2026-07-01T00:00:00",
        end: "2026-07-02T00:00:00",
      },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(true);
    const forwarded = invokeNode.mock.calls[0]?.[0]?.params ?? {};
    expect(forwarded).toMatchObject({
      entity_id: "sensor.outdoor_temp",
      start_time: "2026-07-01T00:00:00",
      end_time: "2026-07-02T00:00:00",
    });
    expect(forwarded).not.toHaveProperty("start");
    expect(forwarded).not.toHaveProperty("end");
  });

  it("ha.history forwards entity_ids-only when every id has valid format", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.history",
      nodeId: "node-1",
      params: { entity_ids: ["sensor.outdoor_temp", "light.kitchen"] },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(true);
    expect(invokeNode).toHaveBeenCalledTimes(1);
    const forwarded = invokeNode.mock.calls[0]?.[0]?.params ?? {};
    expect(forwarded).toMatchObject({
      entity_ids: ["sensor.outdoor_temp", "light.kitchen"],
    });
    expect(forwarded).not.toHaveProperty("entity_id");
  });

  it("ha.history rejects when both entity_id and entity_ids are set", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.history",
      nodeId: "node-1",
      params: {
        entity_id: "sensor.outdoor_temp",
        entity_ids: ["sensor.outdoor_temp"],
      },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.history rejects entity_id + empty entity_ids array (no bypass)", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.history",
      nodeId: "node-1",
      params: {
        entity_id: "sensor.outdoor_temp",
        entity_ids: [],
      },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.history rejects empty entity_ids array", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.history",
      nodeId: "node-1",
      params: { entity_ids: [] },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.history rejects non-array entity_ids", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.history",
      nodeId: "node-1",
      params: { entity_ids: "sensor.outdoor_temp" },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.history rejects entity_ids with empty/non-string entries", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.history",
      nodeId: "node-1",
      params: { entity_ids: ["sensor.outdoor_temp", ""] },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.history rejects delimiter-smuggling entity_ids entry", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.history",
      nodeId: "node-1",
      params: { entity_ids: ["sensor.outdoor_temp,person.rob"] },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.history rejects delimiter-smuggling singular entity_id", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.history",
      nodeId: "node-1",
      params: { entity_id: "sensor.outdoor_temp,person.rob" },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.logbook rejects delimiter-smuggling entity_id", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.logbook",
      nodeId: "node-1",
      params: { entity_id: "sensor.outdoor_temp person.rob" },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.get_state rejects malformed entity_id syntax", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.get_state",
      nodeId: "node-1",
      params: { entity_id: "sensor.outdoor_temp,person.rob" },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.logbook rejects entity_ids param (history-only field)", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.logbook",
      nodeId: "node-1",
      params: { entity_ids: ["sensor.outdoor_temp"] },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  // --- convenience light actions ---
  it("ha.light_turn_on forwards", async () => {
    const result = await runPolicy({
      command: "ha.light_turn_on",
      nodeId: "node-1",
      params: { entity_id: "light.kitchen" },
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(true);
  });

  it("ha.light_turn_off forwards", async () => {
    const result = await runPolicy({
      command: "ha.light_turn_off",
      nodeId: "node-1",
      params: { entity_id: "light.kitchen" },
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(true);
  });

  // --- Tier B admin ---
  it("ha.reload_config denied when allowAdminOps unset", async () => {
    const result = await runPolicy({
      command: "ha.reload_config",
      nodeId: "node-1",
      params: { domain: "automation" },
      pluginConfig: nodeConfig,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("ADMIN_DENIED");
  });

  it("ha.reload_config denied when adminToken missing", async () => {
    const result = await runPolicy({
      command: "ha.reload_config",
      nodeId: "node-1",
      params: { domain: "automation" },
      pluginConfig: {
        nodes: { "node-1": { allowAdminOps: true } },
      },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("ADMIN_DENIED");
  });

  it("ha.reload_config forwards with injected admin_token", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true as const }));
    const result = await runPolicy({
      command: "ha.reload_config",
      nodeId: "node-1",
      params: { domain: "automation", admin_token: "attacker-supplied" },
      pluginConfig: {
        nodes: { "node-1": { allowAdminOps: true, adminToken: "REAL" } },
      },
      invokeNode,
    });
    expect(result.ok).toBe(true);
    expect(invokeNode).toHaveBeenCalledTimes(1);
    // Attacker-supplied admin_token must be overridden with the configured one.
    expect(invokeNode.mock.calls[0]?.[0]).toEqual({
      params: { domain: "automation", admin_token: "REAL" },
    });
  });

  it("ha.addon_start denied for slug 'homeassistant' even with admin config", async () => {
    const result = await runPolicy({
      command: "ha.addon_start",
      nodeId: "node-1",
      params: { slug: "homeassistant" },
      pluginConfig: {
        nodes: { "node-1": { allowAdminOps: true, adminToken: "T" } },
      },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("ADMIN_SLUG_DENIED");
  });

  it("ha.addon_restart denied for 'core_dns' prefix", async () => {
    const result = await runPolicy({
      command: "ha.addon_restart",
      nodeId: "node-1",
      params: { slug: "core_dns" },
      pluginConfig: {
        nodes: { "node-1": { allowAdminOps: true, adminToken: "T" } },
      },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("ADMIN_SLUG_DENIED");
  });

  // --- domain/service smuggling regression ---
  it("ha.call_service rejects service with URL delimiters (bypass attempt)", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.call_service",
      nodeId: "node-1",
      params: { domain: "homeassistant", service: "restart?x" },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.call_service rejects domain with slash (path smuggling)", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.call_service",
      nodeId: "node-1",
      params: { domain: "light/../homeassistant", service: "turn_on" },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  // --- timestamp smuggling regression (PR #207 v5 blocker) ---
  it("ha.history rejects end_time smuggling `&filter_entity_id=...`", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.history",
      nodeId: "node-1",
      params: {
        entity_id: "sensor.outdoor_temp",
        end_time: "2026-07-02T00:00:00&filter_entity_id=person.rob",
      },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.history rejects start_time with whitespace/newline", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.history",
      nodeId: "node-1",
      params: {
        entity_id: "sensor.outdoor_temp",
        start_time: "2026-07-01T00:00:00\n&filter_entity_id=person.rob",
      },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.history rejects start_time with slash path smuggling", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.history",
      nodeId: "node-1",
      params: {
        entity_id: "sensor.outdoor_temp",
        start_time: "2026-07-01T00:00:00/../states",
      },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.history rejects malformed date (garbage)", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.history",
      nodeId: "node-1",
      params: {
        entity_id: "sensor.outdoor_temp",
        start_time: "yesterday",
      },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.history accepts valid ISO-8601 timestamps (Z + offset + fractional)", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: { history: [] } }));
    const result = await runPolicy({
      command: "ha.history",
      nodeId: "node-1",
      params: {
        entity_id: "sensor.outdoor_temp",
        start_time: "2026-07-01T00:00:00.123Z",
        end_time: "2026-07-02T00:00:00+00:00",
      },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(true);
    expect(invokeNode).toHaveBeenCalledTimes(1);
  });

  it("ha.logbook rejects end_time smuggling delimiters", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.logbook",
      nodeId: "node-1",
      params: {
        entity_id: "sensor.outdoor_temp",
        end_time: "2026-07-02T00:00:00&entity=person.rob",
      },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.logbook accepts valid ISO-8601 start_time/end_time", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: [] }));
    const result = await runPolicy({
      command: "ha.logbook",
      nodeId: "node-1",
      params: {
        entity_id: "sensor.outdoor_temp",
        start_time: "2026-07-01T00:00:00",
        end_time: "2026-07-02T00:00:00Z",
      },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(true);
    expect(invokeNode).toHaveBeenCalledTimes(1);
  });

  it("ha.history rejects Assist-shape `end` smuggling delimiters", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.history",
      nodeId: "node-1",
      params: {
        entity_id: "sensor.outdoor_temp",
        end: "2026-07-02T00:00:00&filter_entity_id=person.rob",
      },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.calendar_get_events rejects smuggled end_date_time", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
    const result = await runPolicy({
      command: "ha.calendar_get_events",
      nodeId: "node-1",
      params: {
        entity_id: "calendar.family",
        start_date_time: "2026-07-01T00:00:00",
        end_date_time: "2026-07-02T00:00:00&x=y",
      },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("ha.calendar_get_events accepts valid ISO-8601 datetimes", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true, payload: [] }));
    const result = await runPolicy({
      command: "ha.calendar_get_events",
      nodeId: "node-1",
      params: {
        entity_id: "calendar.family",
        start_date_time: "2026-07-01T00:00:00",
        end_date_time: "2026-07-02T00:00:00Z",
      },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(true);
    expect(invokeNode).toHaveBeenCalledTimes(1);
  });

  it("ha.addon_logs metadata read forwards without any policy", async () => {
    const result = await runPolicy({
      command: "ha.addon_logs",
      nodeId: "any-node",
      params: { slug: "openclaw-hass-node" },
      pluginConfig: {},
    });
    expect(result.ok).toBe(true);
  });
});
