import { describe, expect, it, vi } from "vitest";
import assistCommandContract from "./src/tools/assist-command-contract.json" with {
  type: "json",
};

vi.mock("openclaw/plugin-sdk/plugin-entry", () => ({
  definePluginEntry: (entry: unknown) => entry,
}));

import entry from "./index.js";
import { readAssistSessionKey } from "./src/shared/caller-context.js";
import * as assistRegistration from "./src/tools/assist-command-registration.js";

describe("plugin entrypoint registration", () => {
  it("registers every executable Assist contract row and its invoke policy", () => {
    const registerTool = vi.fn();
    const registerNodeInvokePolicy = vi.fn();
    const plugin = entry as unknown as {
      register(api: {
        registerTool: typeof registerTool;
        registerNodeInvokePolicy: typeof registerNodeInvokePolicy;
      }): void;
    };

    plugin.register({ registerTool, registerNodeInvokePolicy });

    expect(registerTool).toHaveBeenCalledTimes(assistCommandContract.registrations.length);
    expect(registerTool.mock.calls.map(([, opts]) => opts.name)).toEqual(
      assistCommandContract.registrations.map(({ tool_name }) => tool_name),
    );
    for (const [factory] of registerTool.mock.calls) expect(typeof factory).toBe("function");
    expect(registerNodeInvokePolicy).toHaveBeenCalledTimes(1);
    expect(registerNodeInvokePolicy.mock.calls[0]?.[0].commands).toEqual(
      assistCommandContract.registrations.map(({ node_command }) => node_command),
    );
  });

  it("captures the per-run session key and exposes it to tool execution only", async () => {
    const seen: Array<string | undefined> = [];
    const registrations = assistRegistration.resolvedAssistCommandRegistrations();
    const first = registrations[0]!;
    const spy = vi.spyOn(assistRegistration, "resolvedAssistCommandRegistrations").mockReturnValue([
      {
        ...first,
        loadTool: async () => ({
          ...first.descriptor,
          async execute() {
            seen.push(readAssistSessionKey());
            return { content: [{ type: "text" as const, text: "ok" }] };
          },
        }),
      },
    ]);
    const registerTool = vi.fn();
    (entry as unknown as { register(api: unknown): void }).register({
      registerTool,
      registerNodeInvokePolicy: vi.fn(),
    });
    spy.mockRestore();

    const factory = registerTool.mock.calls[0]![0] as (ctx: { sessionKey?: string }) => {
      execute(...args: unknown[]): Promise<unknown>;
    };
    const toolA = factory({ sessionKey: "agent:main:ha-assist:a" });
    const toolB = factory({ sessionKey: "agent:main:ha-assist:b" });
    const toolNone = factory({});
    await Promise.all([toolA.execute("1", {}), toolB.execute("2", {}), toolNone.execute("3", {})]);
    expect(seen.sort()).toEqual([undefined, "agent:main:ha-assist:a", "agent:main:ha-assist:b"].sort());
    expect(readAssistSessionKey()).toBeUndefined();
  });
});
