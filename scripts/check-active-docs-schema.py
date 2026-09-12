#!/usr/bin/env python3
"""Reject drift between active docs and the shipped node command surface.

Three gates run per invocation:

1. Retired key: ``gateway.nodes.allowCommands`` is only kept in the
   Gateway as a migration alias; this alpha repo publishes canonical
   ``gateway.nodes.commands.allow`` shape only. Fails when any file
   under ``ACTIVE_PATHS`` still mentions the retired flat key.

2. Advertised inventory tallies: the operator-facing count/inventory
   claims in README and INSTALL must match the live ``_NODE_COMMANDS``
   list in ``app/node/src/openclaw_node/gateway_ws.py``. Fails when the
   total command count or per-namespace tallies (``ha.*``, ``fs.*``,
   ``system.*``, ``ping``) do not match the shipped advertisement.

3. Advertised inventory exact set: the copy-pasteable
   ``gateway.nodes.commands.allow`` example block in ``docs/INSTALL.md``
   must list exactly the commands in ``_NODE_COMMANDS`` — no additions,
   no omissions, no duplicates. A same-count substitution (swap one
   valid name for a stale/nonexistent name) must fail this gate.
"""

# ruff: noqa: TRY003

from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RETIRED_KEY = "allowCommands"

ACTIVE_PATHS: tuple[Path, ...] = (
    ROOT / "README.md",
    ROOT / "docs/INSTALL.md",
    ROOT / "docs/CONTRIBUTING.md",
    ROOT / "docs/operations/UAT-PLAN.md",
    ROOT / "docs/design/COMMAND-TIERS.md",
    ROOT / "plugins/openclaw-hass-node-assist-tools/README.md",
)

GATEWAY_WS = ROOT / "app/node/src/openclaw_node/gateway_ws.py"


