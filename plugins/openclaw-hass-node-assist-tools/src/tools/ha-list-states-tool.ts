// ha_list_states tool handler. Wraps ha.list_states on the bound hass
// node, then filters the result through per-node allowReadEntities /
// denyReadEntities. An optional entity_filter glob narrows further.

import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import { decideGlobPolicy, matchesAnyGlob } from "../shared/glob-policy.js";
import { HA_LIST_STATES_TOOL_DESCRIPTOR } from "./descriptors.js";
import {
  invokeHaCommand,
  readGatewayCallOptions,
  readTrimmedString,
  resolveNodeAndPolicy,
} from "./node-tool-invoke.js";

type HaState = {
  entity_id?: unknown;
  state?: unknown;
  attributes?: unknown;
  last_changed?: unknown;
  last_updated?: unknown;
};

function extractStates(payload: unknown): HaState[] {
  if (Array.isArray(payload)) return payload as HaState[];
  if (payload && typeof payload === "object") {
    const obj = payload as {
      ok?: unknown;
      error?: unknown;
      message?: unknown;
      code?: unknown;
      states?: unknown;
    };
    // Surface node-side error payloads instead of masking them as an empty list.
    if (obj.ok === false) {
      const detail =
        (typeof obj.error === "string" && obj.error) ||
        (typeof obj.message === "string" && obj.message) ||
        (typeof obj.code === "string" && obj.code) ||
        "unknown node error";
      throw new Error(`ha.list_states returned an error payload: ${detail}`);
    }
    if (Array.isArray(obj.states)) return obj.states as HaState[];
  }
  throw new Error(
    `ha.list_states returned an unexpected payload shape: ${JSON.stringify(payload)}`,
  );
}

export function createHaListStatesTool(): AnyAgentTool {
  return {
    ...HA_LIST_STATES_TOOL_DESCRIPTOR,
    execute: async (_toolCallId, args) => {
      const params = args as Record<string, unknown>;
      const nodeIdentifier = readTrimmedString(params, "node");
      const entityFilter = readTrimmedString(params, "entity_filter");
      if (!nodeIdentifier) throw new Error("node required");

      const gatewayOpts = readGatewayCallOptions(params);
      const { nodeId, nodeDisplayName, policy } = await resolveNodeAndPolicy({
        nodeIdentifier,
        gatewayOpts,
      });

      if (!policy?.allowReadEntities || policy.allowReadEntities.length === 0) {
        return {
          content: [
            {
              type: "text",
              text:
                `Refused ha_list_states on ${nodeDisplayName} (${nodeId}): ` +
                `no allowReadEntities configured for this node. ` +
                `Configure plugins.entries.openclaw-hass-node-assist-tools.config.nodes.${nodeIdentifier}.allowReadEntities.`,
            },
          ],
          isError: true,
        };
      }

      const payload = await invokeHaCommand({
        nodeId,
        command: "ha.list_states",
        commandParams: {},
        gatewayOpts,
      });

      const all = extractStates(payload);
      const filtered = all.filter((s) => {
        const id = typeof s.entity_id === "string" ? s.entity_id : "";
        if (!id) return false;
        if (entityFilter && !matchesAnyGlob(id, [entityFilter])) return false;
        const decision = decideGlobPolicy({
          candidate: id,
          allow: policy.allowReadEntities,
          deny: policy.denyReadEntities,
          subject: "entity",
        });
        return decision.allowed;
      });

      return {
        content: [
          {
            type: "text",
            text:
              `States on ${nodeDisplayName} (${nodeId}): ` +
              `${filtered.length}/${all.length} entities after policy` +
              `${entityFilter ? ` + filter '${entityFilter}'` : ""}.\n\n` +
              `${JSON.stringify(filtered, null, 2)}`,
          },
        ],
      };
    },
  };
}
