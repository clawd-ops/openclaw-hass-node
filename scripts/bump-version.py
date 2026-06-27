#!/usr/bin/env python3
"""Bump the project version across all files that carry it.

The project version literal lives in five files because the same code
ships through three ecosystems (Python package, HA Supervisor add-on,
HACS custom integration). Hand-editing five files is how versions drift
(see PR #148/#149: addon/config.yaml was bumped to b6 while the other
four stayed at b5). This script is the one command that does all five.

Usage:

    scripts/bump-version.py 2026.6.20b7
    scripts/bump-version.py --check 2026.6.20b6   # exit non-zero on drift

Run from the repo root. Does NOT create commits, tags, or releases —
intentionally narrow. The release procedure in `docs/RELEASE.md` wraps
this with the `git commit` / `git tag` / `gh release create` steps.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_PEP440_RE = re.compile(r"^\d+(?:\.\d+){2}(?:(?:a|b|rc)\d+|\.dev\d+)?$")


@dataclass(frozen=True)
class VersionFile:
    """One file that carries the project version literal."""

    path: Path
    pattern: re.Pattern[str]
    template: str


# Order matters only for readable output — sources are independent.
SOURCES: list[VersionFile] = [
    VersionFile(
        path=REPO_ROOT / "openclaw_hass_node" / "config.yaml",
        pattern=re.compile(r'^version: "([^"]+)"$', re.MULTILINE),
        template='version: "{version}"',
    ),
    VersionFile(
        path=REPO_ROOT / "openclaw_hass_node" / "build.yaml",
        pattern=re.compile(r'^  io\.hass\.version: "([^"]+)"$', re.MULTILINE),
        template='  io.hass.version: "{version}"',
    ),
    VersionFile(
        path=REPO_ROOT / "openclaw_hass_node" / "node" / "pyproject.toml",
        pattern=re.compile(r'^version = "([^"]+)"$', re.MULTILINE),
        template='version = "{version}"',
    ),
    VersionFile(
        path=REPO_ROOT / "openclaw_hass_node" / "node" / "src" / "openclaw_node" / "__init__.py",
        pattern=re.compile(r'^    __version__ = "([^"]+)"$', re.MULTILINE),
        template='    __version__ = "{version}"',
    ),
    VersionFile(
        path=REPO_ROOT / "custom_components" / "openclaw_gateway" / "manifest.json",
        pattern=re.compile(r'^  "version": "([^"]+)"$', re.MULTILINE),
        template='  "version": "{version}"',
    ),
]


def _read_current(source: VersionFile) -> str:
    """Return the version literal currently in *source*.

    Raises:
        SystemExit: if the pattern doesn't match — the regex needs to
            be updated, not silently ignored.
    """
    text = source.path.read_text(encoding="utf-8")
    match = source.pattern.search(text)
    if match is None:
        raise SystemExit(  # noqa: TRY003
            f"error: version pattern did not match in {source.path.relative_to(REPO_ROOT)}; "
            f"update the regex in scripts/bump-version.py"
        )
    return match.group(1)


def _write_new(source: VersionFile, new_version: str) -> bool:
    """Rewrite *source* to carry *new_version*. Return True if changed."""
    text = source.path.read_text(encoding="utf-8")
    current = _read_current(source)
    if current == new_version:
        return False
    new_line = source.template.format(version=new_version)
    new_text = source.pattern.sub(new_line.replace("\\", "\\\\"), text, count=1)
    source.path.write_text(new_text, encoding="utf-8")
    return True


def _check(version: str | None) -> int:
    """Verify all five sources carry the same version (and optionally match *version*).

    Returns 0 on success, non-zero on drift.
    """
    versions = {s.path.relative_to(REPO_ROOT): _read_current(s) for s in SOURCES}
    distinct = set(versions.values())
    if len(distinct) != 1:
        print("error: version drift across sources:", file=sys.stderr)
        for path, ver in versions.items():
            print(f"  {path}: {ver}", file=sys.stderr)
        return 1
    current = next(iter(distinct))
    if version is not None and current != version:
        print(f"error: sources agree on {current!r} but expected {version!r}", file=sys.stderr)
        return 1
    print(f"ok: all {len(SOURCES)} sources on {current}")
    return 0


def _bump(new_version: str, sources: Iterable[VersionFile]) -> int:
    """Rewrite each source to *new_version*. Returns 0 if any change applied."""
    if not _PEP440_RE.match(new_version):
        raise SystemExit(  # noqa: TRY003
            f"error: {new_version!r} does not look like a PEP 440 version "
            f"(expected e.g. 2026.6.20b7 or 2026.7.0)"
        )
    changed: list[Path] = []
    for source in sources:
        if _write_new(source, new_version):
            changed.append(source.path.relative_to(REPO_ROOT))
    if not changed:
        print(f"ok: all sources already on {new_version}; no changes")
        return 0
    print(f"bumped {len(changed)} file(s) to {new_version}:")
    for path in changed:
        print(f"  {path}")
    return 0


def main() -> int:
    """CLI entry point — see module docstring."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "version",
        help="The new version literal (e.g. 2026.6.20b7). With --check, the "
        "expected version (omit to just verify the sources agree).",
        nargs="?",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Don't write; verify all sources carry the same version.",
    )
    parser.add_argument(
        "--get",
        action="store_true",
        help="Print the current synced version and exit (or fail on drift).",
    )
    args = parser.parse_args()
    if args.get:
        # Print the bare version to stdout for shell capture
        # (`VERSION=$(scripts/bump-version.py --get)`). Drift exits non-zero
        # with the diff on stderr — no stdout output in that case.
        versions = {s.path.relative_to(REPO_ROOT): _read_current(s) for s in SOURCES}
        distinct = set(versions.values())
        if len(distinct) != 1:
            print("error: version drift across sources:", file=sys.stderr)
            for path, ver in versions.items():
                print(f"  {path}: {ver}", file=sys.stderr)
            return 1
        print(next(iter(distinct)))
        return 0
    if args.check:
        return _check(args.version)
    if not args.version:
        parser.error("version is required unless --check or --get is passed")
    return _bump(args.version, SOURCES)


if __name__ == "__main__":
    sys.exit(main())