def _load_node_commands() -> list[str]:
    tree = ast.parse(GATEWAY_WS.read_text())
    for node in tree.body:
        target_name: str | None = None
        value: ast.AST | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = node.target.id
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                target_name = target.id
                value = node.value
        if target_name == "_NODE_COMMANDS" and isinstance(value, ast.List):
            return [
                elt.value
                for elt in value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
    raise SystemExit("ERROR: _NODE_COMMANDS list not found in gateway_ws.py")


def _tallies(commands: list[str]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for cmd in commands:
        if cmd == "ping":
            counts["ping"] += 1
        else:
            counts[cmd.split(".", 1)[0] + ".*"] += 1
    return {"total": len(commands), **counts}


# Docs that make explicit count/inventory claims. Each entry pairs a
# regex whose named groups must be integers with the tally key they
# reference.
COUNT_CLAIMS: tuple[tuple[Path, str, dict[str, str]], ...] = (
    (
        ROOT / "README.md",
        (
            r"(?P<total>\d+)\s+commands\s+both\s+registered\s+by\s+the\s+"
            r"dispatcher\s+and\s+advertised\s+in\s+the\s+node's\s+connect-frame"
            r".*?`ha\.\*`\s*\S\s*(?P<ha>\d+),\s+"
            r"`fs\.\*`\s*\S\s*(?P<fs>\d+),\s+"
            r"`system\.\*`\s*\S\s*(?P<system>\d+),\s+"
            r"and\s+`ping`"
        ),
        {"total": "total", "ha": "ha.*", "fs": "fs.*", "system": "system.*"},
    ),
    (
        ROOT / "docs/INSTALL.md",
        (
            r"HA node advertises\s+(?P<total>\d+)\s+commands\s+across\s+"
            r"`ha\.\*`,\s+`fs\.\*`,\s*\n?\s*`system\.\*`,\s+and\s+`ping`"
        ),
        {"total": "total"},
    ),
)


INSTALL_MD = ROOT / "docs/INSTALL.md"

_JSON_FENCE = re.compile(r"```json\s*\n(?P<body>.*?)\n```", re.DOTALL)


def _extract_install_inventory() -> list[str]:
    """Return the exact command list ordered as it appears in INSTALL.md.

    Parses the first ```json fenced block in ``docs/INSTALL.md`` that
    contains ``gateway.nodes.commands.allow`` and returns the raw list
    (with any duplicates preserved so this gate can detect them).
    """
    text = INSTALL_MD.read_text()
    for match in _JSON_FENCE.finditer(text):
        body = match.group("body")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            continue
        try:
            allow = payload["gateway"]["nodes"]["commands"]["allow"]
        except (KeyError, TypeError):
            continue
        if isinstance(allow, list) and all(isinstance(c, str) for c in allow):
            return list(allow)
    raise SystemExit(
        "ERROR: docs/INSTALL.md is missing a ```json block containing "
        "gateway.nodes.commands.allow — inventory gate cannot run."
    )


def _check_inventory_set(commands: list[str]) -> list[str]:
    problems: list[str] = []
    listed = _extract_install_inventory()

    counts = Counter(listed)
    duplicates = sorted(name for name, n in counts.items() if n > 1)
    if duplicates:
        problems.append(
            f"  docs/INSTALL.md: duplicate command entries in "
            f"`commands.allow`: {', '.join(duplicates)}"
        )

    listed_set = set(listed)
    shipped_set = set(commands)

    missing = sorted(shipped_set - listed_set)
    if missing:
        problems.append(
            "  docs/INSTALL.md: `commands.allow` is missing commands "
            f"advertised by _NODE_COMMANDS: {', '.join(missing)}"
        )

    extra = sorted(listed_set - shipped_set)
    if extra:
        problems.append(
            "  docs/INSTALL.md: `commands.allow` lists names not "
            f"advertised by _NODE_COMMANDS: {', '.join(extra)}"
        )

    return problems


def _check_retired_key() -> list[str]:
    problems: list[str] = []
    for path in ACTIVE_PATHS:
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if RETIRED_KEY in line:
                rel = path.relative_to(ROOT)
                problems.append(f"  {rel}:{lineno}: {line.rstrip()}")
    return problems


def _check_count_claims(tallies: dict[str, int]) -> list[str]:
    problems: list[str] = []
    for path, pattern, group_to_key in COUNT_CLAIMS:
        text = path.read_text()
        match = re.search(pattern, text, re.DOTALL)
        rel = path.relative_to(ROOT)
        if not match:
            problems.append(
                f"  {rel}: expected count/inventory claim not found; "
                "update this doc together with _NODE_COMMANDS."
            )
            continue
        for group_name, key in group_to_key.items():
            claimed = int(match.group(group_name))
            actual = tallies.get(key, 0)
            if claimed != actual:
                problems.append(f"  {rel}: claims {key}={claimed} but _NODE_COMMANDS has {actual}")
    return problems


def main() -> int:
    """Return 0 when active docs match _NODE_COMMANDS, 1 otherwise."""
    ok = True

    retired = _check_retired_key()
    if retired:
        ok = False
        print(
            f"ERROR: active docs mention retired `{RETIRED_KEY}` key. "
            "Use canonical `gateway.nodes.commands.allow` instead:",
            file=sys.stderr,
        )
        for line in retired:
            print(line, file=sys.stderr)

    commands = _load_node_commands()
    tallies = _tallies(commands)
    count_problems = _check_count_claims(tallies)
    if count_problems:
        ok = False
        print(
            "ERROR: active docs drift from shipped _NODE_COMMANDS "
            f"(total={tallies['total']}, ha.*={tallies.get('ha.*', 0)}, "
            f"fs.*={tallies.get('fs.*', 0)}, "
            f"system.*={tallies.get('system.*', 0)}, "
            f"ping={tallies.get('ping', 0)}):",
            file=sys.stderr,
        )
        for line in count_problems:
            print(line, file=sys.stderr)

    inventory_problems = _check_inventory_set(commands)
    if inventory_problems:
        ok = False
        print(
            "ERROR: INSTALL.md `commands.allow` inventory drift from "
            "_NODE_COMMANDS (a same-count substitution or duplicate would "
            "hit this check):",
            file=sys.stderr,
        )
        for line in inventory_problems:
            print(line, file=sys.stderr)

    if not ok:
        return 1
    print(
        f"OK: {len(ACTIVE_PATHS)} active docs paths free of `{RETIRED_KEY}`, "
        f"count/inventory claims match _NODE_COMMANDS "
        f"(total={tallies['total']}), and INSTALL.md `commands.allow` "
        f"matches _NODE_COMMANDS exactly."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
