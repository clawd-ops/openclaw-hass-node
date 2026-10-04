// Native OpenClaw approval for node mutations (prototype: ha.config.automation save).
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
  const id = typeof inner.id === "string" ? inner.id.slice(0, 80) : "unknown id";
  const config = inner.config as { alias?: unknown } | null | undefined;
  const alias = typeof config?.alias === "string" ? ` (alias "${config.alias.slice(0, 80)}")` : "";
  return `automation ${id}${alias}`;
}

/**
 * `before_tool_call` handler for the core `nodes` tool.
 *
 * Every nodes invoke has any model-supplied marker stripped. A
 * `ha.config.automation` save additionally requires approval and, once
 * approved, carries a marker. The hook creates the marker when it requests
 * approval; OpenClaw applies it only after the approval succeeds. A save whose
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
  const needsApproval =
    normalized(params.invokeCommand) === "ha.config.automation" &&
    typeof clean.action === "string" &&
    clean.action.trim() === "save";
  if (!needsApproval) return supplied === undefined ? undefined : { params: rewrite(clean) };

  if (hasUnsafeInteger(params.invokeParamsJson as string)) {
    return {
      block: true,
      blockReason: "Save contains an integer beyond 2^53 that cannot be approved exactly; use a string.",
    };
  }
  const marker = {
    id: randomUUID(),
    exp: Math.floor(nowMs() / 1000) + APPROVAL_TTL_SECONDS,
    bind: approvalBind("ha.config.automation", "save", clean),
  };
  return {
    params: rewrite({ ...clean, [APPROVAL_PARAM]: marker }),
    requireApproval: {
      title: "Save HA automation",
      description: `Save ${describeTarget(clean)} via ha.config.automation action=save.`,
      severity: "warning" as const,
      allowedDecisions: ["allow-once", "deny"] as Array<"allow-once" | "deny">,
      timeoutMs: APPROVAL_TIMEOUT_MS,
    },
  };
}
