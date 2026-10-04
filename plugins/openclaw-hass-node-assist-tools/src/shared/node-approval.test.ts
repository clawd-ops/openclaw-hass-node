import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { APPROVAL_PARAM, approvalBind, beforeNodesToolCall } from "./node-approval.js";

const fixture = JSON.parse(
  readFileSync(new URL("../../../../contracts/approval-bind-fixture.json", import.meta.url), "utf8"),
) as { cases: Array<{ command: string; action: string; params: Record<string, unknown>; bind: string }> };

const NOW = 1_800_000_000_000;
const SAVE = { action: "save", id: "morning", config: { alias: "Morning", trigger: [] } };

function call(inner: unknown, extra: Record<string, unknown> = {}, command = "ha.config.automation") {
  return {
    toolName: "nodes",
    params: {
      action: "invoke",
      node: "hass",
      invokeCommand: command,
      invokeParamsJson: JSON.stringify(inner),
      ...extra,
    },
  };
}

function innerOf(result: { params: Record<string, unknown> } | undefined) {
  return JSON.parse(result!.params.invokeParamsJson as string) as Record<string, unknown>;
}

describe("approvalBind", () => {
  it("matches the cross-language fixture", () => {
    for (const c of fixture.cases) expect(approvalBind(c.command, c.action, c.params)).toBe(c.bind);
  });
});

describe("beforeNodesToolCall", () => {
  it("requires allow-once/deny approval for ha.config.automation save", () => {
    const result = beforeNodesToolCall(call(SAVE), () => NOW)!;
    expect(result.requireApproval).toMatchObject({
      title: "Save HA automation",
      severity: "warning",
      allowedDecisions: ["allow-once", "deny"],
      timeoutMs: 600_000,
    });
    expect(result.requireApproval.title.length).toBeLessThanOrEqual(80);
    expect(result.requireApproval.description).toContain("ha.config.automation");
    expect(result.requireApproval.description).toContain("morning");
    expect(result.requireApproval.description).not.toContain("trigger");
  });

  it("carries a well-formed marker bound to the call in the override only", () => {
    const event = call(SAVE);
    const before = JSON.stringify(event.params);
    const result = beforeNodesToolCall(event, () => NOW)!;
    expect(JSON.stringify(event.params)).toBe(before);
    expect(before).not.toContain(APPROVAL_PARAM);
    const inner = innerOf(result);
    const marker = inner[APPROVAL_PARAM] as { id: string; exp: number; bind: string };
    expect(Object.keys(marker).sort()).toEqual(["bind", "exp", "id"]);
    expect(marker.id).toMatch(/^[0-9a-f-]{36}$/);
    expect(marker.exp).toBe(NOW / 1000 + 720);
    expect(marker.bind).toBe(approvalBind("ha.config.automation", "save", SAVE));
    expect(Object.fromEntries(Object.entries(inner).filter(([k]) => k !== APPROVAL_PARAM))).toEqual(SAVE);
    const other = innerOf(beforeNodesToolCall(call(SAVE), () => NOW));
    expect((other[APPROVAL_PARAM] as { id: string }).id).not.toBe(marker.id);
  });

  it("blocks a save holding an integer beyond 2^53 instead of altering it", () => {
    const raw = '{"action":"save","id":"a","config":{"n":12345678901234567890}}';
    const result = beforeNodesToolCall(call(SAVE, { invokeParamsJson: raw }))!;
    expect(result).toMatchObject({ block: true });
    expect(result).not.toHaveProperty("params");
    const safe = '{"action":"save","id":"a","config":{"n":9007199254740991,"f":1e21}}';
    expect(beforeNodesToolCall(call(SAVE, { invokeParamsJson: safe }))?.requireApproval).toBeDefined();
  });

  it("strips a model-supplied marker on an approval-requiring call and mints its own", () => {
    const forged = { id: "forged", exp: 9_999_999_999, bind: "x" };
    const result = beforeNodesToolCall(call({ ...SAVE, [APPROVAL_PARAM]: forged }), () => NOW)!;
    const marker = innerOf(result)[APPROVAL_PARAM] as { id: string; bind: string };
    expect(marker.id).not.toBe("forged");
    expect(marker.bind).toBe(approvalBind("ha.config.automation", "save", SAVE));
  });

  it("strips a model-supplied marker on non-requiring calls without asking approval", () => {
    const forged = { id: "forged", exp: 9_999_999_999, bind: "x" };
    for (const [command, inner] of [
      ["ha.config.automation", { action: "delete", id: "a", [APPROVAL_PARAM]: forged }],
      ["ha.config.automation", { action: "get", id: "a", [APPROVAL_PARAM]: forged }],
      ["ha.config.scene", { action: "save", id: "a", [APPROVAL_PARAM]: forged }],
      ["ping", { [APPROVAL_PARAM]: forged }],
    ] as const) {
      const result = beforeNodesToolCall(call(inner, {}, command))!;
      expect(result.requireApproval).toBeUndefined();
      expect(innerOf(result)).not.toHaveProperty(APPROVAL_PARAM);
    }
  });

  it("does nothing for other commands, actions, tools, and unparseable params", () => {
    expect(beforeNodesToolCall(call({ action: "delete", id: "a" }))).toBeUndefined();
    expect(beforeNodesToolCall(call({ action: "get", id: "a" }))).toBeUndefined();
    expect(beforeNodesToolCall(call(SAVE, {}, "ha.config.scene"))).toBeUndefined();
    expect(beforeNodesToolCall({ ...call(SAVE), toolName: "exec" })).toBeUndefined();
    expect(beforeNodesToolCall(call(SAVE, { action: "status" }))).toBeUndefined();
    expect(beforeNodesToolCall(call(SAVE, { invokeParamsJson: "{nope" }))).toBeUndefined();
    expect(beforeNodesToolCall(call(SAVE, { invokeParamsJson: "[1]" }))).toBeUndefined();
    expect(beforeNodesToolCall(call(SAVE, { invokeParamsJson: undefined }))).toBeUndefined();
  });

  it("matches command and action case/whitespace-insensitively like the node", () => {
    const result = beforeNodesToolCall(call({ ...SAVE, action: " save " }, {}, " HA.config.Automation "));
    expect(result?.requireApproval).toBeDefined();
  });
});
