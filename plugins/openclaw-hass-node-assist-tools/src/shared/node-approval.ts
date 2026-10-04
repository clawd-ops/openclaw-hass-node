// Native OpenClaw approval for node mutations.
//
// A `before_tool_call` hook on the core `nodes` tool (action "invoke"). It
// never executes anything: it only asks OpenClaw for approval and rewrites the
// inner invoke params. OpenClaw applies the rewrite ONLY after the approval
// succeeds, so the approval marker exists nowhere but in that override.
//
// SECURITY: the marker is not a credential. It binds an approval to one exact
// call (command + action + params), expires, and is single-use on the node.
// KNOWN GAP: a caller that bypasses this hook (for example an operator running
// `openclaw nodes invoke` in a shell) can forge a marker. See
// docs/design/AUTHORIZATION-MODEL.md, "Native approvals for node mutations".

import { createHash, randomUUID } from "node:crypto";
import { CALLER_PARAM } from "./caller-context.js";

/** Reserved node.invoke param carrying the approval marker. */
export const APPROVAL_PARAM = "_openclaw_approval";

// The override is fixed when the hook returns, before the human decides, so the
// marker must outlive the whole approval window plus time to dispatch the call:
// exp = hook time + APPROVAL_TIMEOUT + dispatch grace. Single use is enforced by
// the node's replay cache, not by a short expiry.
const APPROVAL_TIMEOUT_MS = 600_000;
const DISPATCH_GRACE_SECONDS = 120;
const APPROVAL_TTL_SECONDS = APPROVAL_TIMEOUT_MS / 1000 + DISPATCH_GRACE_SECONDS;
const RESERVED_PARAMS = new Set([APPROVAL_PARAM, CALLER_PARAM]);

/**
 * Commands and actions that need approval. Mirrors the node exactly; the shared
 * contract file contracts/approval-gated-commands.json is asserted equal in tests.
 * An empty string stands for a command with no action param (the fs.* writes);
 * the node decides per call whether the path is protected and ignores an
 * unneeded marker on an unprotected write.
 */
export const APPROVAL_GATED: Readonly<Record<string, readonly string[]>> = {
  "ha.config.automation": ["save", "delete"],
  "ha.config.script": ["save", "delete"],
  "ha.config.scene": ["save", "delete"],
  "ha.config.helpers": ["create", "update", "delete"],
  "ha.config.area_registry": ["create", "update", "delete"],
  "ha.config.device_registry": ["update"],
  "ha.config.entity_registry": ["update", "remove"],
  "ha.config.config_entries": ["disable", "enable"],
  "ha.config.lovelace": ["save", "resources_create"],
  "fs.write": [""],
  "fs.restore": [""],
  "fs.move": [""],
  "fs.delete": [""],
  "fs.patch": [""],
};

const NOUNS: Readonly<Record<string, string>> = {
  "ha.config.automation": "HA automation",
  "ha.config.script": "HA script",
  "ha.config.scene": "HA scene",
  "ha.config.helpers": "HA helper",
  "ha.config.area_registry": "HA area",
  "ha.config.device_registry": "HA device",
  "ha.config.entity_registry": "HA entity",
  "ha.config.config_entries": "HA integration",
  "ha.config.lovelace": "HA dashboard",
  "fs.write": "file",
  "fs.restore": "file",
  "fs.move": "file",
  "fs.delete": "file",
  "fs.patch": "file",
};
const FS_VERBS: Readonly<Record<string, string>> = {
  "fs.write": "Write",
  "fs.restore": "Restore",
  "fs.move": "Move",
  "fs.delete": "Delete",
  "fs.patch": "Patch",
};
const TARGET_KEYS = ["id", "entity_id", "device_id", "area_id", "entry_id", "url_path", "url", "name", "path", "src", "dst"];

/** Sorted keys, no whitespace; identical to Python `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`. */
export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

/** sha256 hex binding an approval to one call; reserved fields are excluded from `params`. */
export function approvalBind(
  command: string,
  action: string,
  params: Record<string, unknown>,
): string {
  const body = Object.fromEntries(
    Object.entries(params).filter(([key]) => !RESERVED_PARAMS.has(key)),
  );
  return createHash("sha256")
    .update(canonicalJson({ command, action, params: body }), "utf8")
    .digest("hex");
}

