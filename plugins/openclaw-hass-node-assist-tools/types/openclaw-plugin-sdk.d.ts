// Ambient module declarations for the OpenClaw plugin SDK.
//
// The `openclaw/plugin-sdk/*` packages are not published on npm; they are
// resolved at runtime by the OC gateway when it loads this plugin from
// `openclaw.extensions` in package.json. To let TypeScript pass at
// build/CI time without bundling the SDK, we declare the modules with
// minimal loose types here.
//
// When the OC plugin SDK becomes installable as a real npm package, drop
// this file and add it as a real devDependency.

declare module "openclaw/plugin-sdk/plugin-entry" {
  export type AnyAgentTool = {
    label?: string;
    name: string;
    description: string;
    parameters: unknown;
    execute: (
      toolCallId: string,
      args: unknown,
      signal: AbortSignal,
      onUpdate: (chunk: unknown) => void,
    ) => Promise<{
      content: Array<{ type: "text"; text: string } | { type: "image"; data: string; mimeType: string }>;
      isError?: boolean;
    }>;
  };

  export type OpenClawPluginNodeHostCommand = {
    command: string;
    cap: string;
    dangerous?: boolean;
    handle: (paramsJSON: string | null | undefined) => Promise<string>;
  };

  export type OpenClawPluginNodeMeta = {
    nodeId: string;
    displayName?: string;
    [key: string]: unknown;
  };

  export type OpenClawPluginNodeInvokeResult =
    | { ok: true; payload?: unknown; details?: unknown }
    | { ok: false; code: string; message: string; details?: unknown };

  export type OpenClawPluginApprovalGate = {
    request: (opts: Record<string, unknown>) => Promise<{
      ok: boolean;
      code?: string;
      message?: string;
      [key: string]: unknown;
    }>;
  };

  export type OpenClawPluginNodeInvokePolicyContext = {
    command: string;
    nodeId: string;
    params?: unknown;
    pluginConfig?: unknown;
    node?: OpenClawPluginNodeMeta;
    approvals?: OpenClawPluginApprovalGate;
    invokeNode?: (
      opts: { params: Record<string, unknown> },
    ) => Promise<OpenClawPluginNodeInvokeResult>;
  };

  export type OpenClawPluginNodeInvokePolicyResult =
    | { ok: true; payload?: unknown; details?: unknown }
    | {
        ok: false;
        code: string;
        message: string;
        details?: unknown;
        unavailable?: boolean;
      };

  export type OpenClawPluginNodeInvokePolicy = {
    commands: ReadonlyArray<string>;
    handle: (
      ctx: OpenClawPluginNodeInvokePolicyContext,
    ) => Promise<OpenClawPluginNodeInvokePolicyResult> | OpenClawPluginNodeInvokePolicyResult;
  };

  export type PluginRegisterApi = {
    registerTool: (tool: AnyAgentTool) => void;
    registerNodeInvokePolicy: (policy: OpenClawPluginNodeInvokePolicy) => void;
  };

  export type PluginEntry = {
    id: string;
    name: string;
    description?: string;
    nodeHostCommands?: ReadonlyArray<OpenClawPluginNodeHostCommand>;
    register: (api: PluginRegisterApi) => void;
  };

  export function definePluginEntry(entry: PluginEntry): PluginEntry;
}

declare module "openclaw/plugin-sdk/agent-harness-runtime" {
  export type NodeListNode = {
    nodeId: string;
    displayName?: string;
    [key: string]: unknown;
  };

  export function listNodes(opts?: Record<string, unknown>): Promise<NodeListNode[]>;
  export function resolveNodeIdFromList(
    nodes: NodeListNode[],
    identifier: string,
    allowAuto: boolean,
  ): string;
  export function callGatewayTool<T = unknown>(
    method: string,
    opts: Record<string, unknown>,
    params: Record<string, unknown>,
  ): Promise<T>;
}

declare module "openclaw/plugin-sdk/plugin-config" {
  export function readPluginConfig(pluginId: string): Promise<unknown>;
}
