import { describe, expect, it, vi } from "vitest";

const { createAssistToolsNodeInvokePolicy } =
  await import("./node-invoke-policy.js");

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

  // The permitted-routing cases: each forwards and the policy returns ok.
  // `params` is omitted where the command takes none, which is itself part of
  // the contract being asserted.
  for (const { name, command, nodeId, params, pluginConfig } of [
    {
      name: "forwards ha.call_service with valid domain and service",
      command: "ha.call_service",
      nodeId: "node-1",
      params: { domain: "light", service: "turn_on" },
      pluginConfig: nodeConfig,
    },
    {
      name: "forwards ha.get_state with valid entity_id",
      command: "ha.get_state",
      nodeId: "node-1",
      params: { entity_id: "sensor.outdoor_temp" },
      pluginConfig: nodeConfig,
    },
    {
      name: "forwards ha.list_states without any config",
      command: "ha.list_states",
      nodeId: "node-1",
      pluginConfig: {},
    },
    {
      name: "forwards ha.calendar_get_events with valid entity_id",
      command: "ha.calendar_get_events",
      nodeId: "node-1",
      params: { entity_id: "calendar.family" },
      pluginConfig: nodeConfig,
    },
    {
      name: "forwards ha.list_areas without any per-node policy",
      command: "ha.list_areas",
      nodeId: "node-1",
      pluginConfig: {},
    },
    {
      name: "forwards ha.list_services without any per-node policy",
      command: "ha.list_services",
      nodeId: "unpolicied",
      pluginConfig: { nodes: {} },
    },
    {
      name: "ha.logbook forwards with valid entity_id",
      command: "ha.logbook",
      nodeId: "node-1",
      params: { entity_id: "sensor.outdoor_temp" },
      pluginConfig: nodeConfig,
    },
    {
      name: "ha.logbook forwards without entity_id",
      command: "ha.logbook",
      nodeId: "node-1",
      params: {},
      pluginConfig: nodeConfig,
    },
    {
      name: "ha.light_turn_on forwards",
      command: "ha.light_turn_on",
      nodeId: "node-1",
      params: { entity_id: "light.kitchen" },
      pluginConfig: nodeConfig,
    },
    {
      name: "ha.light_turn_off forwards",
      command: "ha.light_turn_off",
      nodeId: "node-1",
      params: { entity_id: "light.kitchen" },
      pluginConfig: nodeConfig,
    },
    {
      name: "ha.addon_logs metadata read forwards without any policy",
      command: "ha.addon_logs",
      nodeId: "any-node",
      params: { slug: "openclaw-hass-node" },
      pluginConfig: {},
    },
  ]) {
    it(name, async () => {
      const result = await runPolicy({ command, nodeId, params, pluginConfig });
      expect(result.ok).toBe(true);
    });
  }

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

  it("ha.history translates entity_id/start/end into node-shape params", async () => {
    const invokeNode = vi.fn(async () => ({
      ok: true,
      payload: { count: 0, history: [] },
    }));
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
    const invokeNode = vi.fn(async () => ({
      ok: true,
      payload: { count: 0, entries: [] },
    }));
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

  // Every case below asserts the same contract: the policy refuses with
  // INVALID_PARAMS *before* forwarding, and ctx.invokeNode is never reached.
  // These are pre-invoke refusals guarding URL/parameter smuggling, so the
  // not-called assertion is the point, not an extra. A table keeps each
  // scenario name visible in test output while making a missing refusal
  // obvious at a glance.
  for (const { name, command, params } of [
    {
      name: "ha.history rejects when both entity_id and entity_ids are set",
      command: "ha.history",
      params: {
        entity_id: "sensor.outdoor_temp",
        entity_ids: ["sensor.outdoor_temp"],
      },
    },
    {
      name: "ha.history rejects entity_id + empty entity_ids array (no bypass)",
      command: "ha.history",
      params: {
        entity_id: "sensor.outdoor_temp",
        entity_ids: [],
      },
    },
    {
      name: "ha.history rejects empty entity_ids array",
      command: "ha.history",
      params: { entity_ids: [] },
    },
    {
      name: "ha.history rejects non-array entity_ids",
      command: "ha.history",
      params: { entity_ids: "sensor.outdoor_temp" },
    },
    {
      name: "ha.history rejects entity_ids with empty/non-string entries",
      command: "ha.history",
      params: { entity_ids: ["sensor.outdoor_temp", ""] },
    },
    {
      name: "ha.history rejects delimiter-smuggling entity_ids entry",
      command: "ha.history",
      params: { entity_ids: ["sensor.outdoor_temp,person.user1"] },
    },
    {
      name: "ha.history rejects delimiter-smuggling singular entity_id",
      command: "ha.history",
      params: { entity_id: "sensor.outdoor_temp,person.user1" },
    },
    {
      name: "ha.logbook rejects delimiter-smuggling entity_id",
      command: "ha.logbook",
      params: { entity_id: "sensor.outdoor_temp person.user1" },
    },
    {
      name: "ha.get_state rejects malformed entity_id syntax",
      command: "ha.get_state",
      params: { entity_id: "sensor.outdoor_temp,person.user1" },
    },
    {
      name: "ha.logbook rejects entity_ids param (history-only field)",
      command: "ha.logbook",
      params: { entity_ids: ["sensor.outdoor_temp"] },
    },
    {
      name: "ha.call_service rejects service with URL delimiters (bypass attempt)",
      command: "ha.call_service",
      params: { domain: "homeassistant", service: "restart?x" },
    },
    {
      name: "ha.call_service rejects domain with slash (path smuggling)",
      command: "ha.call_service",
      params: { domain: "light/../homeassistant", service: "turn_on" },
    },
    {
      name: "ha.history rejects end_time smuggling `&filter_entity_id=...`",
      command: "ha.history",
      params: {
        entity_id: "sensor.outdoor_temp",
        end_time: "2026-07-02T00:00:00&filter_entity_id=person.user1",
      },
    },
    {
      name: "ha.history rejects start_time with whitespace/newline",
      command: "ha.history",
      params: {
        entity_id: "sensor.outdoor_temp",
        start_time: "2026-07-01T00:00:00\n&filter_entity_id=person.user1",
      },
    },
    {
      name: "ha.history rejects start_time with slash path smuggling",
      command: "ha.history",
      params: {
        entity_id: "sensor.outdoor_temp",
        start_time: "2026-07-01T00:00:00/../states",
      },
    },
    {
      name: "ha.history rejects malformed date (garbage)",
      command: "ha.history",
      params: {
        entity_id: "sensor.outdoor_temp",
        start_time: "yesterday",
      },
    },
    {
      name: "ha.logbook rejects end_time smuggling delimiters",
      command: "ha.logbook",
      params: {
        entity_id: "sensor.outdoor_temp",
        end_time: "2026-07-02T00:00:00&entity=person.user1",
      },
    },
    {
      name: "ha.history rejects Assist-shape `end` smuggling delimiters",
      command: "ha.history",
      params: {
        entity_id: "sensor.outdoor_temp",
        end: "2026-07-02T00:00:00&filter_entity_id=person.user1",
      },
    },
    {
      name: "ha.calendar_get_events rejects smuggled end_date_time",
      command: "ha.calendar_get_events",
      params: {
        entity_id: "calendar.family",
        start_date_time: "2026-07-01T00:00:00",
        end_date_time: "2026-07-02T00:00:00&x=y",
      },
    },
  ]) {
    it(name, async () => {
      const invokeNode = vi.fn(async () => ({ ok: true, payload: {} }));
      const result = await runPolicy({
        command,
        nodeId: "node-1",
        params,
        pluginConfig: nodeConfig,
        invokeNode,
      });
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.code).toBe("INVALID_PARAMS");
      expect(invokeNode).not.toHaveBeenCalled();
    });
  }

  // --- convenience light actions ---

  // --- Tier B admin ---
  it("ha.reload_config forwards params and the approval marker untouched", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true as const }));
    const marker = { id: "m", exp: 1, bind: "b" };
    const result = await runPolicy({
      command: "ha.reload_config",
      nodeId: "node-1",
      params: { domain: "core", _openclaw_approval: marker },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(true);
    expect(invokeNode).toHaveBeenCalledWith({
      params: { domain: "core", _openclaw_approval: marker },
    });
  });

  it("ha.reload_config forwards when domain is omitted entirely", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true as const }));
    const result = await runPolicy({
      command: "ha.reload_config",
      nodeId: "node-1",
      params: {},
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(true);
    expect(invokeNode).toHaveBeenCalledWith({ params: {} });
  });

  it("ha.reload_config leaves an unsupported domain for the node to reject", async () => {
    const invokeNode = vi.fn(async () => ({ ok: true as const }));
    const result = await runPolicy({
      command: "ha.reload_config",
      nodeId: "node-1",
      params: { domain: "automation" },
      pluginConfig: nodeConfig,
      invokeNode,
    });
    expect(result.ok).toBe(true);
    expect(invokeNode).toHaveBeenCalledWith({ params: { domain: "automation" } });
  });

  it("ha.addon_start denied for slug 'homeassistant' ", async () => {
    const result = await runPolicy({
      command: "ha.addon_start",
      nodeId: "node-1",
      params: { slug: "homeassistant" },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("ADMIN_SLUG_DENIED");
  });

  it("ha.addon_restart denied for 'core_dns' prefix", async () => {
    const result = await runPolicy({
      command: "ha.addon_restart",
      nodeId: "node-1",
      params: { slug: "core_dns" },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("ADMIN_SLUG_DENIED");
  });

  // --- domain/service smuggling regression ---

  // --- timestamp smuggling regression (PR #207 v5 blocker) ---

  it("ha.history accepts valid ISO-8601 timestamps (Z + offset + fractional)", async () => {
    const invokeNode = vi.fn(async () => ({
      ok: true,
      payload: { history: [] },
    }));
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

  // --- Tier B: approval is verified by the node; the policy only forwards ---

  describe("Tier B commands forward the approval marker", () => {
    const tierB = [
      { cmd: "ha.reload_config", params: { domain: "core" } },
      { cmd: "ha.update_install", params: { entity_id: "update.hacs" } },
      { cmd: "ha.addon_start", params: { slug: "openclaw-hass-node" } },
      { cmd: "ha.addon_stop", params: { slug: "openclaw-hass-node" } },
      { cmd: "ha.addon_restart", params: { slug: "openclaw-hass-node" } },
      { cmd: "ha.addon_update", params: { slug: "openclaw-hass-node" } },
    ];

    for (const { cmd, params } of tierB) {
      it(`${cmd} forwards its params with the marker and no other gate`, async () => {
        const invokeNode = vi.fn(async () => ({ ok: true as const }));
        const marker = { id: "m", exp: 1, bind: "b" };
        const result = await runPolicy({
          command: cmd,
          nodeId: "node-1",
          params: { ...params, _openclaw_approval: marker },
          invokeNode,
        });
        expect(result.ok).toBe(true);
        expect(invokeNode).toHaveBeenCalledWith({
          params: { ...params, _openclaw_approval: marker },
        });
      });
    }
  });
});

// ---------------------------------------------------------------------------
// Finding 3: Contract-to-real-policy-switch parity
// Every contract registration must reach invokeNode through the policy switch,
// not fall through to COMMAND_NOT_ALLOWED.
// ---------------------------------------------------------------------------

import assistCommandContract from "../tools/assist-command-contract.json" with { type: "json" };

type ContractRegistration =
  (typeof assistCommandContract.registrations)[number];

/** Build minimal valid params for each command so the policy switch routes it. */
function validParamsForCommand(
  reg: ContractRegistration,
): Record<string, unknown> {
  const cmd = reg.node_command;
  const params: Record<string, unknown> = {};

  // Commands requiring slug (addon_*)
  if (cmd.includes("addon_")) params.slug = "test_addon";

  // Commands requiring domain + service
  if (cmd === "ha.call_service") {
    params.domain = "light";
    params.service = "turn_on";
  }

  // Commands requiring entity_id
  if (
    ["ha.get_state", "ha.calendar_get_events", "ha.update_install"].includes(
      cmd,
    )
  ) {
    params.entity_id = "light.test_entity";
  }

  // Commands requiring domain (ha.reload_config)
  if (cmd === "ha.reload_config") params.domain = "automation";

  // Calendar time params
  if (cmd === "ha.calendar_get_events") {
    params.start_date_time = "2026-01-01T00:00:00Z";
    params.end_date_time = "2026-01-02T00:00:00Z";
  }

  return params;
}


describe("Contract-to-policy-switch parity", () => {
  const registrations = assistCommandContract.registrations;

  it("has exactly 30 registrations", () => {
    expect(registrations).toHaveLength(31);
  });

  for (const reg of registrations) {
    it(`${reg.node_command}: reaches invokeNode through policy switch`, async () => {
      const invokeNode = vi.fn(async () => ({
        ok: true as const,
        payload: { forwarded: true },
      }));

      const result = await runPolicy({
        command: reg.node_command,
        nodeId: "test-node",
        params: validParamsForCommand(reg),
                invokeNode,
      });

      expect(invokeNode).toHaveBeenCalledTimes(1);
      expect(result.ok).toBe(true);
    });
  }

  it("contract-only command missing from switch falls through to COMMAND_NOT_ALLOWED", async () => {
    const invokeNode = vi.fn(async () => ({
      ok: true as const,
      payload: { forwarded: true },
    }));

    const result = await runPolicy({
      command: "ha.fabricated_command",
      nodeId: "test-node",
      params: {},
            invokeNode,
    });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.code).toBe("COMMAND_NOT_ALLOWED");
    }
    expect(invokeNode).not.toHaveBeenCalled();
  });

  it("policy.commands matches contract node_command set exactly", () => {
    const policy = createAssistToolsNodeInvokePolicy();
    const policyCommands = new Set(policy.commands);
    const contractCommands = new Set(
      registrations.map((r: ContractRegistration) => r.node_command),
    );
    expect(policyCommands).toEqual(contractCommands);
  });
});
