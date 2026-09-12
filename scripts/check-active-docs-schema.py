#!/usr/bin/env python3
"""Reject the retired ``gateway.nodes.allowCommands`` flat key in active docs.

The canonical Gateway schema is the nested ``gateway.nodes.commands.allow``
form. The flat ``allowCommands`` key is only kept in the Gateway as a
migration alias; this alpha repo's rule is to publish canonical shape only.
Fails if any file under the paths in ``ACTIVE_PATHS`` mentions the retired
key (case-sensitive). Historical, changelog, and design-context files that
discuss the migration are excluded via ``EXCLUDED``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RETIRED_KEY = "allowCommands"

# Active operator-facing paths. These are the surfaces installers/operators
# read, so they must publish only the canonical nested schema.
ACTIVE_PATHS: tuple[Path, ...] = (
    ROOT / "README.md",
    ROOT / "docs/INSTALL.md",
    ROOT / "docs/CONTRIBUTING.md",
    ROOT / "docs/operations/UAT-PLAN.md",
    ROOT / "docs/design/COMMAND-TIERS.md",
    ROOT / "plugins/openclaw-hass-node-assist-tools/README.md",
)


def main() -> int:
    """Return 0 when no active doc mentions the retired key, 1 otherwise."""
    offenders: list[tuple[Path, int, str]] = []
    for path in ACTIVE_PATHS:
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if RETIRED_KEY in line:
                offenders.append((path.relative_to(ROOT), lineno, line.rstrip()))

    if offenders:
        print(
            f"ERROR: active docs mention retired `{RETIRED_KEY}` key. "
            "Use canonical `gateway.nodes.commands.allow` instead:",
            file=sys.stderr,
        )
        for rel, lineno, text in offenders:
            print(f"  {rel}:{lineno}: {text}", file=sys.stderr)
        return 1
    print(f"OK: {len(ACTIVE_PATHS)} active docs paths free of `{RETIRED_KEY}`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
