// Tests for readPerNodePolicy, notably the authorization rule that policy is
// resolved by canonical node ID only and that an explicit per-node entry wins
// over a permissive wildcard default.

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
  });

  it("returns undefined when neither an exact entry nor a wildcard matches", () => {
    const cfg = { nodes: { "other-node": { allowAdminOps: true } } };
    expect(readPerNodePolicy(cfg, "hass-001")).toBeUndefined();
  });

  // Regression: a permissive wildcard must not shadow an explicit canonical
  // deny. The canonical entry is consulted before the wildcard default.
  it("prefers an explicit canonical deny over a permissive wildcard", () => {
    const cfg = {
      nodes: {
        "*": { allowAdminOps: true, adminToken: "wildcard-token" },
        "hass-001": { allowAdminOps: false },
      },
    };
    expect(readPerNodePolicy(cfg, "hass-001")).toEqual({ allowAdminOps: false });
  });

  // Regression for #322: policy resolves by canonical node ID only. An entry
  // keyed by an alias the same node also answers to must never be selected,
  // because the caller chooses that alias per request. Before the fix the
  // caller-supplied identifier was tried first, so `node: "kitchen"` returned
  // the permissive alias entry and bypassed the canonical deny.
  it("ignores an alias-keyed entry and honours the canonical entry", () => {
    const cfg = {
      nodes: {
        "hass-001": { allowAdminOps: false },
        kitchen: { allowAdminOps: true, adminToken: "alias-token" },
      },
    };
    expect(readPerNodePolicy(cfg, "hass-001")).toEqual({ allowAdminOps: false });
  });

  // The inverse direction: an alias-keyed grant must not be reachable even
  // when the canonical ID has no entry at all. Without a canonical entry the
  // result is the wildcard (here absent), never the alias entry.
  it("does not fall back to an alias-keyed entry when the canonical ID is absent", () => {
    const cfg = { nodes: { kitchen: { allowAdminOps: true } } };
    expect(readPerNodePolicy(cfg, "hass-001")).toBeUndefined();
  });

  // An alias-keyed entry must not suppress the wildcard default either: with
  // no canonical entry, resolution falls through to `*`.
  it("falls back to the wildcard rather than an alias-keyed entry", () => {
    const cfg = {
      nodes: {
        "*": { allowAdminOps: false },
        kitchen: { allowAdminOps: true },
      },
    };
    expect(readPerNodePolicy(cfg, "hass-001")).toEqual({ allowAdminOps: false });
  });

  it("never treats a literal '*' node ID as an exact lookup", () => {
    const cfg = { nodes: { "*": { allowAdminOps: true } } };
    expect(readPerNodePolicy(cfg, "*")).toEqual({ allowAdminOps: true });
  });
});
