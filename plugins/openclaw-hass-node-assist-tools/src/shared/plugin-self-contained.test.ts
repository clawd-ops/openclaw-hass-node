import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";

const root = fileURLToPath(new URL("../../", import.meta.url));

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (name === "node_modules" || name === "dist") return [];
    if (statSync(path).isDirectory()) return sources(path);
    return /\.ts$/.test(name) && !/\.test\.ts$/.test(name) ? [path] : [];
  });
}

// scripts/install-plugin-local.sh copies only this directory, so nothing the
// plugin loads at runtime may live outside it.
it("runtime sources reference nothing outside the plugin directory", () => {
  const escapes: string[] = [];
  for (const file of sources(root)) {
    const text = readFileSync(file, "utf8");
    for (const [, spec] of text.matchAll(/(?:from\s+|import\s*\(\s*|new URL\(\s*)["'](\.[^"']*)["']/g)) {
      const target = resolve(dirname(file), spec);
      if (relative(root, target).startsWith("..")) escapes.push(`${relative(root, file)} -> ${spec}`);
    }
  }
  expect(escapes).toEqual([]);
});
