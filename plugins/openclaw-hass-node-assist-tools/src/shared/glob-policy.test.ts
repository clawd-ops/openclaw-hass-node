// Tests for the glob-policy decider (pure function, no mocks needed).

import { describe, expect, it } from "vitest";
import { decideGlobPolicy, matchesAnyGlob } from "./glob-policy.js";

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
    expect(matchesAnyGlob("homeassistant.restart", ["light.*", "homeassistant.*"]))
      .toBe(true);
  });
});

describe("decideGlobPolicy", () => {
  it("denies by default when no allow patterns configured", () => {
    const d = decideGlobPolicy({ candidate: "light.turn_on", subject: "service" });
    expect(d.allowed).toBe(false);
    expect(d.reason).toMatch(/no allow patterns/);
  });
  it("allows when candidate matches an allow pattern", () => {
    const d = decideGlobPolicy({
      candidate: "light.turn_on",
      allow: ["light.*"],
      subject: "service",
    });
    expect(d.allowed).toBe(true);
  });
  it("denies when candidate doesn't match any allow pattern", () => {
    const d = decideGlobPolicy({
      candidate: "homeassistant.restart",
      allow: ["light.*"],
      subject: "service",
    });
    expect(d.allowed).toBe(false);
  });
  it("denies when candidate matches a deny pattern (deny wins)", () => {
    const d = decideGlobPolicy({
      candidate: "homeassistant.restart",
      allow: ["homeassistant.*"],
      deny: ["homeassistant.restart"],
      subject: "service",
    });
    expect(d.allowed).toBe(false);
    expect(d.reason).toMatch(/deny pattern/);
  });
});
