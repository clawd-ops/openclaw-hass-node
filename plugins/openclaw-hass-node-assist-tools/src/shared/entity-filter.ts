// Glob helper for caller-supplied entity_filter narrowing in ha_list_states.
// This is not an access-control mechanism — it narrows results at the
// caller's request. Access control lives at the hass node tier/allowCommands.

import { minimatch } from "minimatch";

const MINIMATCH_OPTIONS = { dot: true, nocase: false } as const;

export function matchesAnyGlob(
  candidate: string,
  patterns: ReadonlyArray<string> | undefined,
): boolean {
  if (!patterns || patterns.length === 0) return false;
  return patterns.some((pattern) => minimatch(candidate, pattern, MINIMATCH_OPTIONS));
}
