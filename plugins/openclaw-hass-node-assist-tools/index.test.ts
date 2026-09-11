import { describe, expect, it, vi } from "vitest";
import assistCommandContract from "./src/tools/assist-command-contract.json" with {
  type: "json",
};

vi.mock("openclaw/plugin-sdk/plugin-entry", () => ({
  definePluginEntry: (entry: unknown) => entry,
}));

import entry from "./index.js";

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
    expect(registerTool.mock.calls.map(([tool]) => tool.name)).toEqual(
      assistCommandContract.registrations.map(({ tool_name }) => tool_name),
    );
    expect(registerNodeInvokePolicy).toHaveBeenCalledTimes(1);
    expect(registerNodeInvokePolicy.mock.calls[0]?.[0].commands).toEqual(
      assistCommandContract.registrations.map(({ node_command }) => node_command),
    );
  });
});
