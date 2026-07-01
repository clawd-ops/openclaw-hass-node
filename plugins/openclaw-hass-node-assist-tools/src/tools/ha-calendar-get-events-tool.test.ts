// Tests for ha_calendar_get_events.

import { afterEach, describe, expect, it, vi } from "vitest";
import { HA_CALENDAR_GET_EVENTS_TOOL_DESCRIPTOR } from "./descriptors.js";

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

async function loadTool() {
  const mod = await import("./ha-calendar-get-events-tool.js");
  return mod.createHaCalendarGetEventsTool();
}

afterEach(() => {
  invokeMock.mockReset();
  resolveMock.mockReset();
});

describe("ha_calendar_get_events descriptor", () => {
  it("declares the expected tool name", () => {
    expect(HA_CALENDAR_GET_EVENTS_TOOL_DESCRIPTOR.name).toBe(
      "ha_calendar_get_events",
    );
  });
});

describe("ha_calendar_get_events execute", () => {
  it("fetches events when calendar is in allowCalendars", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: { allowCalendars: ["calendar.family"] },
    });
    invokeMock.mockResolvedValue([
      { summary: "soccer", start: "2026-07-01T10:00:00" },
    ]);

    const tool = await loadTool();
    const result = await tool.execute(
      "call-1",
      {
        node: "hass",
        entity_id: "calendar.family",
        start_date_time: "2026-07-01T00:00:00",
        end_date_time: "2026-07-08T00:00:00",
      },
      new AbortController().signal,
      () => undefined,
    );

    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock.mock.calls[0]?.[0]).toMatchObject({
      command: "ha.calendar_get_events",
      commandParams: {
        entity_id: "calendar.family",
        start_date_time: "2026-07-01T00:00:00",
        end_date_time: "2026-07-08T00:00:00",
      },
    });
    expect(result.isError).toBeUndefined();
  });

  it("refuses when calendar is not in allowCalendars", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: { allowCalendars: ["calendar.family"] },
    });

    const tool = await loadTool();
    const result = await tool.execute(
      "call-2",
      {
        node: "hass",
        entity_id: "calendar.work",
        start_date_time: "2026-07-01T00:00:00",
        end_date_time: "2026-07-08T00:00:00",
      },
      new AbortController().signal,
      () => undefined,
    );

    expect(invokeMock).not.toHaveBeenCalled();
    expect(result.isError).toBe(true);
  });

  it("refuses when allowCalendars missing", async () => {
    resolveMock.mockResolvedValue({
      nodeId: "hass-001",
      nodeDisplayName: "Hass",
      policy: {},
    });

    const tool = await loadTool();
    const result = await tool.execute(
      "call-3",
      {
        node: "hass",
        entity_id: "calendar.family",
        start_date_time: "2026-07-01T00:00:00",
        end_date_time: "2026-07-08T00:00:00",
      },
      new AbortController().signal,
      () => undefined,
    );

    expect(invokeMock).not.toHaveBeenCalled();
    expect(result.isError).toBe(true);
  });

  it("throws when entity_id missing", async () => {
    const tool = await loadTool();
    await expect(
      tool.execute(
        "call-4",
        {
          node: "hass",
          start_date_time: "2026-07-01T00:00:00",
          end_date_time: "2026-07-08T00:00:00",
        },
        new AbortController().signal,
        () => undefined,
      ),
    ).rejects.toThrow(/entity_id required/);
  });
});
