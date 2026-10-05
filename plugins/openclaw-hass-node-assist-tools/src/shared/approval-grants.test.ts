import { describe, expect, it } from "vitest";
import { createGrantStore, grantable, GRANT_TTL_MS } from "./approval-grants.js";
import { APPROVAL_PARAM, approvalBind, beforeToolCall } from "./node-approval.js";

const NOW = 1_800_000_000_000;
const SESSION = "agent:main:main";
const SAVE = { action: "save", id: "morning", config: { alias: "Morning", trigger: [] } };

function nodesCall(command: string, inner: Record<string, unknown>) {
  return {
    toolName: "nodes",
    params: { action: "invoke", node: "hass", invokeCommand: command, invokeParamsJson: JSON.stringify(inner) },
  };
}

function innerOf(result: { params: Record<string, unknown> } | undefined) {
  return JSON.parse(result!.params.invokeParamsJson as string) as Record<string, unknown>;
}

type Approval = { allowedDecisions: string[]; onResolution(decision: unknown): void };

function prompted(result: unknown): Approval {
  return (result as { requireApproval: Approval }).requireApproval;
}

function resolve(result: unknown, decision: string) {
  prompted(result).onResolution(decision);
}

describe("allow-always offer", () => {
  it("is offered for a non-destructive call in a session", () => {
    const result = beforeToolCall(nodesCall("ha.config.automation", SAVE), () => NOW, SESSION, createGrantStore());
    expect(prompted(result).allowedDecisions).toEqual(["allow-once", "allow-always", "deny"]);
  });

  it("is never offered for a destructive call", () => {
    const store = createGrantStore();
    const del = beforeToolCall(nodesCall("ha.config.automation", { action: "delete", id: "x" }), () => NOW, SESSION, store);
    expect(prompted(del).allowedDecisions).toEqual(["allow-once", "deny"]);
    const fsDelete = beforeToolCall(nodesCall("fs.delete", { path: "/config/a" }), () => NOW, SESSION, store);
    expect(prompted(fsDelete).allowedDecisions).toEqual(["allow-once", "deny"]);
    expect(grantable("ha.update_install", "")).toBe(false);
  });

  it("is not offered without a session key", () => {
    const result = beforeToolCall(nodesCall("ha.config.automation", SAVE), () => NOW, undefined, createGrantStore());
    expect(prompted(result).allowedDecisions).toEqual(["allow-once", "deny"]);
  });

  it("is offered on admin tools, except update install", () => {
    const store = createGrantStore();
    const restart = beforeToolCall({ toolName: "ha_addon_restart", params: { slug: "a0d7b954_x" } }, () => NOW, SESSION, store);
    expect(prompted(restart).allowedDecisions).toContain("allow-always");
    const update = beforeToolCall({ toolName: "ha_update_install", params: { entity_id: "update.x" } }, () => NOW, SESSION, store);
    expect(prompted(update).allowedDecisions).toEqual(["allow-once", "deny"]);
  });
});

describe("a recorded grant", () => {
  function granted(store = createGrantStore()) {
    resolve(beforeToolCall(nodesCall("ha.config.automation", SAVE), () => NOW, SESSION, store), "allow-always");
    return store;
  }
  const LOVELACE_SAVE = { action: "save", url_path: "lovelace", config: { views: [] } };
  const LOVELACE_RESOURCE = { action: "resources_create", url: "/local/x.js", res_type: "module" };

  it("skips the prompt and mints a marker bound to the exact params", () => {
    const store = granted();
    const other = { ...SAVE, id: "evening" };
    const result = beforeToolCall(nodesCall("ha.config.automation", other), () => NOW + 1000, SESSION, store);
    expect(result).not.toHaveProperty("requireApproval");
    const marker = innerOf(result)[APPROVAL_PARAM] as { bind: string; exp: number };
    expect(marker.bind).toBe(approvalBind("ha.config.automation", "save", other));
    expect(marker.exp).toBeGreaterThan(NOW / 1000);
  });

  it("skips the prompt for admin tools", () => {
    const store = createGrantStore();
    const first = beforeToolCall({ toolName: "ha_addon_restart", params: { slug: "a0d7b954_x" } }, () => NOW, SESSION, store);
    resolve(first, "allow-always");
    const again = beforeToolCall({ toolName: "ha_addon_restart", params: { slug: "a0d7b954_x" } }, () => NOW, SESSION, store);
    expect(again).not.toHaveProperty("requireApproval");
    expect((again as { params: Record<string, unknown> }).params[APPROVAL_PARAM]).toBeDefined();
  });

  it("still prompts for another session, command or action", () => {
    const store = granted();
    const session = beforeToolCall(nodesCall("ha.config.automation", SAVE), () => NOW, "agent:main:other", store);
    const command = beforeToolCall(nodesCall("ha.config.script", SAVE), () => NOW, SESSION, store);
    resolve(beforeToolCall(nodesCall("ha.config.lovelace", LOVELACE_SAVE), () => NOW, SESSION, store), "allow-always");
    const action = beforeToolCall(nodesCall("ha.config.lovelace", LOVELACE_RESOURCE), () => NOW, SESSION, store);
    expect(beforeToolCall(nodesCall("ha.config.lovelace", LOVELACE_SAVE), () => NOW, SESSION, store)).not.toHaveProperty("requireApproval");
    for (const result of [session, command, action]) expect(result).toHaveProperty("requireApproval");
  });

  it("never lets a model-supplied marker through", () => {
    const store = granted();
    const forged = { ...SAVE, [APPROVAL_PARAM]: { id: "x", exp: 1, bind: "0" } };
    const marker = innerOf(beforeToolCall(nodesCall("ha.config.automation", forged), () => NOW, SESSION, store))[APPROVAL_PARAM] as {
      id: string;
    };
    expect(marker.id).not.toBe("x");
  });

  it("expires after one hour", () => {
    const store = granted();
    const at = (ms: number) => beforeToolCall(nodesCall("ha.config.automation", SAVE), () => NOW + ms, SESSION, store);
    expect(at(GRANT_TTL_MS - 1)).not.toHaveProperty("requireApproval");
    expect(at(GRANT_TTL_MS)).toHaveProperty("requireApproval");
  });

  it("is gone with a new store (restart)", () => {
    granted();
    const result = beforeToolCall(nodesCall("ha.config.automation", SAVE), () => NOW, SESSION, createGrantStore());
    expect(result).toHaveProperty("requireApproval");
  });

  it("is dropped when its session ends, leaving other sessions", () => {
    const store = granted();
    resolve(beforeToolCall(nodesCall("ha.config.automation", SAVE), () => NOW, "agent:main:keep", store), "allow-always");
    store.revokeSession(SESSION);
    expect(beforeToolCall(nodesCall("ha.config.automation", SAVE), () => NOW, SESSION, store)).toHaveProperty("requireApproval");
    expect(beforeToolCall(nodesCall("ha.config.automation", SAVE), () => NOW, "agent:main:keep", store)).not.toHaveProperty("requireApproval");
  });
});

describe("other decisions", () => {
  it.each(["allow-once", "deny", "timeout", undefined])("%s creates no grant", (decision) => {
    const store = createGrantStore();
    resolve(beforeToolCall(nodesCall("ha.config.automation", SAVE), () => NOW, SESSION, store), decision as string);
    expect(beforeToolCall(nodesCall("ha.config.automation", SAVE), () => NOW, SESSION, store)).toHaveProperty("requireApproval");
  });
});
