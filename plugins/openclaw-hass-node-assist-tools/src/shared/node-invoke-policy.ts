// Node-invoke policy for openclaw-hass-node-assist-tools.
//
// Routing-only: validates parameter shape and format (anti-smuggling
// defense-in-depth), then forwards every allowlisted ha.* command to
// the node. Entity/service/calendar access control is NOT the plugin's
// job — it lives at the hass node's tier/allowCommands + HA's own auth.
//
// The only plugin-scoped gate is the Tier B admin surface
// (allowAdminOps + adminToken), which is a shared secret between the
// plugin and the node's admin handler.

import type {
  OpenClawPluginNodeInvokePolicy,
  OpenClawPluginNodeInvokePolicyContext,
  OpenClawPluginNodeInvokePolicyResult,
} from "openclaw/plugin-sdk/plugin-entry";
import { ASSIST_TOOLS_NODE_INVOKE_COMMANDS } from "./lazy-node-invoke-policy.js";
import { readPerNodePolicy, type PerNodePolicy } from "./per-node-policy.js";

function asRecord(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function readString(params: Record<string, unknown>, key: string): string {
  const v = params[key];
  return typeof v === "string" ? v.trim() : "";
}

// Home Assistant entity IDs are `domain.object_id`, where both parts are
// lowercase alphanumeric + underscore. Reject anything else — in particular,
// commas / whitespace / other delimiters — so a single "entity_id" cannot
// smuggle multiple ids past validation by exploiting the addon's
// `entity_ids.join(",")` serialization or an unencoded query string.
const ENTITY_ID_PATTERN = /^[a-z0-9_]+\.[a-z0-9_]+$/;

function isValidEntityId(value: string): boolean {
  return ENTITY_ID_PATTERN.test(value);
}

// HA domain / service names are lowercase alphanumeric + underscore. Reject
// anything else — without this, a caller could pass `service: "restart?x"`
// so the addon builds `/api/services/homeassistant/restart?x`, which
// aiohttp still routes to the real `homeassistant.restart` handler.
const DOMAIN_OR_SERVICE_PATTERN = /^[a-z0-9_]+$/;

function isValidDomainOrService(value: string): boolean {
  return DOMAIN_OR_SERVICE_PATTERN.test(value);
}

// ISO-8601 datetime, strict. Rejects anything that could smuggle URL
// delimiters (`&`, `?`, `/`, `#`, whitespace, `%`) into the addon's URL
// builder, which interpolates timestamps unencoded into paths and query
// strings for ha.history / ha.logbook.
const ISO_8601_DATETIME_PATTERN =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$/;

function isValidIso8601DateTime(value: string): boolean {
  return ISO_8601_DATETIME_PATTERN.test(value);
}

function validateTimeParam(
  params: Record<string, unknown>,
  key: string,
  commandLabel: string,
): OpenClawPluginNodeInvokePolicyResult | undefined {
  const raw = params[key];
  if (raw === undefined || raw === null) return undefined;
  if (typeof raw !== "string" || !isValidIso8601DateTime(raw.trim())) {
    return deny(
      "INVALID_PARAMS",
      `${commandLabel} ${key} must be an ISO-8601 datetime (YYYY-MM-DDTHH:MM:SS[.fff][Z|±HH:MM])`,
    );
  }
  return undefined;
}

function deny(
  code: string,
  message: string,
): OpenClawPluginNodeInvokePolicyResult {
  return { ok: false, code, message };
}

async function forward(
  ctx: OpenClawPluginNodeInvokePolicyContext,
  params: Record<string, unknown>,
): Promise<OpenClawPluginNodeInvokePolicyResult> {
  if (!ctx.invokeNode) {
    return deny(
      "NODE_UNAVAILABLE",
      `${ctx.command} cannot be forwarded: invokeNode is not bound on this policy context`,
    );
  }
  return await ctx.invokeNode({ params });
}

function loadPolicyForNode(
  ctx: OpenClawPluginNodeInvokePolicyContext,
): PerNodePolicy | undefined {
  return readPerNodePolicy(ctx.pluginConfig, ctx.nodeId);
}

async function enforceCallService(
  ctx: OpenClawPluginNodeInvokePolicyContext,
  params: Record<string, unknown>,
): Promise<OpenClawPluginNodeInvokePolicyResult> {
  const domain = readString(params, "domain");
  const service = readString(params, "service");
  if (!domain || !service) {
    return deny("INVALID_PARAMS", "ha.call_service requires domain and service");
  }
  if (!isValidDomainOrService(domain) || !isValidDomainOrService(service)) {
    return deny(
      "INVALID_PARAMS",
      "ha.call_service domain and service must be lowercase [a-z0-9_]+ (no URL delimiters)",
    );
  }
  return await forward(ctx, params);
}

async function enforceReadEntity(
  ctx: OpenClawPluginNodeInvokePolicyContext,
  params: Record<string, unknown>,
): Promise<OpenClawPluginNodeInvokePolicyResult> {
  const entityId = readString(params, "entity_id");
  if (!entityId) {
    return deny("INVALID_PARAMS", "ha.get_state requires entity_id");
  }
  if (!isValidEntityId(entityId)) {
    return deny(
      "INVALID_PARAMS",
      "ha.get_state entity_id must match domain.object_id",
    );
  }
  return await forward(ctx, params);
}

async function enforceListStates(
  ctx: OpenClawPluginNodeInvokePolicyContext,
  params: Record<string, unknown>,
): Promise<OpenClawPluginNodeInvokePolicyResult> {
  return await forward(ctx, params);
}

async function enforceMetadataRead(
  ctx: OpenClawPluginNodeInvokePolicyContext,
  params: Record<string, unknown>,
): Promise<OpenClawPluginNodeInvokePolicyResult> {
  return await forward(ctx, params);
}

const ADMIN_ADDON_SLUG_DENYLIST: ReadonlyArray<string> = [
  "homeassistant",
  "supervisor",
];

function isAdminAddonSlugDenied(slug: string): boolean {
  if (!slug) return true;
  if (ADMIN_ADDON_SLUG_DENYLIST.includes(slug)) return true;
  return slug.startsWith("core_");
}

async function enforceEntityScopedRead(
  ctx: OpenClawPluginNodeInvokePolicyContext,
  params: Record<string, unknown>,
): Promise<OpenClawPluginNodeInvokePolicyResult> {
  // Validate timestamps first to reject URL-smuggled delimiters.
  // Both Assist-shape (start/end) and node-shape (start_time/end_time)
  // are accepted on the raw invoke path; both need validation.
  for (const key of ["start", "end", "start_time", "end_time"]) {
    const denial = validateTimeParam(params, key, ctx.command);
    if (denial) return denial;
  }
  const entityId = readString(params, "entity_id");
  const rawEntityIds = params.entity_ids;
  let entityIdsArray: string[] | undefined;
  if (rawEntityIds !== undefined) {
    if (ctx.command !== "ha.history") {
      return deny(
        "INVALID_PARAMS",
        `${ctx.command} does not accept entity_ids`,
      );
    }
    if (entityId) {
      return deny(
        "INVALID_PARAMS",
        "ha.history cannot receive both entity_id and entity_ids",
      );
    }
    if (!Array.isArray(rawEntityIds)) {
      return deny(
        "INVALID_PARAMS",
        "ha.history entity_ids must be an array of strings",
      );
    }
    if (rawEntityIds.length === 0) {
      return deny(
        "INVALID_PARAMS",
        "ha.history entity_ids must be a non-empty array",
      );
    }
    const normalized = rawEntityIds.map((v) =>
      typeof v === "string" ? v.trim() : "",
    );
    if (normalized.some((v) => v.length === 0)) {
      return deny(
        "INVALID_PARAMS",
        "ha.history entity_ids must contain non-empty strings",
      );
    }
    if (!normalized.every(isValidEntityId)) {
      return deny(
        "INVALID_PARAMS",
        "ha.history entity_ids must each match domain.object_id",
      );
    }
    entityIdsArray = normalized;
  }
  if (entityId && !isValidEntityId(entityId)) {
    return deny(
      "INVALID_PARAMS",
      `${ctx.command} entity_id must match domain.object_id`,
    );
  }
  // Translate Assist-shape params (entity_id/start/end) to the node's
  // expected shape before forwarding. Without this, `ha.history {entity_id}`
  // would reach the node with no filter and return unfiltered history.
  const forwarded: Record<string, unknown> = { ...params };
  const start = readString(params, "start");
  const end = readString(params, "end");
  if (start && !("start_time" in forwarded)) forwarded.start_time = start;
  if (end && !("end_time" in forwarded)) forwarded.end_time = end;
  delete forwarded.start;
  delete forwarded.end;
  if (ctx.command === "ha.history") {
    if (entityIdsArray) {
      forwarded.entity_ids = entityIdsArray;
    } else if (entityId) {
      forwarded.entity_ids = [entityId];
    }
    delete forwarded.entity_id;
  }
  return await forward(ctx, forwarded);
}

async function enforceConvenienceAction(
  ctx: OpenClawPluginNodeInvokePolicyContext,
  params: Record<string, unknown>,
): Promise<OpenClawPluginNodeInvokePolicyResult> {
  return await forward(ctx, params);
}

async function enforceAdminOp(
  ctx: OpenClawPluginNodeInvokePolicyContext,
  params: Record<string, unknown>,
  requireSlug: boolean,
): Promise<OpenClawPluginNodeInvokePolicyResult> {
  const policy = loadPolicyForNode(ctx);
  if (!policy?.allowAdminOps) {
    return deny(
      "ADMIN_DENIED",
      `${ctx.command} denied: allowAdminOps is not set for this node`,
    );
  }
  const token = policy.adminToken;
  if (typeof token !== "string" || token.length === 0) {
    return deny(
      "ADMIN_DENIED",
      `${ctx.command} denied: adminToken is not configured for this node`,
    );
  }
  if (requireSlug) {
    const slug = readString(params, "slug");
    if (!slug) {
      return deny("INVALID_PARAMS", `${ctx.command} requires slug`);
    }
    if (isAdminAddonSlugDenied(slug)) {
      return deny(
        "ADMIN_SLUG_DENIED",
        `${ctx.command} denied: slug '${slug}' is on the always-deny list (homeassistant / supervisor / core_*)`,
      );
    }
  } else if (ctx.command === "ha.reload_config") {
    const domain = readString(params, "domain");
    if (!domain) {
      return deny("INVALID_PARAMS", "ha.reload_config requires domain");
    }
  }
  // Inject admin_token from per-node config, overriding any caller-supplied
  // value. Callers must not be able to bypass the policy by passing their own.
  const forwardedParams: Record<string, unknown> = {
    ...params,
    admin_token: token,
  };
  return await forward(ctx, forwardedParams);
}

async function enforceCalendarGetEvents(
  ctx: OpenClawPluginNodeInvokePolicyContext,
  params: Record<string, unknown>,
): Promise<OpenClawPluginNodeInvokePolicyResult> {
  const entityId = readString(params, "entity_id");
  if (!entityId) {
    return deny(
      "INVALID_PARAMS",
      "ha.calendar_get_events requires entity_id",
    );
  }
  if (!isValidEntityId(entityId)) {
    return deny(
      "INVALID_PARAMS",
      "ha.calendar_get_events entity_id must match domain.object_id",
    );
  }
  // Validate timestamps as defense-in-depth so a smuggled param can't
  // reach HA raw even though the addon posts these as JSON body.
  for (const key of ["start_date_time", "end_date_time"]) {
    const denial = validateTimeParam(params, key, ctx.command);
    if (denial) return denial;
  }
  return await forward(ctx, params);
}

export function createAssistToolsNodeInvokePolicy(): OpenClawPluginNodeInvokePolicy {
  const allowed = new Set(ASSIST_TOOLS_NODE_INVOKE_COMMANDS);
  return {
    commands: [...ASSIST_TOOLS_NODE_INVOKE_COMMANDS],
    async handle(ctx) {
      if (!allowed.has(ctx.command)) {
        return deny(
          "COMMAND_NOT_ALLOWED",
          `${ctx.command} is not in the plugin allowlist`,
        );
      }
      const params = asRecord(ctx.params);
      switch (ctx.command) {
        case "ha.call_service":
          return await enforceCallService(ctx, params);
        case "ha.get_state":
          return await enforceReadEntity(ctx, params);
        case "ha.list_states":
          return await enforceListStates(ctx, params);
        case "ha.calendar_get_events":
          return await enforceCalendarGetEvents(ctx, params);
        case "ha.list_areas":
        case "ha.list_devices":
        case "ha.list_entity_registry":
        case "ha.list_services":
        case "ha.get_config":
        case "ha.list_events":
        case "ha.list_config_entries":
        case "ha.list_automations":
        case "ha.check_config":
        case "ha.core_logs":
        case "ha.addon_logs":
        case "ha.list_addons":
        case "ha.addon_info":
        case "ha.addon_stats":
        case "ha.addon_changelog":
        case "ha.addon_documentation":
          return await enforceMetadataRead(ctx, params);
        case "ha.logbook":
        case "ha.history":
          return await enforceEntityScopedRead(ctx, params);
        case "ha.light_turn_on":
        case "ha.light_turn_off":
          return await enforceConvenienceAction(ctx, params);
        case "ha.reload_config":
          return await enforceAdminOp(ctx, params, false);
        case "ha.addon_start":
        case "ha.addon_stop":
        case "ha.addon_restart":
        case "ha.addon_update":
          return await enforceAdminOp(ctx, params, true);
        case "ha.update_install":
          return await enforceAdminOp(ctx, params, false);
        default:
          return deny(
            "COMMAND_NOT_ALLOWED",
            `${ctx.command} has no policy handler`,
          );
      }
    },
  };
}
