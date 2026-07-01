// Node-invoke policy for openclaw-hass-node-assist-tools.
//
// This policy is the security gate for raw `node.invoke` calls against
// the `ha.*` command names this plugin owns. It MUST enforce the
// per-node allow/deny policy here, not only in the tool handlers —
// otherwise any session with `node.invoke` could bypass the tool
// handlers and hit `ha.call_service` / `ha.get_state` / ... directly.
//
// Default-deny / deny-wins semantics, modeled on /app/extensions/
// file-transfer/src/shared/node-invoke-policy.ts.

import type {
  OpenClawPluginNodeInvokePolicy,
  OpenClawPluginNodeInvokePolicyContext,
  OpenClawPluginNodeInvokePolicyResult,
} from "openclaw/plugin-sdk/plugin-entry";
import { decideGlobPolicy } from "./glob-policy.js";
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
// smuggle multiple ids past the per-entity glob check by exploiting the
// addon's `entity_ids.join(",")` serialization or an unencoded query string.
const ENTITY_ID_PATTERN = /^[a-z0-9_]+\.[a-z0-9_]+$/;

function isValidEntityId(value: string): boolean {
  return ENTITY_ID_PATTERN.test(value);
}

// HA domain / service names are lowercase alphanumeric + underscore. Reject
// anything else — same class of bug as entity_id smuggling: without this,
// a caller could pass `service: "restart?x"` so the deny-glob check sees
// `homeassistant.restart?x` (misses `homeassistant.restart` denylist entry)
// while the addon builds `/api/services/homeassistant/restart?x`, which
// aiohttp still routes to the real `homeassistant.restart` handler.
const DOMAIN_OR_SERVICE_PATTERN = /^[a-z0-9_]+$/;

function isValidDomainOrService(value: string): boolean {
  return DOMAIN_OR_SERVICE_PATTERN.test(value);
}

// ISO-8601 datetime, strict. Rejects anything that could smuggle URL
// delimiters (`&`, `?`, `/`, `#`, whitespace, `%`) into the addon's URL
// builder, which interpolates timestamps unencoded into paths and query
// strings for ha.history / ha.logbook. Allowed shape:
//   YYYY-MM-DDTHH:MM:SS[.fraction][Z|±HH:MM]
// Four-digit year only (no negative or expanded years), no unicode digits.
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
  // After the per-node policy check passes, forward the call to the node
  // so the HA command actually executes. Returning { ok: true } without
  // invoking the node would swallow the call.
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
  const policy = loadPolicyForNode(ctx);
  const decision = decideGlobPolicy({
    candidate: `${domain}.${service}`,
    allow: policy?.allowServices,
    deny: policy?.denyServices,
    subject: "service",
  });
  if (!decision.allowed) {
    return deny("SERVICE_DENIED", `ha.call_service denied: ${decision.reason}`);
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
  const policy = loadPolicyForNode(ctx);
  const decision = decideGlobPolicy({
    candidate: entityId,
    allow: policy?.allowReadEntities,
    deny: policy?.denyReadEntities,
    subject: "entity",
  });
  if (!decision.allowed) {
    return deny("ENTITY_DENIED", `ha.get_state denied: ${decision.reason}`);
  }
  return await forward(ctx, params);
}

async function enforceListStates(
  ctx: OpenClawPluginNodeInvokePolicyContext,
  params: Record<string, unknown>,
): Promise<OpenClawPluginNodeInvokePolicyResult> {
  // list_states returns all entities, so per-entity allowReadEntities
  // can't be matched against an input. Require allowReadEntities to be
  // non-empty as a coarse opt-in. Callers should filter results.
  const policy = loadPolicyForNode(ctx);
  if (!policy?.allowReadEntities || policy.allowReadEntities.length === 0) {
    return deny(
      "ENTITY_DENIED",
      "ha.list_states denied: no allowReadEntities configured for this node",
    );
  }
  return await forward(ctx, params);
}

