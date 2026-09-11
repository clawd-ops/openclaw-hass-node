// Real TS wrapper -> Python transport/dispatcher -> stub HA -> real TS result guard.
// Run with `uv sync --package openclaw-node` at the repo root, then `pnpm test`.
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it, vi } from "vitest";

const gatewayMock = vi.fn();
vi.mock("openclaw/plugin-sdk/agent-harness-runtime", () => ({
  callGatewayTool: (...args: unknown[]) => gatewayMock(...args),
  listNodes: async () => [{ nodeId: "test-node", displayName: "Test HA" }],
  resolveNodeIdFromList: () => "test-node",
}));
vi.mock("openclaw/plugin-sdk/plugin-config-runtime", () => ({
  resolvePluginConfigObject: () => ({}),
}));

import { createHaCallServiceTool } from "./ha-call-service-tool.js";
import { invokeHaCommand } from "./node-tool-invoke.js";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
type Exchange = { response: { ok: boolean; payload?: unknown; error?: unknown }; ha_calls: unknown[][] };
let exchanges: Exchange[];
let haError: boolean;
let legacyEnvelope: boolean;

function nodeInvoke(params: Record<string, unknown>): Exchange {
  const child = spawnSync(`${root}.venv/bin/python`, ["app/node/tests/contracts/invoke_fixture.py"], {
    cwd: root,
    input: JSON.stringify({ ...params, ha_error: haError }),
    encoding: "utf8",
    timeout: 10000,
  });
  if (child.error || child.status !== 0) {
    throw new Error(`Python contract fixture failed: ${child.error ?? child.stderr}`);
  }
  return JSON.parse(child.stdout) as Exchange;
}

beforeEach(() => {
  exchanges = [];
  haError = false;
  legacyEnvelope = false;
  gatewayMock.mockReset().mockImplementation(async (method: string, _opts: unknown, params: Record<string, unknown>) => {
    if (method === "config.get") return { payload: {} };
    const exchange = nodeInvoke(params);
    exchanges.push(exchange);
    if (legacyEnvelope) return { ok: true, payload: exchange.response.payload };
    // Match the real gateway's respondUnavailableOnNodeInvokeError contract:
    // failures reject callGatewayTool with details.nodeError, not a payload.
    if (!exchange.response.ok) {
      throw Object.assign(new Error("Node invoke failed"), {
        code: "UNAVAILABLE", details: { nodeError: exchange.response.error },
      });
    }
    return exchange.response;
  });
});

function call(args: Record<string, unknown>) {
  return createHaCallServiceTool().execute("test", {
    node: "hass", domain: "light", service: "turn_on", ...args,
  }, new AbortController().signal, () => undefined);
}

describe("wrapper/node command contract", () => {
  it.each(["data", "service_data"])("preserves brightness and nested %s through the real wrapper and Python handler", async (key) => {
    const data = { brightness_pct: 50, transition: 0, rgb_color: [1, 2, 3], nested: { effect: "test", enabled: false } };
    const result = await call({ [key]: data, target: { entity_id: ["light.test"] } });
    expect(exchanges[0].ha_calls).toEqual([["/api/services/light/turn_on", { ...data, entity_id: ["light.test"] }]]);
    expect(gatewayMock.mock.calls[1][2].params).toEqual({ domain: "light", service: "turn_on", data, target: { entity_id: ["light.test"] } });
    const expectedPayload = {
      ok: true,
      changed_states: [{
        entity_id: "light.test",
        state: "on",
        attributes: {
          brightness: 128,
          rgb_color: [1, 2, 3],
          supported_color_modes: ["rgb"],
          fixture: { enabled: false, transition: 0 },
        },
        context: { id: "fixture-context", parent_id: null },
      }],
    };
    expect(exchanges[0].response.payload).toEqual(expectedPayload);
    expect(result).toEqual({
      content: [{
        type: "text",
        text: "Called light.turn_on on Test HA (test-node).\n\n" +
          `--- payload ---\n${JSON.stringify(expectedPayload, null, 2)}`,
      }],
    });
  });

  it("accepts equal aliases but rejects conflicts before any gateway or HA call", async () => {
    await call({ data: { brightness: 10 }, service_data: { brightness: 10 } });
    gatewayMock.mockClear();
    await expect(call({ data: { brightness: 10 }, service_data: { brightness: 20 } })).rejects.toThrow("INVALID_PARAM");
    expect(gatewayMock).not.toHaveBeenCalled();
    expect(exchanges).toHaveLength(1);
  });

  it("compares reordered object keys, equivalent numeric values, and signed zero as JSON", async () => {
    const data = { nested: { amount: 1, transition: -0 }, enabled: false };
    const service_data = { enabled: false, nested: { transition: 0, amount: 1.0 } };
    await call({ data, service_data });
    expect(exchanges[0].ha_calls).toEqual([["/api/services/light/turn_on", service_data]]);
  });

  it("does not confuse boolean and numeric aliases", async () => {
    await expect(call({ data: { nested: [false] }, service_data: { nested: [0] } })).rejects.toThrow("INVALID_PARAM");
    expect(gatewayMock).not.toHaveBeenCalled();
  });

  it.each([null, [], "invalid"])("rejects invalid payload %j before any gateway call", async (data) => {
    await expect(call({ data })).rejects.toThrow("INVALID_PARAM");
    expect(gatewayMock).not.toHaveBeenCalled();
  });

  it.each([false, true])("never renders service success on a Python HA error (legacy envelope=%s)", async (legacy) => {
    haError = true;
    legacyEnvelope = legacy;
    await expect(call({ data: { brightness: 10 } })).rejects.toMatchObject({ code: "HA_NETWORK", source: "ha" });
    expect(exchanges[0].response.ok).toBe(false);
  });

  it.each([
    ["ha.call_service", { domain: "light", service: "turn_on", data: { brightness: 10 }, service_data: { brightness: 20 } }, "INVALID_PARAM"],
    ["ha.config.automation", { action: "save", automation_id: "test", config: { alias: "test", trigger: [], action: [] }, proposal_id: "fake" }, "PROPOSAL_REQUIRED"],
  ] as const)("preserves real Python refusal from %s without HA calls", async (command, commandParams, code) => {
    await expect(invokeHaCommand({ nodeId: "test-node", command, commandParams, gatewayOpts: {} })).rejects.toMatchObject({ code, source: "node" });
    expect(exchanges[0].ha_calls).toEqual([]);
    expect(exchanges[0].response.ok).toBe(false);
  });

  it("distinguishes a gateway transport rejection without running a node command", async () => {
    gatewayMock.mockImplementation(async (method: string) => {
      if (method === "config.get") return { payload: {} };
      throw new Error("Gateway disconnected");
    });
    await expect(call({ data: { brightness: 10 } })).rejects.toMatchObject({ source: "transport", code: "TRANSPORT_ERROR" });
    expect(exchanges).toEqual([]);
  });
});
