#!/usr/bin/env bash
# Mark all unreleased commands as shipped in the given release version.
# Run this BEFORE the version-bump PR when cutting a release.
set -euo pipefail

VERSION_RE='^[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2}[ab][0-9]+$'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MANUAL="${REPO_ROOT}/contracts/command-coverage-manual.json"

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <version>  (e.g. 2026.9.12b1)" >&2
    exit 1
fi

VERSION="$1"

if ! [[ "${VERSION}" =~ ${VERSION_RE} ]]; then
    echo "error: version '${VERSION}' does not match required pattern YYYY.M.D[ab]N" >&2
    echo "       example: 2026.9.12b1" >&2
    exit 1
fi

# Require that the repo is in a clean enough state to stage files safely.
# Uncommitted modifications to files we will touch would be lost.
if ! git -C "${REPO_ROOT}" diff --quiet -- "${MANUAL}" \
   || ! git -C "${REPO_ROOT}" diff --cached --quiet -- "${MANUAL}"; then
    echo "error: ${MANUAL} has uncommitted modifications; commit or stash first" >&2
    exit 1
fi

# Rewrite "unreleased" → VERSION for every matching first_shipped_in value.
python3 - <<PYEOF
import json, sys

path = "${MANUAL}"
with open(path, encoding="utf-8") as f:
    data = json.load(f)

changed = 0
for cmd, entry in data["commands"].items():
    if entry.get("first_shipped_in") == "unreleased":
        entry["first_shipped_in"] = "${VERSION}"
        changed += 1

if changed == 0:
    print("no unreleased commands found; nothing to do")
    sys.exit(0)

with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")

print(f"stamped {changed} command(s) with first_shipped_in={repr('${VERSION}')}")
PYEOF

# Regenerate artifacts so they reflect the new values.
cd "${REPO_ROOT}"
python3 scripts/generate-command-coverage.py

# Stage changed files.
git add \
    contracts/command-coverage-manual.json \
    docs/reference/command-coverage.json \
    docs/reference/COMMAND-COVERAGE.md

echo "done — staged changes are ready to commit alongside the version-bump PR"