async function enforceMetadataRead(
  ctx: OpenClawPluginNodeInvokePolicyContext,
  params: Record<string, unknown>,
): Promise<OpenClawPluginNodeInvokePolicyResult> {
  // Metadata reads (list_areas / list_devices / list_entity_registry)
  // don't take entity_id inputs, so per-entity globs can't be applied.
  // Match the tool-layer coarse "plugin is bound to this node" opt-in:
  // require *some* per-node policy entry (or wildcard '*') before
  // forwarding. Prevents raw node.invoke from exposing bulk HA metadata
  // for a node that has no policy at all.
  const policy = loadPolicyForNode(ctx);
  if (!policy) {
    return deny(
      "METADATA_DENIED",
      `${ctx.command} denied: no per-node policy configured for this node`,
    );
  }
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
  // ha.logbook / ha.history: entity_id is optional. When present, gate
  // through per-entity globs (same shape as get_state). When absent, fall
  // back to the coarse allowReadEntities opt-in (like list_states).
  //
  // ha.history additionally accepts a node-shape `entity_ids` array. Raw
  // node.invoke must not be able to pass unvalidated `entity_ids`, so we
  // validate each id against the same per-entity globs. Passing both
  // `entity_id` and `entity_ids` is rejected as conflicting.
  // Validate any timestamp params before touching entity globs so a
  // smuggled `end_time: "...&filter_entity_id=person.rob"` is rejected
  // regardless of allowReadEntities scope. Both Assist-shape (start/end)
  // and node-shape (start_time/end_time) are accepted on the raw invoke
  // path; both need validation.
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
  const policy = loadPolicyForNode(ctx);
  const candidates: string[] = entityId
    ? [entityId]
    : entityIdsArray ?? [];
  if (candidates.length > 0) {
    for (const candidate of candidates) {
      const decision = decideGlobPolicy({
        candidate,
        allow: policy?.allowReadEntities,
        deny: policy?.denyReadEntities,
        subject: "entity",
      });
      if (!decision.allowed) {
        return deny(
          "ENTITY_DENIED",
          `${ctx.command} denied: ${decision.reason}`,
        );
      }
    }
  } else {
    if (!policy?.allowReadEntities || policy.allowReadEntities.length === 0) {
      return deny(
        "ENTITY_DENIED",
        `${ctx.command} without entity_id denied: no allowReadEntities configured for this node`,
      );
    }
  }
  // Translate Assist-shape params (entity_id/start/end) to the node's
  // expected shape before forwarding. Without this, `ha.history {entity_id}`
  // would pass the entity check but reach the node with no filter and
  // return unfiltered history.
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
  serviceCandidate: string,
): Promise<OpenClawPluginNodeInvokePolicyResult> {
  const policy = loadPolicyForNode(ctx);
  const decision = decideGlobPolicy({
    candidate: serviceCandidate,
    allow: policy?.allowServices,
    deny: policy?.denyServices,
    subject: "service",
  });
  if (!decision.allowed) {
    return deny("SERVICE_DENIED", `${ctx.command} denied: ${decision.reason}`);
  }
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
  // Even though the addon posts these as JSON body (not URL-interpolated),
  // validate as defense-in-depth so a smuggled param can't reach HA raw.
  for (const key of ["start_date_time", "end_date_time"]) {
    const denial = validateTimeParam(params, key, ctx.command);
    if (denial) return denial;
  }
  const policy = loadPolicyForNode(ctx);
  const allowed = policy?.allowCalendars ?? [];
  if (!allowed.includes(entityId)) {
    return deny(
      "CALENDAR_DENIED",
      `ha.calendar_get_events denied: ${entityId} not in allowCalendars`,
    );
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
          return await enforceConvenienceAction(ctx, params, "light.turn_on");
        case "ha.light_turn_off":
          return await enforceConvenienceAction(ctx, params, "light.turn_off");
        case "ha.reload_config":
          return await enforceAdminOp(ctx, params, false);
        case "ha.addon_start":
        case "ha.addon_stop":
        case "ha.addon_restart":
          return await enforceAdminOp(ctx, params, true);
        default:
          return deny(
            "COMMAND_NOT_ALLOWED",
            `${ctx.command} has no policy handler`,
          );
      }
    },
  };
}
