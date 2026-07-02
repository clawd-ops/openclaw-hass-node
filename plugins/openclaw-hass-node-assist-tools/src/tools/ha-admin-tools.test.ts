// Tests for Tier B admin handlers.

import { afterEach, describe, expect, it, vi } from "vitest";

const invokeMock = vi.fn();
const resolveMock = vi.fn();

vi.mock("./node-tool-invoke.js", () => ({
  PLUGIN_ID: "openclaw-hass-node-assist-tools",
  invokeHaCommand: (...args: unknown[]) => invokeMock(...args),
  resolveNodeAndPolicy: (...args: unknown[]) => resolveMock(...args),
  readGatewayCallOptions: () => ({}),
  readTrimmedString: (params: Record<string, unknown>, key: string) => {
    const v = params[key];
    return typeof v === "string" ? v.trim() : "";
  },
}));

afterEach(() => {
  invokeMock.mockReset();
  resolveMock.mockReset();
});

async function load(name: string) {
  const mod = await import("./ha-admin-tools.js");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (mod as any)[name]();
}

const OK_POLICY = { allowAdminOps: true, adminToken: "T0P-S3CR3T" };

describe("ha_reload_config", () => {
  it("refuses when allowAdminOps is not set", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: { adminToken: "x" },
    });
    const tool = await load("createHaReloadConfigTool");
    const r = await tool.execute(
      "c",
      { node: "hass", domain: "automation" },
      new AbortController().signal,
      () => undefined,
    );
    expect(invokeMock).not.toHaveBeenCalled();
    expect(r.isError).toBe(true);
  });

  it("refuses when adminToken is missing", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: { allowAdminOps: true },
    });
    const tool = await load("createHaReloadConfigTool");
    const r = await tool.execute(
      "c",
      { node: "hass", domain: "automation" },
      new AbortController().signal,
      () => undefined,
    );
    expect(invokeMock).not.toHaveBeenCalled();
    expect(r.isError).toBe(true);
  });

  it("forwards with injected admin_token when fully configured", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: OK_POLICY,
    });
    invokeMock.mockResolvedValue({ ok: true });
    const tool = await load("createHaReloadConfigTool");
    const r = await tool.execute(
      "c",
      { node: "hass", domain: "automation" },
      new AbortController().signal,
      () => undefined,
    );
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock.mock.calls[0]?.[0]).toMatchObject({
      command: "ha.reload_config",
      commandParams: { domain: "automation", admin_token: "T0P-S3CR3T" },
    });
    expect(r.isError).toBeUndefined();
  });
});

describe("addon lifecycle (start/stop/restart/update)", () => {
  const lifecycle = [
    { factory: "createHaAddonStartTool", command: "ha.addon_start" },
    { factory: "createHaAddonStopTool", command: "ha.addon_stop" },
    { factory: "createHaAddonRestartTool", command: "ha.addon_restart" },
    { factory: "createHaAddonUpdateTool", command: "ha.addon_update" },
  ];

  for (const l of lifecycle) {
    it(`${l.command} forwards with slug + admin_token`, async () => {
      resolveMock.mockResolvedValue({
        nodeId: "hass-001",
        nodeDisplayName: "Hass",
        policy: OK_POLICY,
      });
      invokeMock.mockResolvedValue({ ok: true });
      const tool = await load(l.factory);
      const r = await tool.execute(
        "c",
        { node: "hass", slug: "openclaw-hass-node" },
        new AbortController().signal,
        () => undefined,
      );
      expect(invokeMock).toHaveBeenCalledTimes(1);
      expect(invokeMock.mock.calls[0]?.[0]).toMatchObject({
        command: l.command,
        commandParams: {
          slug: "openclaw-hass-node",
          admin_token: "T0P-S3CR3T",
        },
      });
      expect(r.isError).toBeUndefined();
    });

    it(`${l.command} refuses always-denied slug 'homeassistant'`, async () => {
      resolveMock.mockResolvedValue({
        nodeId: "hass-001",
        nodeDisplayName: "Hass",
        policy: OK_POLICY,
      });
      const tool = await load(l.factory);
      const r = await tool.execute(
        "c",
        { node: "hass", slug: "homeassistant" },
        new AbortController().signal,
        () => undefined,
      );
      expect(invokeMock).not.toHaveBeenCalled();
      expect(r.isError).toBe(true);
    });

    it(`${l.command} refuses always-denied slug prefix 'core_dns'`, async () => {
      resolveMock.mockResolvedValue({
        nodeId: "hass-001",
        nodeDisplayName: "Hass",
        policy: OK_POLICY,
      });
      const tool = await load(l.factory);
      const r = await tool.execute(
        "c",
        { node: "hass", slug: "core_dns" },
        new AbortController().signal,
        () => undefined,
      );
      expect(invokeMock).not.toHaveBeenCalled();
      expect(r.isError).toBe(true);
    });
  }
});
