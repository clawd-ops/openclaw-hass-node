// Summary of an `ha.ws` / `ha.rest` job for the approval prompt.
//
// The gateway caps an approval description at 512 characters, so the prompt cannot
// list a realistic job. It shows a summary instead: how many requests of each kind and
// type, every hard-deny request in full, and a digest prefix that matches the full
// list the agent showed in chat (from the node's plan) and the node's audit log.
//
// Display only. The node classifies, enforces and binds the approval to the exact
// request list; ha-rules.json is a mirror of the node's rule table (a node test
// asserts it is equal), so this file never decides what may run.

import rulesMirror from "./ha-rules.json" with { type: "json" };

export type Kind = "control" | "diagnose" | "fix" | "configure" | "admin";
/** Highest first, the order kinds are listed in. */
const KINDS_DESC: readonly Kind[] = ["admin", "configure", "fix", "diagnose", "control"];
const RULES = rulesMirror.rules as unknown as ReadonlyArray<readonly [string, Kind, boolean]>;
const SUPERVISOR_SEGMENTS = new Set(["hassio", "supervisor"]);
const SUPERVISOR_PREFIXES = [...SUPERVISOR_SEGMENTS];
const PLAIN_TEXT = /^[A-Za-z0-9_.:+/-]*$/;
const DIGEST_PREFIX = 12;

function globRegex(glob: string): RegExp {
  const source = glob.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".");
  return new RegExp(`^${source}$`);
}

function trimSep(text: string, sep: string): string {
  let out = text;
  while (out.startsWith(sep)) out = out.slice(1);
  while (out.endsWith(sep)) out = out.slice(0, -1);
  return out;
}

function matches(pattern: string, target: string): boolean {
  const [pHead = "", ...pRest] = pattern.split(" ");
  const [tHead = "", ...tRest] = target.split(" ");
  if (!globRegex(pHead).test(tHead)) return false;
  const sep = tHead === "svc" ? "." : "/";
  const p = trimSep(pRest.join(" "), sep).split(sep);
  const t = trimSep(tRest.join(" "), sep).split(sep);
  const open = p[p.length - 1] === "**";
  const fixed = open ? p.slice(0, -1) : p;
  if (open ? t.length < fixed.length : t.length !== fixed.length) return false;
  return fixed.every((segment, i) => globRegex(segment).test(t[i] ?? ""));
}

/** The node's classification of a target string; no match is admin. */
export function classifyTarget(target: string): { kind: Kind; hard: boolean } {
  const rule = RULES.find(([pattern]) => matches(pattern, target));
  return rule === undefined ? { kind: "admin", hard: false } : { kind: rule[1], hard: rule[2] };
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;
}

type Item = { tag: "ws" | "rest"; body: Record<string, unknown> };

/** A request as the node reads it: tagged `{ws}` / `{rest}`, or the command's own shape. */
function itemOf(command: string, raw: unknown): Item | undefined {
  const outer = record(raw);
  if (outer === undefined) return undefined;
  const keys = Object.keys(outer);
  const tag = keys.length === 1 ? keys[0] : undefined;
  if (tag === "ws" || tag === "rest") {
    const body = record(outer[tag]);
    return body === undefined ? undefined : { tag, body };
  }
  return { tag: command === "ha.rest" ? "rest" : "ws", body: outer };
}

function shown(value: unknown): string {
  if (typeof value !== "string") return "?";
  return PLAIN_TEXT.test(value) ? value : JSON.stringify(value);
}

function service(item: Item): string | undefined {
  const { body } = item;
  if (item.tag === "ws") {
    const payload = record(body.payload);
    return body.type === "call_service" && typeof payload?.domain === "string" && typeof payload.service === "string"
      ? `${payload.domain}.${payload.service}`
      : undefined;
  }
  const segments = typeof body.path === "string" ? body.path.split("/").slice(1) : [];
  return body.method === "POST" && segments.length === 4 && segments[0] === "api" && segments[1] === "services"
    ? `${segments[2]}.${segments[3]}`
    : undefined;
}

