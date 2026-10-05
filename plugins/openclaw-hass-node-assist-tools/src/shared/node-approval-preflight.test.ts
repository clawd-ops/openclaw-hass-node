import { describe, expect, it } from "vitest";
import { beforeToolCall } from "./node-approval.js";

function nodesCall(command: string, inner: Record<string, unknown>) {
  return {
    toolName: "nodes",
    params: { action: "invoke", node: "hass", invokeCommand: command, invokeParamsJson: JSON.stringify(inner) },
  };
}

function expectRefused(result: unknown, text: string) {
  const r = result as { block?: boolean; blockReason?: string; requireApproval?: unknown; params?: unknown };
  expect(r.block).toBe(true);
  expect(r.blockReason).toContain(text);
  expect(r.requireApproval).toBeUndefined();
  expect(r.params).toBeUndefined(); // no marker minted
}

describe("refuse before prompting", () => {
  it.each(["fs.write", "fs.patch", "fs.delete", "fs.restore"])("%s under .storage/", (command) => {
    const inner = command === "fs.write" ? { path: "/config/.storage/core.config_entries", content: "x" } : { path: "/config/.storage/auth" };
    expectRefused(beforeToolCall(nodesCall(command, inner)), "STORAGE_READONLY: Writes to .storage/ are refused");
  });

  it("fs.move with a .storage/ destination", () => {
    expectRefused(beforeToolCall(nodesCall("fs.move", { src: "/config/a.yaml", dst: "/config/.storage/a" })), "STORAGE_READONLY");
  });

  it("unknown parameter on a config mutation, naming the allowed set", () => {
    const result = beforeToolCall(nodesCall("ha.config.entity_registry", { action: "update", entity_id: "light.a", name: "x" }));
    expectRefused(result, "INVALID_PARAM: unknown parameter(s): name; allowed: _openclaw_approval, action, attrs, entity_id, proposal_id");
  });

  it.each([
    ["nodes invoke", (slug: string) => beforeToolCall(nodesCall("ha.addon_restart", { slug }))],
    ["admin tool", (slug: string) => beforeToolCall({ toolName: "ha_addon_restart", params: { slug } })],
  ])("add-on slug refusals via %s", (_label, run) => {
    expectRefused(run("core_ssh"), "PERMISSION_DENIED: addon lifecycle denied for core slug");
    expectRefused(run("Supervisor"), "INVALID_PARAM: invalid addon slug");
    expectRefused(run("homeassistant"), "PERMISSION_DENIED: addon lifecycle denied for slug");
    expectRefused(run(""), "MISSING_PARAM: slug is required");
  });

  it("accepts the helper type's own id key and refuses another", () => {
    const ok = beforeToolCall(nodesCall("ha.config.helpers", { action: "delete", helper_type: "timer", timer_id: "k" })) as { requireApproval?: unknown };
    expect(ok.requireApproval).toBeDefined();
    expectRefused(
      beforeToolCall(nodesCall("ha.config.helpers", { action: "delete", helper_type: "timer", counter_id: "k" })),
      "unknown parameter(s): counter_id",
    );
  });

  it("still prompts for valid calls", () => {
    for (const [command, inner] of [
      ["fs.write", { path: "/config/automations.yaml", content: "x" }],
      ["ha.config.entity_registry", { action: "update", entity_id: "light.a", attrs: { name: "x" } }],
      ["ha.addon_restart", { slug: "a0d7b954_vscode" }],
    ] as const) {
      const result = beforeToolCall(nodesCall(command, inner), () => 1_800_000_000_000) as { requireApproval?: unknown };
      expect(result.requireApproval).toBeDefined();
    }
    const admin = beforeToolCall({ toolName: "ha_addon_restart", params: { slug: "a0d7b954_vscode" } }) as { requireApproval?: unknown };
    expect(admin.requireApproval).toBeDefined();
  });
});
