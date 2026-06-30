// Glob-pattern policy helpers used by tool handlers and the node-invoke policy.
//
// All checks follow the same semantics as the OC core file-transfer plugin:
//   - If both allow and deny are unset → the operation is denied (default-deny).
//   - If allow is set and the candidate matches NONE of the patterns → denied.
//   - If deny is set and the candidate matches ANY of the patterns → denied.
//   - Deny wins.

import { minimatch } from "minimatch";

const MINIMATCH_OPTIONS = { dot: true, nocase: false } as const;

export type GlobPolicyDecision = {
  allowed: boolean;
  reason: string;
};

export function matchesAnyGlob(
  candidate: string,
  patterns: ReadonlyArray<string> | undefined,
): boolean {
  if (!patterns || patterns.length === 0) return false;
  return patterns.some((pattern) => minimatch(candidate, pattern, MINIMATCH_OPTIONS));
}

export function decideGlobPolicy(input: {
  candidate: string;
  allow?: ReadonlyArray<string>;
  deny?: ReadonlyArray<string>;
  subject: string; // "service", "entity", "calendar" — used in reason text
}): GlobPolicyDecision {
  const { candidate, allow, deny, subject } = input;

  if (matchesAnyGlob(candidate, deny)) {
    return {
      allowed: false,
      reason: `${subject} '${candidate}' matches a deny pattern`,
    };
  }

  if (!allow || allow.length === 0) {
    return {
      allowed: false,
      reason: `${subject} '${candidate}' not allowed (no allow patterns configured for this node)`,
    };
  }

  if (!matchesAnyGlob(candidate, allow)) {
    return {
      allowed: false,
      reason: `${subject} '${candidate}' does not match any allow pattern`,
    };
  }

  return { allowed: true, reason: "allowed" };
}
