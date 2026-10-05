// Refuse certain-to-fail calls before asking a human to approve them.
//
// The node refuses some gated calls on static grounds (unknown parameter,
// `.storage/` path, malformed or protected add-on slug) before it reaches its
// approval gate. Prompting for those wastes the approver's attention on a call
// that can never run. The node stays authoritative; this only mirrors its
// pre-gate checks from approval-preflight.json (inside the plugin, so a copied plugin
// stays self-contained), which a node-side
// test asserts equals the node's own tables.
//
// Conservative rule: a refusal is issued only when the outcome cannot differ
// between JS and Python string handling. Slugs and paths are judged only when
// they are plain ASCII strings (no whitespace or control characters, so no
// trim or strip could change them); an absent slug and unknown parameter names
// (exact keys) are always judged. Anything else is left to the node.
//
// Not mirrored (the node alone knows): the add-on lifecycle denylist (node
// environment config) and parameter value shapes.

import { CALLER_PARAM } from "./caller-context.js";
import preflightContract from "./approval-preflight.json" with { type: "json" };

type Preflight = {
  allowed_keys: Record<string, Record<string, string[]>>;
  dynamic_keys: Record<string, { actions: string[]; type_key: string; key_suffix: string }>;
  storage: { path_keys: Record<string, string[]>; marker: string; suffix: string; message: string };
  addon: { slug_pattern: string; max_length: number; core_prefix: string; denylist: string[] };
};

const contract = preflightContract as Preflight;

const PLAIN_SLUG = /^[A-Za-z0-9_-]*$/;
const PLAIN_PATH = /^[\x21-\x7e]+$/;
const SLUG = new RegExp(contract.addon.slug_pattern);
const LIFECYCLE = new Set(["ha.addon_start", "ha.addon_stop", "ha.addon_restart", "ha.addon_update"]);

/** The node's refusal, rendered as the model sees it from a node error. */
function refusal(code: string, message: string): string {
  return `${code}: ${message}`;
}

/** Slug policy the node applies before its approval gate (static part only). */
export function addonSlugRefusal(slug: unknown): string | undefined {
  if (slug === undefined) return refusal("MISSING_PARAM", "slug is required");
  if (typeof slug !== "string" || !PLAIN_SLUG.test(slug)) return undefined;
  if (!slug) return refusal("MISSING_PARAM", "slug is required");
  if (slug.length > contract.addon.max_length || !SLUG.test(slug)) {
    return refusal("INVALID_PARAM", `invalid addon slug: '${slug}'`);
  }
  if (slug.startsWith(contract.addon.core_prefix)) {
    return refusal("PERMISSION_DENIED", `addon lifecycle denied for core slug: ${slug}`);
  }
  if (contract.addon.denylist.includes(slug)) {
    return refusal("PERMISSION_DENIED", `addon lifecycle denied for slug: ${slug}`);
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
  if (LIFECYCLE.has(command)) {
    const slugRefusal = addonSlugRefusal(inner.slug);
    if (slugRefusal !== undefined) return slugRefusal;
  }
  let allowed = contract.allowed_keys[command]?.[action];
  const dynamic = contract.dynamic_keys[command];
  const dynamicType = dynamic !== undefined ? inner[dynamic.type_key] : undefined;
  let exemptSuffix: string | undefined;
  if (allowed !== undefined && dynamic?.actions.includes(action)) {
    if (typeof dynamicType === "string" && PLAIN_SLUG.test(dynamicType)) {
      allowed = [...allowed, `${dynamicType}${dynamic.key_suffix}`];
    } else {
      exemptSuffix = dynamic.key_suffix;
    }
  }
  if (allowed !== undefined) {
    const known = allowed;
    const unknown = Object.keys(inner)
      .filter((key) => key !== CALLER_PARAM && !known.includes(key) && !(exemptSuffix !== undefined && key.endsWith(exemptSuffix)))
      .sort();
    if (unknown.length > 0) {
      return refusal(
        "INVALID_PARAM",
        `unknown parameter(s): ${unknown.join(", ")}; allowed: ${[...allowed].sort().join(", ")}`,
      );
    }
  }
  for (const key of contract.storage.path_keys[command] ?? []) {
    const path = inner[key];
    if (typeof path === "string" && PLAIN_PATH.test(path) && (path.includes(contract.storage.marker) || path.endsWith(contract.storage.suffix))) {
      return refusal("STORAGE_READONLY", contract.storage.message);
    }
  }
  return undefined;
}
