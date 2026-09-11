# Local Documentation Preview

The documentation site is built with [MkDocs](https://www.mkdocs.org/) and the
[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) theme.
This is a **private preview only** — no public deployment is configured.

## Install dependencies

```bash
pip install -r docs-requirements.txt
```

This installs MkDocs, Material for MkDocs, and mkdocstrings (Python handler)
as pinned in `docs-requirements.txt`.

## Run the local preview server

```bash
mkdocs serve
```

By default the server binds to `localhost:8000`. Open that address in a
browser to preview the site with live-reload on file changes.

## Build a static site

To do a one-off build and check for errors:

```bash
mkdocs build --strict
```

Output goes to `site/` (excluded from version control via `.gitignore`).
Strict mode treats warnings as errors, matching the CI gate.

## Private access from the OpenClaw Control UI

The OpenClaw portal tool can expose `localhost:8000` inside the Control UI for
private access without a public tunnel. No external port or hostname is
required. See the OpenClaw documentation for the portal tool configuration.

## What is not included

TypeScript API reference for `plugins/openclaw-hass-node-assist-tools` is
deferred — see [`reference/api/typescript-deferred.md`](../reference/api/typescript-deferred.md)
for details and the follow-up plan.
