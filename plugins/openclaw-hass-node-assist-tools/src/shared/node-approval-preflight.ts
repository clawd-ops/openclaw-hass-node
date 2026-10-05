// Refuse certain-to-fail calls before asking a human to approve them.
//
// The node refuses some gated calls on static grounds (unknown parameter,
// `.storage/` path, malformed or protected add-on slug) before it reaches its
// approval gate. Prompting for those wastes the approver's attention on a call
// that can never run. The node stays authoritative; this only mirrors its
// pre-gate checks from contracts/approval-preflight.json, which a node-side
// test asserts equals the node's own tables.
//
// Not mirrored (the node alone knows): the add-on lifecycle allowlist (node
// environment config) and parameter value shapes.

import { readFileSync } from "node:fs";

type Preflight = {
  allowed_keys: Record<string, Record<string, string[]>>;
  dynamic_keys: Record<string, { actions: string[]; type_key: string; key_suffix: string }>;
  storage: { path_keys: Record<string, string[]>; marker: string; suffix: string; message: string };
  addon: { slug_pattern: string; max_length: number; core_prefix: string; denylist: string[] };
};

const contract = JSON.parse(
  readFileSync(new URL("../../../../contracts/approval-preflight.json", import.meta.url), "utf8"),
) as Preflight;

const SLUG = new RegExp(contract.addon.slug_pattern);
const LIFECYCLE = new Set(["ha.addon_start", "ha.addon_stop", "ha.addon_restart", "ha.addon_update"]);

/** The node's refusal, rendered as the model sees it from a node error. */
function refusal(code: string, message: string): string {
  return `${code}: ${message}`;
}

/** Slug policy the node applies before its approval gate (static part only). */
export function addonSlugRefusal(slug: unknown): string | undefined {
  const value = typeof slug === "string" ? slug.trim() : "";
  if (!value) return refusal("MISSING_PARAM", "slug is required");
  if (value.length > contract.addon.max_length || !SLUG.test(value)) {
    return refusal("INVALID_PARAM", `invalid addon slug: ${JSON.stringify(value)}`);
  }
  if (value.startsWith(contract.addon.core_prefix)) {
    return refusal("PERMISSION_DENIED", `addon lifecycle denied for core slug: ${value}`);
  }
  if (contract.addon.denylist.includes(value.toLowerCase())) {
    return refusal("PERMISSION_DENIED", `addon lifecycle denied for slug: ${value}`);
  }
  return undefined;
}

/**
 * The node's refusal for a gated `nodes` invoke when it is certain, in the
 * node's own check order (slug policy, unknown keys, then `.storage/` path).
 * `inner` must already have the approval marker removed.
 */
export function gatedCallRefusal(
  command: string,
  action: string,
  inner: Record<string, unknown>,
): string | undefined {
  if (LIFECYCLE.has(command) && inner.slug !== undefined) {
    const slugRefusal = addonSlugRefusal(inner.slug);
    if (slugRefusal !== undefined) return slugRefusal;
  }
  let allowed = contract.allowed_keys[command]?.[action];
  const dynamic = contract.dynamic_keys[command];
  const dynamicType = dynamic !== undefined ? inner[dynamic.type_key] : undefined;
  if (allowed !== undefined && dynamic?.actions.includes(action) && typeof dynamicType === "string") {
    allowed = [...allowed, `${dynamicType.trim()}${dynamic.key_suffix}`];
  }
  if (allowed !== undefined) {
    const unknown = Object.keys(inner).filter((key) => !allowed.includes(key)).sort();
    if (unknown.length > 0) {
      return refusal(
        "INVALID_PARAM",
        `unknown parameter(s): ${unknown.join(", ")}; allowed: ${[...allowed].sort().join(", ")}`,
      );
    }
  }
  for (const key of contract.storage.path_keys[command] ?? []) {
    const path = inner[key];
    if (typeof path === "string" && (path.includes(contract.storage.marker) || path.endsWith(contract.storage.suffix))) {
      return refusal("STORAGE_READONLY", contract.storage.message);
    }
  }
  return undefined;
}
