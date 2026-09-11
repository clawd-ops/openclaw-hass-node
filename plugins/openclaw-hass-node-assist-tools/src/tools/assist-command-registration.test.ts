import { describe, expect, it, vi } from "vitest";
import { resolvedAssistCommandRegistrations } from "./assist-command-registration.js";

vi.mock("./node-tool-invoke.js", () => ({
  PLUGIN_ID: "openclaw-hass-node-assist-tools",
  invokeHaCommand: vi.fn(),
  readGatewayCallOptions: () => ({}),
  readTrimmedString: () => "",
  resolveNodeAndPolicy: vi.fn(),
}));

describe("Assist executable command contract", () => {
  it("resolves every manifest row to one descriptor and factory", () => {
    const registrations = resolvedAssistCommandRegistrations();
    expect(registrations).toHaveLength(30);
    expect(new Set(registrations.map(({ contract }) => contract.tool_name)).size).toBe(30);
    expect(new Set(registrations.map(({ contract }) => contract.node_command)).size).toBe(30);
    for (const { contract, descriptor, loadTool } of registrations) {
      expect(descriptor.name).toBe(contract.tool_name);
      expect(typeof loadTool).toBe("function");
      expect(Object.keys(contract.emitted_params).sort()).toEqual(
        [...contract.accepted_tool_params].sort(),
      );
    }
  });

  it("loads every registered factory with the declared tool identity", async () => {
    const registrations = resolvedAssistCommandRegistrations();
    const tools = await Promise.all(
      registrations.map(async ({ contract, loadTool }) => ({
        contract,
        tool: await loadTool(),
      })),
    );
    for (const { contract, tool } of tools) {
      expect(tool.name).toBe(contract.tool_name);
    }
  });
});
