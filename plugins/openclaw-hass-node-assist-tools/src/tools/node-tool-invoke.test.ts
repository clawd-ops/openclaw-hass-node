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
  it.each([
    ["UNAUTHORIZED", "Operator scope required", false],
    ["INVALID_REQUEST", "Invalid node.invoke params", false],
    ["FORBIDDEN", "Command denied by gateway policy", false],
    ["UNAVAILABLE", "Gateway admission temporarily unavailable", true],
  ] as const)("preserves authoritative gateway rejection %s", async (code, message, retryable) => {
    const details = { reason: "fixture-gateway-decision" };
    callGatewayToolMock.mockRejectedValue(Object.assign(new Error(message), {
      name: "GatewayClientRequestError", code, gatewayCode: code,
      details, retryable, retryAfterMs: retryable ? 1500 : undefined,
    }));
    const { invokeHaCommand } = await loadModule();
    await expect(invokeHaCommand({ nodeId: "test", command: "test", commandParams: {}, gatewayOpts: {} }))
      .rejects.toMatchObject({
        source: "gateway", code, message: `gateway:${code}: ${message}`,
        details, retryable, retryAfterMs: retryable ? 1500 : undefined,
      });
  });

  it("supports the SDK code alias when gatewayCode is absent", async () => {
    callGatewayToolMock.mockRejectedValue(Object.assign(new Error("Bad request"), {
      name: "GatewayClientRequestError", code: "INVALID_REQUEST", retryable: false,
    }));
    const { invokeHaCommand } = await loadModule();
    await expect(invokeHaCommand({ nodeId: "test", command: "test", commandParams: {}, gatewayOpts: {} }))
      .rejects.toMatchObject({ source: "gateway", code: "INVALID_REQUEST", retryable: false });
  });

  it.each(["PROPOSAL_REQUIRED", "HA_NETWORK"])("preserves inner %s over the SDK gateway classification", async (code) => {
    callGatewayToolMock.mockRejectedValue(Object.assign(new Error("Gateway wrapped node refusal"), {
      name: "GatewayClientRequestError", code: "UNAVAILABLE", gatewayCode: "UNAVAILABLE",
      retryable: false, details: { nodeError: { code, message: "Original operation failure" } },
    }));
    const { invokeHaCommand } = await loadModule();
    await expect(invokeHaCommand({ nodeId: "test", command: "test", commandParams: {}, gatewayOpts: {} }))
      .rejects.toMatchObject({ source: code.startsWith("HA_") ? "ha" : "node", code, message: expect.stringContaining("Original operation failure") });
  });

  it.each([
    Object.assign(new Error("Connection reset"), { code: "ECONNRESET" }),
    Object.assign(new Error("Local timeout"), { name: "GatewayProtocolRequestTimeoutError", code: "CLIENT_TIMEOUT" }),
    new Error("Local failure"),
  ])("keeps local/socket failures in the transport category: %s", async (error) => {
    callGatewayToolMock.mockRejectedValue(error);
    const { invokeHaCommand } = await loadModule();
    await expect(invokeHaCommand({ nodeId: "test", command: "test", commandParams: {}, gatewayOpts: {} }))
      .rejects.toMatchObject({ source: "transport", code: "TRANSPORT_ERROR", message: expect.stringContaining(error.message), retryable: undefined });
  });

  it.each([
    { ok: true, payload: { ok: false, error: "PROPOSAL_REQUIRED", message: "Approval missing" } },
    { ok: false, error: { code: "PROPOSAL_REQUIRED", message: "Approval missing" } },
    { ok: false, error: "PROPOSAL_REQUIRED", message: "Approval missing" },
  ])("rejects semantic refusal in every supported envelope: %j", async (response) => {
    callGatewayToolMock.mockResolvedValue(response);
    const { invokeHaCommand } = await loadModule();
    await expect(invokeHaCommand({ nodeId: "test", command: "test", commandParams: {}, gatewayOpts: {} }))
      .rejects.toMatchObject({ code: "PROPOSAL_REQUIRED", source: "node", message: expect.stringContaining("Approval missing") });
  });

  it.each([undefined, null, [], { ok: true, payload: null }, { ok: true, payload: "bad" }, { ok: true, payload: { ok: "false" } }])("rejects malformed results: %j", async (response) => {
    callGatewayToolMock.mockResolvedValue(response);
    const { invokeHaCommand } = await loadModule();
    await expect(invokeHaCommand({ nodeId: "test", command: "test", commandParams: {}, gatewayOpts: {} }))
      .rejects.toMatchObject({ code: "INVALID_RESULT", source: "node" });
  });

  it("accepts the legacy ping payload without an ok field", async () => {
    callGatewayToolMock.mockResolvedValue({ pong: true });
    const { invokeHaCommand } = await loadModule();
    await expect(invokeHaCommand({ nodeId: "test", command: "ping", commandParams: {}, gatewayOpts: {} })).resolves.toEqual({ pong: true });
  });

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

describe("resolveNodeAndPolicy", () => {
  // #322: the per-node policy must be selected by the canonical node ID, not
  // by the identifier the caller passed as the `node` tool parameter. The
  // scenario below is the one the issue describes: one physical node whose
  // canonical ID carries an explicit deny, and an alias-keyed entry that
  // grants. Selecting the node by the alias must still resolve the deny.
  const escalationConfig = {
    nodes: {
      "hass-001": { allowAdminOps: false },
      kitchen: { allowAdminOps: true, adminToken: "alias-token" },
    },
  };

  function primeGateway(config: unknown): void {
    listNodesMock.mockResolvedValue([
      { nodeId: "hass-001", displayName: "Kitchen" },
    ]);
    resolveNodeIdFromListMock.mockReturnValue("hass-001");
    callGatewayToolMock.mockResolvedValue({ payload: {} });
    resolvePluginConfigObjectMock.mockReturnValue(config);
  }

  afterEach(() => {
    listNodesMock.mockReset();
    resolveNodeIdFromListMock.mockReset();
    resolvePluginConfigObjectMock.mockReset();
  });

  it.each(["kitchen", "Kitchen", "hass-001"])(
    "resolves the canonical deny when the caller selects the node as %s",
    async (nodeIdentifier) => {
      primeGateway(escalationConfig);

      const { resolveNodeAndPolicy } = await loadModule();
      const resolved = await resolveNodeAndPolicy({
        nodeIdentifier,
        gatewayOpts: {},
      });

      expect(resolved.nodeId).toBe("hass-001");
      expect(resolved.policy).toEqual({ allowAdminOps: false });
      expect(resolved.policy?.adminToken).toBeUndefined();
    },
  );

  it("does not grant from an alias-keyed entry when the canonical ID has none", async () => {
    primeGateway({ nodes: { kitchen: { allowAdminOps: true } } });

    const { resolveNodeAndPolicy } = await loadModule();
    const resolved = await resolveNodeAndPolicy({
      nodeIdentifier: "kitchen",
      gatewayOpts: {},
    });

    expect(resolved.nodeId).toBe("hass-001");
    expect(resolved.policy).toBeUndefined();
  });

  it("still reports the node display name for operator-facing messages", async () => {
    primeGateway(escalationConfig);

    const { resolveNodeAndPolicy } = await loadModule();
    const resolved = await resolveNodeAndPolicy({
      nodeIdentifier: "kitchen",
      gatewayOpts: {},
    });

    expect(resolved.nodeDisplayName).toBe("Kitchen");
  });
});
