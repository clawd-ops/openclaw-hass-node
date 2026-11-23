import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { describe, expect, it } from "vitest";

// OpenClaw 2026.11.6's plugin preparation worker asks the host to map every
// relative import (requestHost({kind:"mapping"}), 15s deadline). The host never
// answers for specifiers that need TypeScript resolution: `./x.js` when only
// x.ts exists, or an extensionless `./x`. Plugin load then fails with
// "Plugin preparation callback timed out". Until core is fixed, every relative
// specifier in plugin source must name the real file explicitly (`./x.ts`) or be
// a data file such as `./x.json`, and that file must exist. Do not revert to the
// `.js`-for-`.ts` convention.
//
// The scan uses the TypeScript AST and covers: static import / export-from
// (multi-line included), `import type`, import-type nodes, dynamic `import()`
// with string or no-substitution template literals, `require()`,
// `vi.mock`/`doMock`/`unmock`/`importActual`/`importMock`, and
// `new URL("./...", import.meta.url)`.

const PLUGIN_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

/** Directory URLs used only as test fixtures. Key: `<plugin-relative file>|<specifier>`. */
const ALLOWED_DIRECTORY_URLS = new Set([
  "src/tools/invoke-contract.test.ts|../../../../", // repo root, to locate .venv and Python fixtures
  "src/shared/plugin-self-contained.test.ts|../../", // plugin root, to scan its own files
]);

/** Relative template literals with substitutions in import() (unverifiable). Key: `<file>|<template text>`. Needs a reason. */
const ALLOWED_TEMPLATE_IMPORTS = new Map<string, string>();

const BAD_EXT = /\.(js|mjs|cjs|jsx)$/;
const GOOD_IMPORT_EXT = /\.(ts|mts|json)$/;
const VI_METHODS = new Set(["mock", "doMock", "unmock", "importActual", "importMock"]);

export type Violation = { file: string; specifier: string; reason: string };

function isRelative(spec: string): boolean {
  return spec.startsWith("./") || spec.startsWith("../");
}

/** Strip parentheses and type-only wrappers (`as`, `satisfies`, `!`, `<T>x`) so matching is structural. */
function unwrap(node: ts.Node | undefined): ts.Node | undefined {
  let n = node;
  while (
    n &&
    (ts.isParenthesizedExpression(n) ||
      ts.isAsExpression(n) ||
      ts.isSatisfiesExpression(n) ||
      ts.isNonNullExpression(n) ||
      ts.isTypeAssertionExpression(n))
  ) {
    n = n.expression;
  }
  return n;
}

function literalText(node: ts.Node | undefined): string | undefined {
  const n = unwrap(node);
  if (n && (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n))) return n.text;
  return undefined;
}

/** True for `import.meta.url` (any wrapping), matched on the AST, not on text. */
function isImportMetaUrl(node: ts.Node | undefined): boolean {
  const n = unwrap(node);
  if (!n || !ts.isPropertyAccessExpression(n) || n.name.text !== "url") return false;
  const base = unwrap(n.expression);
  return !!base && ts.isMetaProperty(base) && base.keywordToken === ts.SyntaxKind.ImportKeyword && base.name.text === "meta";
}

function isIdent(node: ts.Node | undefined, name: string): boolean {
  const n = unwrap(node);
  return !!n && ts.isIdentifier(n) && n.text === name;
}

