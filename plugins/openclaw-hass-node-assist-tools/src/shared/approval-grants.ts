// Session-scoped "allow always" grants for node mutations.
//
// OpenClaw passes `allow-always` to `onResolution` and leaves persistence to the
// plugin. A grant here trusts exactly one thing: later gated calls with the same
// session key, the same command and the same action skip the prompt. It never
// covers other params (each call still gets a marker bound to its own exact
// params), other sessions, other commands or other actions, and is never offered
// for a destructive pair. Grants live in memory only: a plugin or gateway restart
// clears them. They expire after one hour or when their session ends.

import { DESTRUCTIVE } from "./approval-policy.js";

/** Lifetime of one grant. */
export const GRANT_TTL_MS = 3_600_000;

export type GrantStore = {
  /** Records a grant. */
  grant(sessionKey: string, command: string, action: string, nowMs: number): void;
  /** True while an unexpired grant matches exactly. */
  has(sessionKey: string, command: string, action: string, nowMs: number): boolean;
  /** Drops every grant of a session (session end). */
  revokeSession(sessionKey: string): void;
};

export function createGrantStore(): GrantStore {
  const grants = new Map<string, number>();
  const keyOf = (sessionKey: string, command: string, action: string) =>
    JSON.stringify([sessionKey, command, action]);
  return {
    grant(sessionKey, command, action, nowMs) {
      grants.set(keyOf(sessionKey, command, action), nowMs + GRANT_TTL_MS);
    },
    has(sessionKey, command, action, nowMs) {
      const key = keyOf(sessionKey, command, action);
      const expiresAt = grants.get(key);
      if (expiresAt === undefined) return false;
      if (nowMs >= expiresAt) {
        grants.delete(key);
        return false;
      }
      return true;
    },
    revokeSession(sessionKey) {
      const prefix = `[${JSON.stringify(sessionKey)},`;
      for (const key of grants.keys()) if (key.startsWith(prefix)) grants.delete(key);
    },
  };
}

/** The process-wide store used by the registered hook; a restart starts empty. */
export const sessionGrants: GrantStore = createGrantStore();

/** True when allow-always may be offered: the pair is not in the destructive set. */
export function grantable(command: string, action: string): boolean {
  return !(Object.hasOwn(DESTRUCTIVE, command) && DESTRUCTIVE[command]?.includes(action) === true);
}

/** The trimmed key when it is a non-empty string; grants need a session identity. */
export function grantSessionKey(sessionKey: unknown): string | undefined {
  if (typeof sessionKey !== "string") return undefined;
  const trimmed = sessionKey.trim();
  return trimmed === "" ? undefined : trimmed;
}
