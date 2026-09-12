#!/usr/bin/env bash
# Regenerate the TypeScript API reference, then fail if its committed output
# differs. Checking untracked files as well as `git diff` catches newly added
# generated pages, which `git diff --exit-code` alone would miss.
set -euo pipefail

pnpm docs:typescript

generated_dir="docs/reference/api/typescript/generated"
if ! git diff --exit-code -- "$generated_dir"; then
  echo "TypeScript API reference is stale. Run: pnpm docs:typescript" >&2
  exit 1
fi

if [[ -n "$(git ls-files --others --exclude-standard -- "$generated_dir")" ]]; then
  echo "TypeScript API reference has untracked generated files. Run: pnpm docs:typescript and commit the output." >&2
  exit 1
fi
