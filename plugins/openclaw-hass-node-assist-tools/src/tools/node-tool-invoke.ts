// Helper that calls OC's node.invoke against the bound hass node for
// this plugin's tool handlers. Modeled on file-transfer's
// node-tool-invoke.ts but simplified — no media/buffer plumbing.

import { randomUUID } from "node:crypto";
import {
  callGatewayTool,
  listNodes,
  resolveNodeIdFromList,
  type NodeListNode,
} from "openclaw/plugin-sdk/agent-harness-runtime";
import { resolvePluginConfigObject } from "openclaw/plugin-sdk/plugin-config-runtime";
import { readPerNodePolicy, type PerNodePolicy } from "../shared/per-node-policy.js";

export const PLUGIN_ID = "openclaw-hass-node-assist-tools";

/** A failed operation, distinct from failure to reach the gateway/node. */
export class HaCommandError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly source: "node" | "ha" | "gateway" | "transport",
    public readonly retryable?: boolean,
    public readonly details?: unknown,
    public readonly retryAfterMs?: number,
  ) {
    super(`${source}:${code}: ${message}`);
    this.name = "HaCommandError";
  }
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function rejectFailure(value: unknown): void {
  const result = record(value);
  if (!result || !("ok" in result) || result.ok === true) return;
  if (result.ok !== false) {
    throw new HaCommandError("INVALID_RESULT", "Node result ok must be a boolean", "node");
  }
  const error = record(result.error);
  const code = typeof error?.code === "string" ? error.code
    : typeof result.error === "string" ? result.error : "COMMAND_ERROR";
  const message = typeof error?.message === "string" ? error.message
    : typeof result.message === "string" ? result.message : "Node command failed";
  throw new HaCommandError(code, message, code.startsWith("HA_") ? "ha" : "node");
}

export type ResolvedNode = {
  nodeId: string;
  nodeDisplayName: string;
  policy: PerNodePolicy | undefined;
};

export async function resolveNodeAndPolicy(input: {
  nodeIdentifier: string;
  gatewayOpts: Record<string, unknown>;
}): Promise<ResolvedNode> {
  const nodes: NodeListNode[] = await listNodes(input.gatewayOpts);
  if (nodes.length === 0) {
    throw new Error(
      "no paired nodes available; openclaw-hass-node-assist-tools requires a paired hass node.",
    );
  }
  const nodeId = resolveNodeIdFromList(nodes, input.nodeIdentifier, false);
  const nodeMeta = nodes.find((n) => n.nodeId === nodeId);
  const nodeDisplayName = nodeMeta?.displayName ?? input.nodeIdentifier;

  const configResult = await callGatewayTool<{ payload?: unknown }>("config.get", input.gatewayOpts, {});
  const pluginConfig = resolvePluginConfigObject(configResult?.payload, PLUGIN_ID);
  const policy = readPerNodePolicy(pluginConfig, input.nodeIdentifier) ??
    readPerNodePolicy(pluginConfig, nodeId);

  return { nodeId, nodeDisplayName, policy };
}

export async function invokeHaCommand<T = unknown>(input: {
  nodeId: string;
  command: string;
  commandParams: Record<string, unknown>;
  gatewayOpts: Record<string, unknown>;
}): Promise<T> {
  let raw: { payload?: T; ok?: boolean; error?: unknown };
  try {
    raw = await callGatewayTool<{ payload?: T; ok?: boolean; error?: unknown }>(
      "node.invoke",
      input.gatewayOpts,
      {
        nodeId: input.nodeId,
        command: input.command,
        params: input.commandParams,
        // The gateway's node.invoke schema requires idempotencyKey. The plugin-sdk
        // callGatewayTool wrapper does not inject one automatically, so we generate
        // a per-call UUID here. See docs/known-workarounds.md #3.
        idempotencyKey: randomUUID(),
      },
    );
  } catch (error) {
    // The gateway projects a failed node result to an SDK rejection with
    // details.nodeError. Preserve that semantic failure instead of relabeling
    // it as a connection error. Older nodes can still return an inner failure.
    const errorRecord = record(error);
    const nodeError = record(record(errorRecord?.details)?.nodeError);
    if (nodeError) {
      rejectFailure({ ok: false, error: nodeError });
    }
    // Authoritative gateway refusals use this SDK class, including schema,
    // authorization, and command-policy errors. Socket errors can also have a
    // string `code`, so code alone is not proof of a gateway response.
    const gatewayCode = typeof errorRecord?.gatewayCode === "string" ? errorRecord.gatewayCode
      : typeof errorRecord?.code === "string" ? errorRecord.code : undefined;
    if ((errorRecord?.name === "GatewayClientRequestError" || errorRecord?.name === "GatewayProtocolRequestError") && gatewayCode) {
      throw new HaCommandError(
        gatewayCode,
        typeof errorRecord.message === "string" ? errorRecord.message : "Gateway request rejected",
        "gateway",
        typeof errorRecord.retryable === "boolean" ? errorRecord.retryable : undefined,
        errorRecord.details,
        typeof errorRecord.retryAfterMs === "number" ? errorRecord.retryAfterMs : undefined,
      );
    }
    throw new HaCommandError(
      "TRANSPORT_ERROR", error instanceof Error ? error.message : String(error), "transport",
    );
  }

  rejectFailure(raw);
  const payload = record(raw) && "payload" in raw ? raw.payload : raw;
  // Older nodes return outer ok:true even when a handler has refused/failed.
  // All plugin tools share this boundary, so none can render success then.
  rejectFailure(payload);
  if (!record(payload)) {
    throw new HaCommandError("INVALID_RESULT", "Missing or malformed node payload", "node");
  }
  return payload as T;
}

export function readTrimmedString(
  params: Record<string, unknown>,
  key: string,
): string {
  const v = params[key];
  if (typeof v !== "string") return "";
  return v.trim();
}

export function readGatewayCallOptions(
  params: Record<string, unknown>,
): Record<string, unknown> {
  const gatewayUrl = readTrimmedString(params, "gatewayUrl");
  const gatewayToken = readTrimmedString(params, "gatewayToken");
  const opts: Record<string, unknown> = {};
  if (gatewayUrl) opts.gatewayUrl = gatewayUrl;
  if (gatewayToken) opts.gatewayToken = gatewayToken;
  return opts;
}
