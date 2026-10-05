// Native OpenClaw approval for node mutations.
//
// A `before_tool_call` hook on the core `nodes` tool (action "invoke") and on
// this plugin's own Tier B admin tools. It never executes anything: it only
// asks OpenClaw for approval and rewrites the invoke params. OpenClaw applies the rewrite ONLY after the approval
// succeeds, so the approval marker exists nowhere but in that override.
//
// SECURITY: the marker is not a credential. It binds an approval to one exact
// call (command + action + params), expires, and is single-use on the node.
// KNOWN GAP: a caller that bypasses this hook (for example an operator running
// `openclaw nodes invoke` in a shell) can forge a marker. See
// docs/design/AUTHORIZATION-MODEL.md, "Native approvals for node mutations".

import { createHash, randomUUID } from "node:crypto";
import { assistSkipsPrompt } from "./approval-policy.js";
import { CALLER_PARAM, normalizeAssistSessionKey } from "./caller-context.js";
import { grantable, grantSessionKey, sessionGrants, type GrantStore } from "./approval-grants.js";
import { addonSlugRefusal, gatedCallRefusal } from "./node-approval-preflight.js";

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
  "ha.reload_config": [""],
  "ha.update_install": [""],
  "ha.addon_start": [""],
  "ha.addon_stop": [""],
  "ha.addon_restart": [""],
  "ha.addon_update": [""],
};

/** This plugin's Tier B admin tools and the node command each one invokes. */
export const ADMIN_TOOL_COMMANDS: Readonly<Record<string, string>> = {
  ha_reload_config: "ha.reload_config",
  ha_update_install: "ha.update_install",
  ha_addon_start: "ha.addon_start",
  ha_addon_stop: "ha.addon_stop",
  ha_addon_restart: "ha.addon_restart",
  ha_addon_update: "ha.addon_update",
};

const ADMIN_TITLES: Readonly<Record<string, string>> = {
  "ha.reload_config": "Reload HA core config",
  "ha.update_install": "Install HA update",
  "ha.addon_start": "Start HA add-on",
  "ha.addon_stop": "Stop HA add-on",
  "ha.addon_restart": "Restart HA add-on",
  "ha.addon_update": "Update HA add-on",
};

