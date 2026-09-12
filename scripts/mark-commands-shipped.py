#!/usr/bin/env python3
"""Stamp every `first_shipped_in: "unreleased"` command with a release version.

This is a manual step the release cutter runs **while preparing the version-bump
PR**. It is not invoked by CI and nothing runs it automatically on tag. The
resulting diff is committed as part of that same PR, so the six version-file
changes, the CHANGELOG entry, and this sweep land in one reviewed diff and the
ledger can never claim a command shipped in a release that was never cut.

Usage:

    python scripts/mark-commands-shipped.py 2026.9.12b1

The script rewrites `contracts/command-coverage-manual.json` in place and
regenerates the ledger artifacts. It does not stage anything; the cutter commits
the result alongside the version bumps. Running it twice is harmless: the second
run finds nothing left marked unreleased and exits successfully without writing.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANUAL_PATH = ROOT / "contracts" / "command-coverage-manual.json"
GENERATOR = ROOT / "scripts" / "generate-command-coverage.py"

UNRELEASED = "unreleased"

# Both prerelease markers are accepted. 33 commands first shipped in `2026.6.8a8`,
# an alpha tag, so a beta-only pattern would reject real history. Keep `[ab]`.
VERSION_RE = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}[ab]\d+$")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stamp unreleased commands with a release version.",
        epilog=(
            "Run while preparing the version-bump PR; commit the result with the "
            "version bumps. Not run by CI."
        ),
    )
    parser.add_argument(
        "version",
        help="release version to stamp, e.g. 2026.9.12b1 (no leading 'v')",
    )
    args = parser.parse_args()
    version = args.version

    if version.startswith("v"):
        print(
            f"error: pass the version without a leading 'v' (got {version!r}); "
            f"tags carry the prefix, ledger values do not",
            file=sys.stderr,
        )
        return 2
    if not VERSION_RE.match(version):
        print(
            f"error: {version!r} does not match YYYY.M.D[ab]N, e.g. 2026.9.12b1",
            file=sys.stderr,
        )
        return 2

    if not MANUAL_PATH.is_file():
        print(f"error: {MANUAL_PATH} not found", file=sys.stderr)
        return 2

    original = MANUAL_PATH.read_text(encoding="utf-8")
    data = json.loads(original)

    commands = data.get("commands")
    if not isinstance(commands, dict):
        print("error: manual ledger has no 'commands' object", file=sys.stderr)
        return 2

    stamped = sorted(
        name
        for name, entry in commands.items()
        if isinstance(entry, dict) and entry.get("first_shipped_in") == UNRELEASED
    )

    if not stamped:
        print(f"nothing to do: no command is marked {UNRELEASED!r}")
        return 0

    for name in stamped:
        commands[name]["first_shipped_in"] = version

    # Match the file's existing formatting so the diff stays readable.
    indent = 2 if original.startswith("{\n  ") else 4
    MANUAL_PATH.write_text(
        json.dumps(data, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    result = subprocess.run([sys.executable, str(GENERATOR)], cwd=ROOT)
    if result.returncode != 0:
        print(
            "error: ledger regeneration failed; manual JSON was already rewritten, "
            "so re-run the generator by hand or revert the file",
            file=sys.stderr,
        )
        return result.returncode

    print(f"stamped {len(stamped)} command(s) with first_shipped_in={version!r}:")
    for name in stamped:
        print(f"  {name}")
    print("\nCommit this alongside the version bumps in the release PR.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
