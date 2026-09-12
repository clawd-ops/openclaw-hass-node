# TypeScript API Reference (Deferred)

TypeScript API documentation for `plugins/openclaw-hass-node-assist-tools` is
a planned follow-up tracked by
[#279](https://github.com/clawd-ops/openclaw-hass-node/issues/279).

## Why it is deferred

TypeDoc is not currently configured in the plugin's `package.json`. The plugin
uses `typescript` and `vitest` as dev dependencies but has no `typedoc` or
`typedoc-plugin-markdown` dependency, and no TypeDoc output directory or
`typedoc.json` config is present in the repo. Adding TypeDoc and wiring its
markdown output into MkDocs requires:

1. Adding `typedoc` and `typedoc-plugin-markdown` to the plugin's
   `devDependencies`.
2. Adding a `typedoc.json` (or `typedoc` key in `package.json`) that targets
   `src/` and outputs markdown to `docs/reference/api/typescript/`.
3. Adding a CI step (or pre-commit hook) to regenerate the output on change.
4. Wiring the generated pages into `mkdocs.yml` nav.

That work is a build-system change, not only a documentation content change,
and belongs in its own PR. Issue #279 defines the generation, navigation, and
CI drift-check acceptance criteria.

## What the plugin does

The `@openclaw-hass-node/assist-tools` plugin bridges HA Assist sessions to
the node command surface. Key source files:

| File | Purpose |
|------|---------|
| `index.ts` | Plugin entry point |
| `src/tools/` | Per-command tool registrations |
| `src/shared/` | Policy evaluation helpers |
| `types/` | Shared TypeScript type declarations |

Until TypeDoc is wired up, the source files in
`plugins/openclaw-hass-node-assist-tools/src/` are the authoritative reference.