export function findViolations(
  relFile: string,
  absFile: string,
  text: string,
  exists: (abs: string) => boolean,
): Violation[] {
  const out: Violation[] = [];
  const sf = ts.createSourceFile(absFile, text, ts.ScriptTarget.Latest, true);

  const checkSpec = (spec: string, kind: "import" | "url") => {
    if (!isRelative(spec)) return;
    const bad = (reason: string) => out.push({ file: relFile, specifier: spec, reason });
    if (BAD_EXT.test(spec)) return bad("relative .js specifier; use the real .ts file");
    if (kind === "url" && spec.endsWith("/")) {
      if (!ALLOWED_DIRECTORY_URLS.has(`${relFile}|${spec}`)) bad("directory URL not on the allowlist");
      else if (!exists(resolve(dirname(absFile), spec))) bad("allowed directory URL does not exist");
      return;
    }
    if (kind === "import" && !GOOD_IMPORT_EXT.test(spec)) return bad("extensionless relative specifier");
    if (kind === "url" && !/\.[A-Za-z0-9]+$/.test(spec)) return bad("extensionless URL target");
    if (!exists(resolve(dirname(absFile), spec))) bad("target file does not exist");
  };

  const visit = (node: ts.Node) => {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) {
      const s = literalText(node.moduleSpecifier);
      if (s !== undefined) checkSpec(s, "import");
    } else if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument)) {
      const s = literalText(node.argument.literal);
      if (s !== undefined) checkSpec(s, "import");
    } else if (ts.isCallExpression(node)) {
      const callee = unwrap(node.expression);
      const arg = node.arguments[0];
      const argU = unwrap(arg);
      if (callee?.kind === ts.SyntaxKind.ImportKeyword) {
        if (argU && ts.isTemplateExpression(argU)) {
          const key = `${relFile}|${argU.getText(sf)}`;
          if (!ALLOWED_TEMPLATE_IMPORTS.has(key)) {
            out.push({ file: relFile, specifier: argU.getText(sf), reason: "import() template with substitutions cannot be verified" });
          }
        } else {
          const s = literalText(arg);
          if (s !== undefined) checkSpec(s, "import");
        }
      } else if (
        isIdent(callee, "require") ||
        (callee &&
          ts.isPropertyAccessExpression(callee) &&
          isIdent(callee.expression, "vi") &&
          VI_METHODS.has(callee.name.text)) ||
        (callee &&
          ts.isElementAccessExpression(callee) &&
          isIdent(callee.expression, "vi") &&
          VI_METHODS.has(literalText(callee.argumentExpression) ?? ""))
      ) {
        const s = literalText(arg);
        if (s !== undefined) checkSpec(s, "import");
      }
    } else if (
      ts.isNewExpression(node) &&
      isIdent(node.expression, "URL") &&
      node.arguments?.length === 2 &&
      isImportMetaUrl(node.arguments[1])
    ) {
      const s = literalText(node.arguments[0]);
      if (s !== undefined) checkSpec(s, "url");
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

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

const realExists = (abs: string) => existsSync(abs) && (statSync(abs).isFile() || statSync(abs).isDirectory());

describe("relative import specifiers (OpenClaw 2026.11.6 preparation callback)", () => {
  it("finds source files to scan", () => {
    expect(listSources(PLUGIN_ROOT).length).toBeGreaterThan(10);
  });

  it("plugin source has no .js, extensionless, missing or unverifiable relative specifiers", () => {
    const offenders: Violation[] = [];
    for (const file of listSources(PLUGIN_ROOT)) {
      offenders.push(...findViolations(relative(PLUGIN_ROOT, file), file, readFileSync(file, "utf8"), realExists));
    }
    expect(offenders).toEqual([]);
  });
});

describe("guard self-test (fixtures, not real source)", () => {
  const dir = "/fixture/src";
  const present = new Set([`${dir}/x.ts`, `${dir}/data.json`]);
  const run = (code: string, rel = "src/fixture.ts") =>
    findViolations(rel, `${dir}/fixture.ts`, code, (p) => present.has(p));

  it("accepts valid explicit .ts / .json specifiers in every form", () => {
    expect(
      run(`
        import { a } from "./x.ts";
        import type { B } from "./x.ts";
        import d from "./data.json" with { type: "json" };
        export { a } from "./x.ts";
        export * from "./x.ts";
        import {
          c,
        } from "./x.ts";
        const m = await import("./x.ts");
        const n = await import(\`./x.ts\`);
        const t: import("./x.ts").T = 1;
        vi.mock("./x.ts", () => ({}));
        const u = new URL("./data.json", import.meta.url);
        import "pkg";
        import { z } from "minimatch";
      `),
    ).toEqual([]);
  });

  it("fails on ./x.js", () => {
    expect(run(`import { a } from "./x.js";`)).toHaveLength(1);
    expect(run(`export { a } from "./x.js";`)).toHaveLength(1);
    expect(run(`vi.mock("./x.js", () => ({}));`)).toHaveLength(1);
    expect(run(`await import("./x.js");`)).toHaveLength(1);
  });

  it("fails on extensionless ./x", () => {
    expect(run(`import { a } from "./x";`)).toHaveLength(1);
    expect(run(`import {\n  a,\n} from "./x";`)).toHaveLength(1);
    expect(run(`await import(\`./x\`);`)).toHaveLength(1);
  });

  it("fails on a nonexistent ./missing.ts", () => {
    const v = run(`import { a } from "./missing.ts";`);
    expect(v).toHaveLength(1);
    expect(v[0]!.reason).toMatch(/does not exist/);
    expect(run(`await import(\`./missing.ts\`);`)).toHaveLength(1);
  });

  it("fails on a dynamic template import with a substitution", () => {
    const v = run("const n = 'x'; await import(`./${n}.ts`);");
    expect(v).toHaveLength(1);
    expect(v[0]!.reason).toMatch(/substitutions/);
  });

  it("matches wrappers and callees structurally, not by text", () => {
    // URL base: parenthesized, double-parenthesized, and type-wrapped import.meta.url
    expect(run(`new URL("./x.js", (import.meta.url));`)).toHaveLength(1);
    expect(run(`new URL("./x.js", ((import.meta.url)));`)).toHaveLength(1);
    expect(run(`new URL("./x.js", import.meta.url as string);`)).toHaveLength(1);
    expect(run(`new URL("./x.js", (import.meta.url)!);`)).toHaveLength(1);
    expect(run(`new URL("./x.js", import . meta . url);`)).toHaveLength(1);
    expect(run(`new URL("./missing.json", ( import.meta.url ));`)).toHaveLength(1);
    expect(run(`new URL(("./x.js"), import.meta.url);`)).toHaveLength(1);
    expect(run(`new URL("./data.json", (import.meta.url));`)).toEqual([]);
    // a different base is not a module-relative URL and is ignored
    expect(run(`new URL("./x.js", "http://example.test/");`)).toEqual([]);
    // import(): parenthesized argument and callee-free forms
    expect(run(`await import(("./x.js"));`)).toHaveLength(1);
    expect(run(`await import((("./x")));`)).toHaveLength(1);
    expect(run(`await import(("./missing.ts") as string);`)).toHaveLength(1);
    expect(run("await import((`./${n}.ts`));")).toHaveLength(1);
    expect(run(`await import(("./x.ts"));`)).toEqual([]);
    // require / vi.mock: parenthesized callee and argument, element access
    expect(run(`(require)("./x.js");`)).toHaveLength(1);
    expect(run(`require(("./x.js"));`)).toHaveLength(1);
    expect(run(`(vi.mock)(("./x.js"), () => ({}));`)).toHaveLength(1);
    expect(run(`(vi).mock("./x.js", () => ({}));`)).toHaveLength(1);
    expect(run(`vi["mock"]("./x.js", () => ({}));`)).toHaveLength(1);
    expect(run(`vi.doMock(("./missing.ts"), () => ({}));`)).toHaveLength(1);
    expect(run(`vi.mock(("./x.ts"), () => ({}));`)).toEqual([]);
    // unrelated calls are ignored
    expect(run(`foo.mock("./x.js"); other("./x.js");`)).toEqual([]);
  });

  it("fails on a .js URL target, a missing URL target, and an unlisted directory URL", () => {
    expect(run(`new URL("./x.js", import.meta.url);`)).toHaveLength(1);
    expect(run(`new URL("./missing.json", import.meta.url);`)).toHaveLength(1);
    expect(run(`new URL("../", import.meta.url);`)).toHaveLength(1);
  });
});
