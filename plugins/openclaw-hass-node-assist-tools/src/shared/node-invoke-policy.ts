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
import { readPluginConfig } from "openclaw/plugin-sdk/plugin-config";
import { decideGlobPolicy } from "./glob-policy.js";
import { ASSIST_TOOLS_NODE_INVOKE_COMMANDS } from "./lazy-node-invoke-policy.js";
import { readPerNodePolicy, type PerNodePolicy } from "./per-node-policy.js";

const PLUGIN_ID = "openclaw-hass-node-assist-tools";

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

async function loadPolicyForNode(
  ctx: OpenClawPluginNodeInvokePolicyContext,
): Promise<PerNodePolicy | undefined> {
  const cfg = ctx.pluginConfig ?? (await readPluginConfig(PLUGIN_ID));
  return readPerNodePolicy(cfg, ctx.nodeId);
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
  const policy = await loadPolicyForNode(ctx);
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
  const policy = await loadPolicyForNode(ctx);
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
  const policy = await loadPolicyForNode(ctx);
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
  const policy = await loadPolicyForNode(ctx);
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
  const entityId = readString(params, "entity_id");
  const policy = await loadPolicyForNode(ctx);
  if (entityId) {
    const decision = decideGlobPolicy({
      candidate: entityId,
      allow: policy?.allowReadEntities,
      deny: policy?.denyReadEntities,
      subject: "entity",
    });
    if (!decision.allowed) {
      return deny("ENTITY_DENIED", `${ctx.command} denied: ${decision.reason}`);
    }
  } else {
    if (!policy?.allowReadEntities || policy.allowReadEntities.length === 0) {
      return deny(
        "ENTITY_DENIED",
        `${ctx.command} without entity_id denied: no allowReadEntities configured for this node`,
      );
    }
  }
  return await forward(ctx, params);
}

async function enforceConvenienceAction(
  ctx: OpenClawPluginNodeInvokePolicyContext,
  params: Record<string, unknown>,
  serviceCandidate: string,
): Promise<OpenClawPluginNodeInvokePolicyResult> {
  const policy = await loadPolicyForNode(ctx);
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
  const policy = await loadPolicyForNode(ctx);
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
  const policy = await loadPolicyForNode(ctx);
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
