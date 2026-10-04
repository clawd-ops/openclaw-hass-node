// Tests for the Tier B tools (add-on lifecycle, reload_config, update_install).

import { afterEach, describe, expect, it, vi } from "vitest";
import { adminCommandParams, approvalBind, beforeToolCall } from "../shared/node-approval.js";

const invokeMock = vi.fn();
const resolveMock = vi.fn();

vi.mock("./node-tool-invoke.js", () => ({
  PLUGIN_ID: "openclaw-hass-node-assist-tools",
  invokeHaCommand: (...args: unknown[]) => invokeMock(...args),
  resolveNode: (...args: unknown[]) => resolveMock(...args),
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

function run(factory: string, args: Record<string, unknown>) {
  resolveMock.mockResolvedValue({ nodeId: "hass-001", nodeDisplayName: "Hass" });
  invokeMock.mockResolvedValue({ ok: true });
  return load(factory).then((tool) =>
    tool.execute("c", { node: "hass", ...args }, new AbortController().signal, () => undefined),
  );
}

const MARKER = { id: "m", exp: 1, bind: "b" };
const LIFECYCLE = [
  { factory: "createHaAddonStartTool", tool: "ha_addon_start", command: "ha.addon_start" },
  { factory: "createHaAddonStopTool", tool: "ha_addon_stop", command: "ha.addon_stop" },
  { factory: "createHaAddonRestartTool", tool: "ha_addon_restart", command: "ha.addon_restart" },
  { factory: "createHaAddonUpdateTool", tool: "ha_addon_update", command: "ha.addon_update" },
];
const ALL = [
  ...LIFECYCLE.map((l) => ({ ...l, args: { slug: "openclaw-hass-node" } })),
  { factory: "createHaReloadConfigTool", tool: "ha_reload_config", command: "ha.reload_config", args: { domain: "core" } },
  { factory: "createHaUpdateInstallTool", tool: "ha_update_install", command: "ha.update_install", args: { entity_id: "update.hacs", backup: true } },
];

describe("Tier B tools forward the approval marker", () => {
  for (const t of ALL) {
    it(`${t.command} forwards its params and the hook-supplied marker`, async () => {
      const r = await run(t.factory, { ...t.args, _openclaw_approval: MARKER });
      expect(invokeMock).toHaveBeenCalledTimes(1);
      expect(invokeMock.mock.calls[0]?.[0]).toMatchObject({
        command: t.command,
        commandParams: { ...adminCommandParams(t.command, t.args), _openclaw_approval: MARKER },
      });
      expect(r.isError).toBeUndefined();
    });

    it(`${t.command} sends no marker when none was supplied`, async () => {
      await run(t.factory, t.args);
      expect(invokeMock.mock.calls[0]?.[0].commandParams).not.toHaveProperty("_openclaw_approval");
    });

    it(`${t.command} marker from the hook binds exactly the params the tool sends`, async () => {
      const hook = beforeToolCall({ toolName: t.tool, params: { node: "hass", ...t.args } });
      const overridden = (hook as { params: Record<string, unknown> }).params;
      await run(t.factory, overridden);
      const sent = invokeMock.mock.calls[0]?.[0].commandParams as Record<string, unknown>;
      const { _openclaw_approval: marker, ...rest } = sent;
      expect((marker as { bind: string }).bind).toBe(approvalBind(t.command, "", rest));
    });
  }
});

describe("ha_reload_config", () => {
  it("omits domain entirely when the caller does not supply it", async () => {
    const r = await run("createHaReloadConfigTool", {});
    expect(invokeMock.mock.calls[0]?.[0].commandParams).toEqual({});
    expect(r.isError).toBeUndefined();
  });

  it("forwards a supplied domain for the node to validate", async () => {
    await run("createHaReloadConfigTool", { domain: " automation " });
    expect(invokeMock.mock.calls[0]?.[0].commandParams).toEqual({ domain: "automation" });
  });
});

describe("ha_update_install", () => {
  it("forwards optional backup + version params", async () => {
    await run("createHaUpdateInstallTool", {
      entity_id: "update.home_assistant_core_update",
      backup: true,
      version: "2026.7.0",
    });
    expect(invokeMock.mock.calls[0]?.[0]).toMatchObject({
      command: "ha.update_install",
      commandParams: {
        entity_id: "update.home_assistant_core_update",
        backup: true,
        version: "2026.7.0",
      },
    });
  });

  it("requires entity_id", async () => {
    await expect(run("createHaUpdateInstallTool", {})).rejects.toThrow("entity_id required");
    expect(invokeMock).not.toHaveBeenCalled();
  });
});

describe("addon lifecycle slug policy", () => {
  for (const l of LIFECYCLE) {
    for (const slug of ["homeassistant", "supervisor", "core_dns"]) {
      it(`${l.command} refuses always-denied slug '${slug}' without calling the node`, async () => {
        const r = await run(l.factory, { slug, _openclaw_approval: MARKER });
        expect(invokeMock).not.toHaveBeenCalled();
        expect(r.isError).toBe(true);
      });
    }

    it(`${l.command} requires slug`, async () => {
      await expect(run(l.factory, {})).rejects.toThrow("slug required");
      expect(invokeMock).not.toHaveBeenCalled();
    });
  }
});
