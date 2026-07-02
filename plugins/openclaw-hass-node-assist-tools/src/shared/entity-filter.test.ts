// Tests for matchesAnyGlob helper used by ha_list_states entity_filter.

import { describe, expect, it } from "vitest";
import { matchesAnyGlob } from "./entity-filter.js";

describe("matchesAnyGlob", () => {
  it("returns false when patterns are undefined or empty", () => {
    expect(matchesAnyGlob("light.living_room", undefined)).toBe(false);
    expect(matchesAnyGlob("light.living_room", [])).toBe(false);
  });
  it("matches a simple glob", () => {
    expect(matchesAnyGlob("light.living_room", ["light.*"])).toBe(true);
    expect(matchesAnyGlob("light.living_room", ["switch.*"])).toBe(false);
  });
  it("matches across multiple patterns", () => {
    expect(matchesAnyGlob("homeassistant.restart", ["light.*", "homeassistant.*"])).toBe(true);
  });
  it("does not match partial entity domains", () => {
    expect(matchesAnyGlob("light.kitchen", ["sensor.*"])).toBe(false);
  });
});