type NodesCallEvent = { toolName: string; params: Record<string, unknown> };

function normalized(value: unknown): string {
  return typeof value === "string" ? value.trim().toLowerCase() : "";
}

/** True when `raw` holds an integer beyond 2^53 that JSON.parse/stringify would silently alter. */
function hasUnsafeInteger(raw: string): boolean {
  let unsafe = false;
  const reviver = (_key: string, value: unknown, context?: { source?: string }) => {
    if (typeof value === "number" && /^-?\d+$/.test(context?.source ?? "") && !Number.isSafeInteger(value)) {
      unsafe = true;
    }
    return value;
  };
  JSON.parse(raw, reviver as Parameters<typeof JSON.parse>[1]);
  return unsafe;
}

function parseInnerParams(raw: unknown): Record<string, unknown> | undefined {
  if (typeof raw !== "string") return undefined;
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : undefined;
  } catch {
    return undefined;
  }
}

function describeTarget(inner: Record<string, unknown>): string {
  const keys = typeof inner.helper_type === "string" ? [...TARGET_KEYS, "helper_type", `${inner.helper_type}_id`] : TARGET_KEYS;
  const parts = keys.flatMap((key) =>
    typeof inner[key] === "string" ? [`${key}=${JSON.stringify((inner[key] as string).slice(0, 80))}`] : [],
  );
  const config = inner.config as { alias?: unknown } | null | undefined;
  const alias = typeof config?.alias === "string" ? ` (alias ${JSON.stringify(config.alias.slice(0, 80))})` : "";
  return `${parts.join(" ") || "unknown target"}${alias}`;
}

function titleOf(command: string, action: string): string {
  const verb = FS_VERBS[command] ?? `${action.charAt(0).toUpperCase()}${action.slice(1).replaceAll("_", " ")}`;
  return `${verb} ${NOUNS[command]}`;
}

/**
 * `before_tool_call` handler for the core `nodes` tool.
 *
 * Every nodes invoke has any model-supplied marker stripped. A
 * call listed in {@link APPROVAL_GATED} additionally requires approval and, once
 * approved, carries a marker. The hook creates the marker when it requests
 * approval; OpenClaw applies it only after the approval succeeds. A gated call whose
 * params hold an integer beyond 2^53 is blocked: re-serializing it would alter
 * the value the human approved.
 */
export function beforeNodesToolCall(event: NodesCallEvent, nowMs: () => number = Date.now) {
  const { params } = event;
  if (event.toolName !== "nodes" || normalized(params.action) !== "invoke") return undefined;
  const inner = parseInnerParams(params.invokeParamsJson);
  if (inner === undefined) return undefined;

  const { [APPROVAL_PARAM]: supplied, ...clean } = inner;
  const rewrite = (innerParams: Record<string, unknown>) => ({
    ...params,
    invokeParamsJson: JSON.stringify(innerParams),
  });
  const command = normalized(params.invokeCommand);
  const action = typeof clean.action === "string" ? clean.action.trim() : "";
  const needsApproval = Object.hasOwn(APPROVAL_GATED, command) && APPROVAL_GATED[command]?.includes(action) === true;
  if (!needsApproval) return supplied === undefined ? undefined : { params: rewrite(clean) };

  if (hasUnsafeInteger(params.invokeParamsJson as string)) {
    return {
      block: true,
      blockReason: "Call contains an integer beyond 2^53 that cannot be approved exactly; use a string.",
    };
  }
  const marker = {
    id: randomUUID(),
    exp: Math.floor(nowMs() / 1000) + APPROVAL_TTL_SECONDS,
    bind: approvalBind(command, action, clean),
  };
  return {
    params: rewrite({ ...clean, [APPROVAL_PARAM]: marker }),
    requireApproval: {
      title: titleOf(command, action),
      description: `${titleOf(command, action)}: ${describeTarget(clean)} via ${command}${action ? ` action=${action}` : ""}.`,
      severity: "warning" as const,
      allowedDecisions: ["allow-once", "deny"] as Array<"allow-once" | "deny">,
      timeoutMs: APPROVAL_TIMEOUT_MS,
    },
  };
}
