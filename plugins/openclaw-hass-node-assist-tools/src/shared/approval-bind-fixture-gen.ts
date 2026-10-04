// Deterministic generator for contracts/approval-bind-generated.json.
//
// Emits random JSON values (seeded PRNG) with the TypeScript canonical string
// and approval digest of each, so the node's Python serializer is checked
// against this plugin's on strings, numbers, key order and nesting. Params are
// stored as raw JSON text so `-0` and exact number spellings survive.
// Regenerate: UPDATE_APPROVAL_FIXTURE=1 pnpm --filter @openclaw-hass-node/assist-tools test

import { canonicalJson, approvalBind } from "./node-approval.js";

export const CASE_COUNT = 300;
const SEED = 0x5eed_0b1d;

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const NUMBER_LITERALS = [
  "-0", "-0.0", "0", "0.5", "-1", "1e21", "1e-7", "1.5e-7", "0.000001", "123456789012345680000",
  "1e+22", "5e-324", "1.7976931348623157e308", "9007199254740991", "-9007199254740991", "0.1", "100.0",
  "4.35", "1e300", "2.5e-10", "123456.789e3",
];
const SPECIAL_STRINGS = [
  "", "\ud800", "\udfff", "a\ud800b", "\ud83d", "\ude00\ud83d", "😀", "\u{1f600}", "\u{10ffff}",
  "\u0000", "\u001f", "\u007f", "  ", "\b\f\n\r\t", "\"\\/", "café ☃", "﻿", "￿",
];

export interface FixtureCase {
  command: string;
  action: string;
  params_json: string;
  canonical: string;
  bind: string;
}

export function generateCases(): FixtureCase[] {
  const rnd = mulberry32(SEED);
  const int = (n: number) => Math.floor(rnd() * n);
  const pick = <T>(xs: readonly T[]): T => xs[int(xs.length)] as T;

  const randString = (): string => {
    if (rnd() < 0.3) return pick(SPECIAL_STRINGS);
    let out = "";
    for (let i = int(8); i > 0; i--) {
      const kind = int(6);
      if (kind === 0) out += String.fromCharCode(int(0x20));
      else if (kind === 1) out += String.fromCharCode(0xd800 + int(0x800));
      else if (kind === 2) out += String.fromCodePoint(0x10000 + int(0x100000));
      else if (kind === 3) out += String.fromCharCode(0xa0 + int(0xd700 - 0xa0));
      else if (kind === 4) out += pick(['"', "\\", "/", "\b", "\f", "\n", "\r", "\t"]);
      else out += String.fromCharCode(0x20 + int(0x5f));
    }
    return out;
  };

  // Values are built as raw JSON text so number spellings are exact.
  const randValue = (depth: number): string => {
    const kind = int(depth >= 3 ? 5 : 7);
    switch (kind) {
      case 0: return "null";
      case 1: return rnd() < 0.5 ? "true" : "false";
      case 2: return pick(NUMBER_LITERALS);
      case 3: {
        const mode = int(4);
        if (mode === 0) return String(Math.trunc((rnd() - 0.5) * 2 ** 53));
        const m = (rnd() - 0.5) * 10 ** int(8);
        const x = mode === 1 ? m : mode === 2 ? m * 10 ** (int(60) - 30) : m / 10 ** int(25);
        return Number.isFinite(x) ? String(x) : "0";
      }
      case 4: return JSON.stringify(randString());
      case 5: return `[${Array.from({ length: int(5) }, () => randValue(depth + 1)).join(",")}]`;
      default: {
        const members = new Map<string, string>();
        for (let i = int(5); i > 0; i--) members.set(randString(), randValue(depth + 1));
        return `{${[...members].map(([k, v]) => `${JSON.stringify(k)}:${v}`).join(",")}}`;
      }
    }
  };

  const cases: FixtureCase[] = [];
  for (let i = 0; i < CASE_COUNT; i++) {
    const members = new Map<string, string>();
    for (let n = 1 + int(4); n > 0; n--) members.set(randString(), randValue(0));
    if (rnd() < 0.2) members.set("_openclaw_approval", randValue(1));
    if (rnd() < 0.2) members.set("_openclaw_caller", randValue(1));
    const paramsJson = `{${[...members].map(([k, v]) => `${JSON.stringify(k)}:${v}`).join(",")}}`;
    const params = JSON.parse(paramsJson) as Record<string, unknown>;
    const command = pick(["ha.config.automation", "fs.write", "ha.reload_config", "ha.config.helpers"]);
    const action = pick(["save", "delete", "", "create"]);
    const { _openclaw_approval: _a, _openclaw_caller: _c, ...body } = params;
    cases.push({
      command,
      action,
      params_json: paramsJson,
      canonical: canonicalJson({ command, action, params: body }),
      bind: approvalBind(command, action, params),
    });
  }
  return cases;
}

export function renderFixture(): string {
  const comment =
    "Generated cross-language fixture. Do not edit; regenerate with UPDATE_APPROVAL_FIXTURE=1 (see approval-bind-fixture-gen.ts). params_json is raw JSON text; canonical and bind are the TypeScript outputs the Python node must reproduce exactly.";
  return `${JSON.stringify({ comment, cases: generateCases() }, null, 2)}\n`;
}