function trimmed(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/**
 * The node invoke params an admin tool sends for the given tool arguments. The
 * tool and the approval hook both call this, so the approval binds to exactly
 * what reaches the node.
 */
export function adminCommandParams(command: string, args: Record<string, unknown>): Record<string, unknown> {
  if (command === "ha.reload_config") {
    const domain = trimmed(args.domain);
    return domain ? { domain } : {};
  }
  if (command === "ha.update_install") {
    const params: Record<string, unknown> = { entity_id: trimmed(args.entity_id) };
    if (typeof args.backup === "boolean") params.backup = args.backup;
    if (typeof args.version === "string" && args.version) params.version = args.version;
    return params;
  }
  return { slug: trimmed(args.slug) };
}

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
const TARGET_KEYS = ["slug", "id", "entity_id", "device_id", "area_id", "entry_id", "url_path", "url", "name", "path", "src", "dst"];

/** Sorted keys, no whitespace; matches the node's ES-compatible canonical serializer (config_mutation.py). */
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

function describeTarget(inner: Record<string, unknown>, command = ""): string {
  if (command === "ha.reload_config") return "core configuration";
  const keys = typeof inner.helper_type === "string" ? [...TARGET_KEYS, "helper_type", `${inner.helper_type}_id`] : TARGET_KEYS;
  const parts = keys.flatMap((key) =>
    typeof inner[key] === "string" ? [`${key}=${JSON.stringify((inner[key] as string).slice(0, 80))}`] : [],
  );
  const config = inner.config as { alias?: unknown } | null | undefined;
  const alias = typeof config?.alias === "string" ? ` (alias ${JSON.stringify(config.alias.slice(0, 80))})` : "";
  return `${parts.join(" ") || "unknown target"}${alias}`;
}

function titleOf(command: string, action: string): string {
  const admin = ADMIN_TITLES[command];
  if (admin !== undefined) return admin;
  const verb = FS_VERBS[command] ?? `${action.charAt(0).toUpperCase()}${action.slice(1).replaceAll("_", " ")}`;
  return `${verb} ${NOUNS[command]}`;
}

type Decision = "allow-once" | "allow-always" | "deny";

/** Everything a session grant keys on, or undefined when allow-always must not be offered. */
type GrantScope = { grants: GrantStore; sessionKey: string; command: string; action: string; nowMs: () => number };

function approvalRequest(title: string, description: string, scope?: GrantScope) {
  const base = {
    title,
    description,
    severity: "warning" as const,
    allowedDecisions: ["allow-once", "deny"] as Decision[],
    timeoutMs: APPROVAL_TIMEOUT_MS,
  };
  if (scope === undefined) return base;
  return {
    ...base,
    description: `${description} Allow always trusts this command and action in this session for 1 hour.`,
    allowedDecisions: ["allow-once", "allow-always", "deny"] as Decision[],
    onResolution(decision: unknown) {
      if (decision === "allow-always") scope.grants.grant(scope.sessionKey, scope.command, scope.action, scope.nowMs());
    },
  };
}

/** Scope for a grant, or undefined for a destructive pair or a call without a session key. */
function grantScope(grants: GrantStore, sessionKey: unknown, command: string, action: string, nowMs: () => number) {
  const key = grantSessionKey(sessionKey);
  return key === undefined || !grantable(command, action) ? undefined : { grants, sessionKey: key, command, action, nowMs };
}

function newMarker(command: string, action: string, params: Record<string, unknown>, nowMs: () => number) {
  return {
    id: randomUUID(),
    exp: Math.floor(nowMs() / 1000) + APPROVAL_TTL_SECONDS,
    bind: approvalBind(command, action, params),
  };
}

/**
 * `before_tool_call` handler for the plugin's own admin tools: always replaces
 * any model-supplied marker with a fresh one bound to the exact node params.
 */
function beforeAdminToolCall(
  command: string,
  params: Record<string, unknown>,
  nowMs: () => number,
  assistSessionKey: string | undefined,
  scope: GrantScope | undefined,
) {
  const { [APPROVAL_PARAM]: _supplied, ...clean } = params;
  const commandParams = adminCommandParams(command, clean);
  const refused = command.startsWith("ha.addon_") ? addonSlugRefusal(commandParams.slug) : undefined;
  if (refused !== undefined) return { block: true, blockReason: refused };
  if (assistSessionKey !== undefined && assistSkipsPrompt(command, "")) return { params: clean };
  const title = titleOf(command, "");
  const approved = { ...clean, [APPROVAL_PARAM]: newMarker(command, "", commandParams, nowMs) };
  if (scope?.grants.has(scope.sessionKey, command, "", nowMs()) === true) return { params: approved };
  return {
    params: approved,
    requireApproval: approvalRequest(
      title,
      `${title}: ${describeTarget(commandParams, command)} via ${command}.`,
      scope,
    ),
  };
}

/**
 * `before_tool_call` handler for the core `nodes` tool and the plugin's admin tools.
 *
 * Every nodes invoke has any model-supplied marker stripped. A
 * call listed in {@link APPROVAL_GATED} additionally requires approval and, once
 * approved, carries a marker. The hook creates the marker when it requests
 * approval; OpenClaw applies it only after the approval succeeds. A gated call whose
 * params hold an integer beyond 2^53 is blocked: re-serializing it would alter
 * the value the human approved.
 */
export function beforeToolCall(
  event: NodesCallEvent,
  nowMs: () => number = Date.now,
  sessionKey?: unknown,
  grants: GrantStore = sessionGrants,
) {
  const { params } = event;
  const assistSessionKey = normalizeAssistSessionKey(sessionKey);
  const adminCommand = Object.hasOwn(ADMIN_TOOL_COMMANDS, event.toolName)
    ? ADMIN_TOOL_COMMANDS[event.toolName]
    : undefined;
  if (adminCommand !== undefined) return beforeAdminToolCall(
      adminCommand,
      params,
      nowMs,
      assistSessionKey,
      grantScope(grants, sessionKey, adminCommand, "", nowMs),
    );
  if (event.toolName !== "nodes" || normalized(params.action) !== "invoke") return undefined;
  const inner = parseInnerParams(params.invokeParamsJson);
  if (inner === undefined) return undefined;

  // A model-supplied marker or caller hint is never trusted. The hint is set only
  // from the host's session key, so the node can resolve the verified Assist caller.
  const { [APPROVAL_PARAM]: supplied, [CALLER_PARAM]: suppliedHint, ...clean } = inner;
  const hinted = assistSessionKey === undefined ? clean : { ...clean, [CALLER_PARAM]: { sessionKey: assistSessionKey } };
  const rewrite = (innerParams: Record<string, unknown>) => ({
    ...params,
    invokeParamsJson: JSON.stringify(innerParams),
  });
  const command = normalized(params.invokeCommand);
  const action = typeof clean.action === "string" ? clean.action.trim() : "";
  const needsApproval = Object.hasOwn(APPROVAL_GATED, command) && APPROVAL_GATED[command]?.includes(action) === true;
  if (!needsApproval) {
    return supplied === undefined && suppliedHint === undefined ? undefined : { params: rewrite(clean) };
  }

  const refused = gatedCallRefusal(command, action, clean);
  if (refused !== undefined) return { block: true, blockReason: refused };
  if (hasUnsafeInteger(params.invokeParamsJson as string)) {
    return {
      block: true,
      blockReason: "Call contains an integer beyond 2^53 that cannot be approved exactly; use a string.",
    };
  }
  if (assistSessionKey !== undefined && assistSkipsPrompt(command, action)) {
    return { params: rewrite(hinted) };
  }
  const scope = grantScope(grants, sessionKey, command, action, nowMs);
  const approved = rewrite({ ...hinted, [APPROVAL_PARAM]: newMarker(command, action, clean, nowMs) });
  if (scope?.grants.has(scope.sessionKey, command, action, nowMs()) === true) return { params: approved };
  return {
    params: approved,
    requireApproval: approvalRequest(
      titleOf(command, action),
      `${titleOf(command, action)}: ${describeTarget(clean, command)} via ${command}${action ? ` action=${action}` : ""}.`,
      scope,
    ),
  };
}
