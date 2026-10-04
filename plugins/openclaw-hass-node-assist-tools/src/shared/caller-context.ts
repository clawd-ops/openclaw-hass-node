// Per-run caller context for the Assist tool wrappers (WP2c-1).
//
// The host builds each tool through a per-run factory whose context carries
// `sessionKey`. `index.ts` captures that key and runs every tool execution
// inside `runWithCallerContext`; `invokeHaCommand` reads it back here and
// attaches it to node.invoke params under CALLER_PARAM.
//
// SECURITY: the attached value is a LOOKUP HINT, never an identity claim
// (design D5). It names which node-owned Assist turn the node should look up;
// the node derives the actual role from its own registry and treats a missing
// or unknown hint as no more privileged than the absent-key default. Nothing
// here proves who the caller is, and a forged hint can only lower privilege.
// Direct node.invoke calls (no wrapper) do not carry the hint and remain
// operator-default; this does not close #275 or #289.

import { AsyncLocalStorage } from "node:async_hooks";

/** Reserved node.invoke param carrying the lookup hint. Tool args cannot set it. */
export const CALLER_PARAM = "_openclaw_caller";

// Assist sessions are "ha-assist:<conversation_id>", possibly agent-prefixed
// ("agent:<id>:ha-assist:<conversation_id>") by the host.
const ASSIST_SESSION_KEY = /(^|:)ha-assist:\S+$/i;

const storage = new AsyncLocalStorage<{ sessionKey: unknown }>();

/** Run `fn` with the host-supplied session key bound as the caller context. */
export function runWithCallerContext<T>(sessionKey: unknown, fn: () => T): T {
  return storage.run({ sessionKey }, fn);
}

/**
 * The bound session key, or undefined when there is no scope or the key is
 * missing, not a string, or not Assist-shaped. Callers must refuse on undefined.
 */
export function readAssistSessionKey(): string | undefined {
  const sessionKey = storage.getStore()?.sessionKey;
  if (typeof sessionKey !== "string") return undefined;
  const trimmed = sessionKey.trim();
  return ASSIST_SESSION_KEY.test(trimmed) ? trimmed : undefined;
}
