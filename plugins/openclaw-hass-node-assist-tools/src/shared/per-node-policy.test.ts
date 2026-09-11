// Tests for readPerNodePolicy, notably the authorization-precedence rule that
// an explicit per-node entry wins over a permissive wildcard default.

import { describe, expect, it } from "vitest";
import { readPerNodePolicy } from "./per-node-policy.js";

describe("readPerNodePolicy", () => {
  it("returns undefined for a missing or malformed plugin config", () => {
    expect(readPerNodePolicy(undefined, "hass-001")).toBeUndefined();
    expect(readPerNodePolicy(null, "hass-001")).toBeUndefined();
    expect(readPerNodePolicy("nope", "hass-001")).toBeUndefined();
    expect(readPerNodePolicy({}, "hass-001")).toBeUndefined();
    expect(readPerNodePolicy({ nodes: "nope" }, "hass-001")).toBeUndefined();
  });

  it("returns the exact node entry when present", () => {
    const cfg = { nodes: { "hass-001": { allowAdminOps: true, adminToken: "t" } } };
    expect(readPerNodePolicy(cfg, "hass-001")).toEqual({ allowAdminOps: true, adminToken: "t" });
  });

  it("falls back to the wildcard default when no exact entry matches", () => {
    const cfg = { nodes: { "*": { allowAdminOps: true } } };
    expect(readPerNodePolicy(cfg, "hass-001")).toEqual({ allowAdminOps: true });
    expect(readPerNodePolicy(cfg, "Hass", "hass-001")).toEqual({ allowAdminOps: true });
  });

  it("returns undefined when neither an exact entry nor a wildcard matches", () => {
    const cfg = { nodes: { "other-node": { allowAdminOps: true } } };
    expect(readPerNodePolicy(cfg, "hass-001")).toBeUndefined();
  });

  // Regression: a permissive wildcard must not shadow an explicit canonical
  // deny when the caller selected the node by its display-name alias. Before
  // the fix, the alias lookup consumed the wildcard and the canonical entry
  // was never consulted, escalating Tier B lifecycle and admin operations.
  it("prefers an explicit canonical deny over a permissive wildcard selected by display name", () => {
    const cfg = {
      nodes: {
        "*": { allowAdminOps: true, adminToken: "wildcard-token" },
        "hass-001": { allowAdminOps: false },
      },
    };
    // Caller passed display name "Hass"; canonical ID resolves to "hass-001".
    expect(readPerNodePolicy(cfg, "Hass", "hass-001")).toEqual({ allowAdminOps: false });
  });

  it("prefers the first exact match when several identifiers have entries", () => {
    const cfg = {
      nodes: {
        "*": { allowAdminOps: true },
        Hass: { allowAdminOps: false },
        "hass-001": { allowAdminOps: true, adminToken: "t" },
      },
    };
    expect(readPerNodePolicy(cfg, "Hass", "hass-001")).toEqual({ allowAdminOps: false });
  });

  it("never treats a literal '*' identifier as an exact lookup", () => {
    const cfg = {
      nodes: {
        "*": { allowAdminOps: true },
        "hass-001": { allowAdminOps: false },
      },
    };
    expect(readPerNodePolicy(cfg, "*", "hass-001")).toEqual({ allowAdminOps: false });
  });
});
