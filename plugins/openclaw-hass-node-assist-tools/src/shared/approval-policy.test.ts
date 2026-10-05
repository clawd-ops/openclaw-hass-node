import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { DESTRUCTIVE, LIFECYCLE_NO_APPROVAL, USER_DIRECTED, assistSkipsPrompt } from "./approval-policy.js";
import { ADMIN_TOOL_COMMANDS, APPROVAL_GATED, APPROVAL_PARAM, beforeToolCall } from "./node-approval.js";

type Table = Record<string, string[]>;
const contract = JSON.parse(
  readFileSync(new URL("../../../../contracts/approval-gated-commands.json", import.meta.url), "utf8"),
) as {
  gated: Table;
  destructive: Table;
  user_directed: Table;
  lifecycle_no_approval: Record<string, Table>;
};

const ASSIST = "agent:main:ha-assist:conv-1";
const PAIRS = Object.entries(APPROVAL_GATED).flatMap(([command, actions]) =>
  actions.map((action) => [command, action] as const),
);

function nodesCall(command: string, inner: Record<string, unknown>) {
  return {
    toolName: "nodes",
    params: { action: "invoke", node: "hass", invokeCommand: command, invokeParamsJson: JSON.stringify(inner) },
  };
}

function innerOf(result: { params?: Record<string, unknown> } | undefined) {
  return JSON.parse(result!.params!.invokeParamsJson as string) as Record<string, unknown>;
}

function inner(command: string, action: string) {
  return action ? { action, id: "example" } : { slug: "example" };
}

describe("approval policy tables", () => {
  it("equal the shared contract", () => {
    expect(DESTRUCTIVE).toEqual(contract.destructive);
    expect(USER_DIRECTED).toEqual(contract.user_directed);
    expect(LIFECYCLE_NO_APPROVAL).toEqual(contract.lifecycle_no_approval);
  });
});

describe("assistSkipsPrompt", () => {
  it.each(PAIRS)("%s %s follows the contract", (command, action) => {
    const destructive = contract.destructive[command]?.includes(action) === true;
    const everyRole = Object.values(contract.lifecycle_no_approval).every(
      (table) => table[command]?.includes(action) === true,
    );
    const expected = !destructive && (contract.user_directed[command]?.includes(action) === true || everyRole);
    expect(assistSkipsPrompt(command, action)).toBe(expected);
  });

  it("prompts for add-on stop, since admin needs approval and the plugin cannot see the role", () => {
    expect(assistSkipsPrompt("ha.addon_stop", "")).toBe(false);
    expect(assistSkipsPrompt("ha.addon_start", "")).toBe(true);
    expect(assistSkipsPrompt("ha.addon_restart", "")).toBe(true);
  });
});

describe("nodes tool hook by origin", () => {
  it.each(PAIRS)("agent-initiated (no Assist session) %s %s always prompts", (command, action) => {
    for (const sessionKey of [undefined, "agent:main:main", "discord:123", "cron:nightly", "agent:main:subagent:x"]) {
      const result = beforeToolCall(nodesCall(command, inner(command, action)), undefined, sessionKey);
      expect(result).toMatchObject({ requireApproval: { allowedDecisions: ["allow-once", "deny"] } });
      expect(innerOf(result)[APPROVAL_PARAM]).toBeDefined();
    }
  });

  it.each(PAIRS)("Assist session %s %s prompts exactly when the policy says so", (command, action) => {
    const result = beforeToolCall(nodesCall(command, inner(command, action)), undefined, ASSIST);
    if (assistSkipsPrompt(command, action)) {
      expect(result).not.toHaveProperty("requireApproval");
      expect(innerOf(result)[APPROVAL_PARAM]).toBeUndefined();
      expect(innerOf(result)._openclaw_caller).toEqual({ sessionKey: ASSIST });
    } else {
      expect(result).toHaveProperty("requireApproval");
      expect(innerOf(result)[APPROVAL_PARAM]).toBeDefined();
    }
  });

  it("destructive calls prompt in an Assist session", () => {
    for (const [command, actions] of Object.entries(contract.destructive)) {
      for (const action of actions) {
        const result = beforeToolCall(nodesCall(command, inner(command, action)), undefined, ASSIST);
        expect(result).toHaveProperty("requireApproval");
      }
    }
  });

  it("replaces a model-supplied caller hint and marker", () => {
    const forged = { ...inner("ha.config.automation", "save"), _openclaw_caller: { sessionKey: "ha-assist:other" }, [APPROVAL_PARAM]: { id: "x" } };
    const result = beforeToolCall(nodesCall("ha.config.automation", forged), undefined, ASSIST);
    expect(innerOf(result)._openclaw_caller).toEqual({ sessionKey: ASSIST });
    expect(innerOf(result)[APPROVAL_PARAM]).toBeUndefined();
    const outside = beforeToolCall(nodesCall("ha.config.automation", forged), undefined, "agent:main:main");
    expect(innerOf(outside)._openclaw_caller).toBeUndefined();
    expect(outside).toHaveProperty("requireApproval");
  });

  it("strips a forged hint from a non-gated call without prompting", () => {
    const result = beforeToolCall(
      nodesCall("ha.get_state", { entity_id: "light.a", _openclaw_caller: { sessionKey: ASSIST } }),
      undefined,
      "agent:main:main",
    );
    expect(innerOf(result)).toEqual({ entity_id: "light.a" });
    expect(result).not.toHaveProperty("requireApproval");
  });
});

describe("plugin admin tools by origin", () => {
  it.each(Object.entries(ADMIN_TOOL_COMMANDS))("%s -> %s", (toolName, command) => {
    const args = { slug: "example", entity_id: "update.example" };
    const inAssist = beforeToolCall({ toolName, params: args }, undefined, ASSIST);
    const outside = beforeToolCall({ toolName, params: args }, undefined, undefined);
    expect(outside).toHaveProperty("requireApproval");
    if (assistSkipsPrompt(command, "")) {
      expect(inAssist).not.toHaveProperty("requireApproval");
      expect(inAssist!.params[APPROVAL_PARAM]).toBeUndefined();
    } else {
      expect(inAssist).toHaveProperty("requireApproval");
    }
  });
});
