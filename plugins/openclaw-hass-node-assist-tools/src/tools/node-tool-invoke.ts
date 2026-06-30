// Helper that calls OC's node.invoke against the bound hass node for
// this plugin's tool handlers. Modeled on file-transfer's
// node-tool-invoke.ts but simplified — no media/buffer plumbing.

import {
  callGatewayTool,
  listNodes,
  resolveNodeIdFromList,
  type NodeListNode,
} from "openclaw/plugin-sdk/agent-harness-runtime";
import { readPluginConfig } from "openclaw/plugin-sdk/plugin-config";
import { readPerNodePolicy, type PerNodePolicy } from "../shared/per-node-policy.js";

export const PLUGIN_ID = "openclaw-hass-node-assist-tools";

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

  const pluginConfig = await readPluginConfig(PLUGIN_ID);
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
  const raw = await callGatewayTool<{ payload?: T; ok?: boolean; error?: unknown }>(
    "node.invoke",
    input.gatewayOpts,
    {
      nodeId: input.nodeId,
      command: input.command,
      params: input.commandParams,
    },
  );

  if (raw && typeof raw === "object" && "ok" in raw && raw.ok === false) {
    const err = raw.error ?? "node command failed";
    throw new Error(
      typeof err === "string" ? err : JSON.stringify(err),
    );
  }

  return (raw?.payload ?? (raw as unknown)) as T;
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
