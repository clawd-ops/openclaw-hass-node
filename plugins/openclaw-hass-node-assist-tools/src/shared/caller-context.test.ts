import { describe, expect, it } from "vitest";
import { CALLER_PARAM, readAssistSessionKey, runWithCallerContext } from "./caller-context.js";

describe("caller context", () => {
  it("uses the reserved param name", () => {
    expect(CALLER_PARAM).toBe("_openclaw_caller");
  });

  it("is empty outside a scope", () => {
    expect(readAssistSessionKey()).toBeUndefined();
  });

  it.each(["ha-assist:abc", "agent:main:ha-assist:abc", "HA-Assist:ABC"])("accepts %s", (key) => {
    expect(runWithCallerContext(key, () => readAssistSessionKey())).toBe(key);
  });

  it.each([undefined, null, 7, {}, "", "main", "agent:main:main", "ha-assist:", "xha-assist:abc"])(
    "rejects %j",
    (key) => {
      expect(runWithCallerContext(key, () => readAssistSessionKey())).toBeUndefined();
    },
  );

  it("keeps concurrent runs isolated across awaits", async () => {
    const run = (key: string, delay: number) =>
      runWithCallerContext(key, async () => {
        await new Promise((resolve) => setTimeout(resolve, delay));
        return readAssistSessionKey();
      });
    const [a, b] = await Promise.all([run("ha-assist:a", 20), run("ha-assist:b", 1)]);
    expect(a).toBe("ha-assist:a");
    expect(b).toBe("ha-assist:b");
  });
});