function targetOf(item: Item | undefined): string {
  if (item === undefined) return "unrecognized";
  const svc = service(item);
  if (svc !== undefined) return `svc ${svc}`;
  return item.tag === "ws" ? `ws ${String(item.body.type)}` : `${String(item.body.method)} ${String(item.body.path)}`;
}

/** The service or type a request is counted under; REST paths keep their first three segments. */
function groupOf(item: Item | undefined): string {
  if (item === undefined) return "unrecognized";
  const svc = service(item);
  if (svc !== undefined) return shown(svc);
  if (item.tag === "ws") return shown(item.body.type);
  const segments = typeof item.body.path === "string" ? item.body.path.split("/").slice(1) : [];
  return `${shown(item.body.method)} ${shown(`/${segments.slice(0, 3).join("/")}`)}${segments.length > 3 ? "/…" : ""}`;
}

/** A hard-deny request in full: its type or method and path, and every payload, query and body value. */
function fullLine(item: Item | undefined): string {
  if (item === undefined) return "(not a request object)";
  const { body } = item;
  if (item.tag === "ws") return `ws ${shown(body.type)} ${JSON.stringify(body.payload ?? {})}`;
  const rest = { ...(body.query === undefined ? {} : { query: body.query }), ...(body.body === undefined ? {} : { body: body.body }) };
  return `${shown(body.method)} ${shown(body.path)}${Object.keys(rest).length > 0 ? ` ${JSON.stringify(rest)}` : ""}`;
}

/**
 * The node refuses Supervisor tunnels (WS `supervisor/*`, `hassio/*`, REST paths through
 * `hassio` or `supervisor`) for every caller, so asking a human to approve one is
 * pointless. Returns the reason, or undefined.
 */
export function jobRefusal(command: string, job: readonly unknown[]): string | undefined {
  for (const raw of job) {
    const item = itemOf(command, raw);
    if (item === undefined) continue;
    const { body } = item;
    const segments =
      item.tag === "ws"
        ? typeof body.type === "string" ? [body.type.split("/")[0] ?? ""] : []
        : typeof body.path === "string" ? body.path.split("/").slice(1) : [];
    const prefixed = item.tag === "rest" && SUPERVISOR_PREFIXES.some((prefix) => (segments[1] ?? "").toLowerCase().startsWith(prefix));
    if (prefixed || segments.some((segment) => SUPERVISOR_SEGMENTS.has(segment.toLowerCase()))) {
      return `Supervisor tunnels and routes are not available through ${command}; the node refuses them for every caller. Nothing was run.`;
    }
  }
  return undefined;
}

/**
 * The approval prompt body for a job: counts per kind and per service or type, every
 * hard-deny request listed in full, the digest prefix, and where the full list is.
 * `fits` says whether a candidate summary fits the prompt; the per-type counts are
 * dropped first when it does not. Returns undefined when even the hard-deny lines do
 * not fit: the caller must refuse and ask for smaller jobs.
 */
export function jobSummary(
  command: string,
  job: readonly unknown[],
  digest: string,
  fits: (text: string) => boolean,
): string | undefined {
  const kinds = new Map<Kind, number>();
  const groups = new Map<string, number>();
  const hard: string[] = [];
  for (const raw of job) {
    const item = itemOf(command, raw);
    const { kind, hard: isHard } = classifyTarget(targetOf(item));
    kinds.set(kind, (kinds.get(kind) ?? 0) + 1);
    const group = groupOf(item);
    groups.set(group, (groups.get(group) ?? 0) + 1);
    if (isHard) hard.push(fullLine(item));
  }
  const counts = KINDS_DESC.filter((kind) => kinds.has(kind)).map((kind) => `${kind} ${kinds.get(kind)}`);
  const types = [...groups].map(([group, n]) => `${group} x${n}`).join("; ");
  const hardText = hard.length > 0 ? ` Hard-deny (${hard.length}): ${hard.join("; ")}.` : "";
  const tail = ` Digest ${digest.slice(0, DIGEST_PREFIX)}. Full list: shown in chat and in the node audit log.`;
  const head = `${job.length} request(s) [${counts.join(", ")}].`;
  return [` Types: ${types}.`, ""].map((t) => `${head}${t}${hardText}${tail}`).find(fits);
}
