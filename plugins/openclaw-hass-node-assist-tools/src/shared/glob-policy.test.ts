// Tests for matchesAnyGlob (moved to entity-filter.ts).
// This file is kept only to avoid breaking any tooling that scans for test files.
// See entity-filter.test.ts for the canonical tests.

import { describe, expect, it } from "vitest";
import { matchesAnyGlob } from "./entity-filter.ts";

describe("matchesAnyGlob (via glob-policy re-export)", () => {
  it("returns false when patterns are undefined or empty", () => {
    expect(matchesAnyGlob("light.living_room", undefined)).toBe(false);
    expect(matchesAnyGlob("light.living_room", [])).toBe(false);
  });
  it("matches a simple glob", () => {
    expect(matchesAnyGlob("light.living_room", ["light.*"])).toBe(true);
    expect(matchesAnyGlob("light.living_room", ["switch.*"])).toBe(false);
  });
});
