import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// OpenClaw 2026.11.6's plugin preparation worker asks the host to map every
// relative import (requestHost({kind:"mapping"}), 15s deadline). The host never
// answers for specifiers that need TypeScript resolution: `./x.js` when only
// x.ts exists, or an extensionless `./x`. Plugin load then fails with
// "Plugin preparation callback timed out". Until core is fixed, every relative
// specifier in plugin source must name the real file explicitly (`./x.ts`) or be
// a data file such as `./x.json`. Do not revert to the `.js`-for-`.ts` convention.

const PLUGIN_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

function listSources(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === "dist") continue;
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...listSources(full));
    else if (/\.(ts|mts)$/.test(entry.name) && !entry.name.endsWith(".d.ts")) out.push(full);
  }
  return out;
}

const SPECIFIER =
  /(?:\bfrom\s*|\bimport\s*\(?\s*|\bvi\.(?:mock|doMock|unmock|importActual|importMock)\s*\(\s*|\brequire\s*\(\s*)(["'])(\.{1,2}\/[^"']*)\1/g;

describe("relative import specifiers (OpenClaw 2026.11.6 preparation callback)", () => {
  it("finds source files to scan", () => {
    expect(listSources(PLUGIN_ROOT).length).toBeGreaterThan(10);
  });

  it("never uses .js or extensionless relative specifiers", () => {
    const offenders: string[] = [];
    for (const file of listSources(PLUGIN_ROOT)) {
      const text = readFileSync(file, "utf8");
      for (const match of text.matchAll(SPECIFIER)) {
        const spec = match[2] as string;
        if (!/\.(ts|mts|json)$/.test(spec)) {
          offenders.push(`${relative(PLUGIN_ROOT, file)}: ${spec}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
