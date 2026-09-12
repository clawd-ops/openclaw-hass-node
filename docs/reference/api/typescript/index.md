# TypeScript API Reference

This reference documents the public gateway-plugin entry point and the
supported TypeScript contracts behind `openclaw-hass-node-assist-tools`.
Generated pages include source links and are searchable with the rest of this
site.

## What is documented

The generated reference covers the plugin entry point plus the `src/` modules
that define supported configuration, node-invoke policy, Assist registration,
tool schemas, and invocation contracts. It intentionally excludes individual
tool factories and non-exported helper modules. Operators configure the plugin
through its manifest and per-node policy rather than importing tool factories.

For the operational contract, start with the
[plugin README](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/README.md)
and the [command surface](../../COMMAND-SURFACE.md). The generated
[Modules](generated/README.md) page is the API-level index.

## Regeneration and drift protection

From a clean checkout:

```bash
pnpm install
pnpm docs:typescript
mkdocs build --strict
```

Generated Markdown is committed so the site build does not need to execute
TypeDoc. CI runs `pnpm docs:typescript:check`, which regenerates the reference
and fails when the committed output differs.
