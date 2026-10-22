#!/usr/bin/env python3
"""Bump the project version across all files that carry it.

The project version literal lives in seven files because the same code
ships through three ecosystems (Python package, HA Supervisor add-on,
HACS custom integration). Hand-editing seven files is how versions drift
(see PR #148/#149: app/config.yaml was bumped to b6 while the other
four stayed at b5). This script is the one command that does all seven.

Version format is ``YYYY.M.D`` (stable) or ``YYYY.M.D-{alpha,beta,rc}.N``
(pre-release), with no leading zeros. The two Python-side sources
(``pyproject.toml`` and the ``__init__.py`` fallback) store the PEP 440
normalized form (``2026.10.22b1``) because that is what packaging tools and
``importlib.metadata`` report; every other source carries the dash form
verbatim. The old ``YYYY.M.DbN`` form is still *readable* so the transition
from an old release passes, but it can no longer be written.

Usage:

    scripts/bump-version.py 2026.10.22-beta.2
    scripts/bump-version.py --check 2026.10.22-beta.1   # exit non-zero on drift

Run from the repo root. Does NOT create commits, tags, or releases —
intentionally narrow. The release procedure in `docs/RELEASE.md` wraps
this with the `git commit` / `git tag` / `gh release create` steps.
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_NUM = r"(?:0|[1-9]\d*)"
# The writable format. Keep this exactly aligned with
# scripts/generate-command-coverage.py::_FIRST_SHIPPED_VERSION_RE.
_VERSION_RE = re.compile(rf"^({_NUM}(?:\.{_NUM}){{2}})(?:-(alpha|beta|rc)\.([1-9]\d*))?$")
# The old ``YYYY.M.DbN`` form: readable as a previous version, never written.
_LEGACY_RE = re.compile(r"^(\d+(?:\.\d+){2})(?:(a|b|rc)(\d+)|\.dev\d+)?$")
_PEP440_STAGE = {"alpha": "a", "beta": "b", "rc": "rc"}
_STAGE_NAME = {"a": "alpha", "b": "beta", "rc": "rc"}
_PRERELEASE_CAP = 9


def normalize_version(version: str) -> str:
    """Return the PEP 440 normalized form of a dash-form or legacy *version*.

    ``2026.10.22-beta.1`` -> ``2026.10.22b1``. Stable and legacy versions are
    already normalized and come back unchanged.

    Raises:
        ValueError: if *version* is in neither supported format.
    """
    match = _VERSION_RE.fullmatch(version)
    if match:
        base, stage, serial = match.groups()
        return base if stage is None else f"{base}{_PEP440_STAGE[stage]}{serial}"
    if _LEGACY_RE.fullmatch(version):
        return version
    raise ValueError(f"unsupported version literal: {version!r}")  # noqa: TRY003


@dataclass(frozen=True)
class VersionFile:
    """One file that carries the project version literal."""

    path: Path
    pattern: re.Pattern[str]
    template: str
    # True when the file carries the PEP 440 normalized form (what Python
    # tooling reports) instead of the dash form verbatim.
    pep440: bool = False


# Order matters only for readable output — sources are independent.
SOURCES: list[VersionFile] = [
    VersionFile(
        path=REPO_ROOT / "app" / "config.yaml",
        pattern=re.compile(r'^version: "([^"]+)"$', re.MULTILINE),
        template='version: "{version}"',
    ),
    VersionFile(
        path=REPO_ROOT / "app" / "build.yaml",
        pattern=re.compile(r'^  io\.hass\.version: "([^"]+)"$', re.MULTILINE),
        template='  io.hass.version: "{version}"',
    ),
    VersionFile(
        path=REPO_ROOT / "app" / "node" / "pyproject.toml",
        pattern=re.compile(r'^version = "([^"]+)"$', re.MULTILINE),
        template='version = "{version}"',
        pep440=True,
    ),
    VersionFile(
        path=REPO_ROOT / "app" / "node" / "src" / "openclaw_node" / "__init__.py",
        pattern=re.compile(r'^    __version__ = "([^"]+)"$', re.MULTILINE),
        template='    __version__ = "{version}"',
        pep440=True,
    ),
    VersionFile(
        path=REPO_ROOT / "custom_components" / "openclaw_hass_node_assist" / "manifest.json",
        pattern=re.compile(r'^  "version": "([^"]+)"$', re.MULTILINE),
        template='  "version": "{version}"',
    ),
    VersionFile(
        path=REPO_ROOT / "plugins" / "openclaw-hass-node-assist-tools" / "package.json",
        pattern=re.compile(r'^  "version": "([^"]+)",$', re.MULTILINE),
        template='  "version": "{version}",',
    ),
    VersionFile(
        path=REPO_ROOT / "skills" / "openclaw-hass-node-skill" / "SKILL.md",
        pattern=re.compile(r"^version: (.+)$", re.MULTILINE),
        template="version: {version}",
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
    literal = normalize_version(new_version) if source.pep440 else new_version
    if _read_current(source) == literal:
        return False
    new_line = source.template.format(version=literal)
    new_text = source.pattern.sub(new_line.replace("\\", "\\\\"), text, count=1)
    source.path.write_text(new_text, encoding="utf-8")
    return True


def _synchronized_version() -> str | None:
    """Return the version every source agrees on, or None after reporting drift.

    The canonical literal is the dash form the verbatim sources carry; the
    PEP 440 sources must hold its normalized form. Comparing normalized forms
    is what stops ``2026.10.22b1`` in pyproject.toml from reading as drift
    against ``2026.10.22-beta.1`` in app/config.yaml.

    `--check` and `--get` both need this, and both used to compute and report it
    themselves. Two copies of the same reconciliation in release tooling can
    drift apart — a new entry in SOURCES reaching only one of them would make
    the two subcommands disagree about what the tracked version is.
    """
    versions = {s.path.relative_to(REPO_ROOT): _read_current(s) for s in SOURCES}
    verbatim = {v for s, v in zip(SOURCES, versions.values(), strict=True) if not s.pep440}
    canonical = next(iter(verbatim)) if len(verbatim) == 1 else None
    try:
        expected = None if canonical is None else normalize_version(canonical)
    except ValueError:
        expected = None
    drift = expected is None or any(
        v != (expected if s.pep440 else canonical)
        for s, v in zip(SOURCES, versions.values(), strict=True)
    )
    if drift:
        print("error: version drift across sources:", file=sys.stderr)
        for path, ver in versions.items():
            print(f"  {path}: {ver}", file=sys.stderr)
        return None
    return canonical


def _check(version: str | None) -> int:
    """Verify all seven sources carry the same version (and optionally match *version*).

    Returns 0 on success, non-zero on drift.
    """
    current = _synchronized_version()
    if current is None:
        return 1
    if version is not None and current != version:
        print(f"error: sources agree on {current!r} but expected {version!r}", file=sys.stderr)
        return 1
    print(f"ok: all {len(SOURCES)} sources on {current}")
    return 0


def writable_version_error(version: str) -> str | None:
    """Return why *version* cannot be written as a new version, or None if it can.

    The single definition of the writable format, shared by ``_bump`` and by
    ``mark-commands-shipped.py --check-release-bump`` (which applies it to a
    changed head). It covers the shape (stable ``YYYY.M.D`` or
    ``-{alpha,beta,rc}.N``, no leading zeros), a real calendar date, and the
    pre-release cap. The old ``bN`` form is readable elsewhere but never valid here.
    """
    match = _VERSION_RE.fullmatch(version)
    if match is None:
        hint = ""
        legacy = _LEGACY_RE.fullmatch(version)
        if legacy and legacy.group(2):
            stage = _STAGE_NAME[legacy.group(2)]
            hint = (
                f"; the old form is no longer written, use "
                f"{legacy.group(1)}-{stage}.{legacy.group(3)}"
            )
        return (
            f"{version!r} is not a valid version; expected YYYY.M.D-beta.N "
            f"(also -alpha.N, -rc.N) or a stable YYYY.M.D, no leading zeros, "
            f"e.g. 2026.10.22-beta.1 or 2026.10.22{hint}"
        )
    base, stage, serial = match.groups()
    year, month, day = (int(part) for part in base.split("."))
    try:
        datetime.date(year, month, day)
    except ValueError:
        return f"{version!r} is not a valid version: {base} is not a real calendar date"
    if stage and int(serial) > _PRERELEASE_CAP:
        marker = f"{stage}.{_PRERELEASE_CAP}"
        return (
            f"{version!r} exceeds the {marker} prerelease cap. Roll the "
            f"calendar portion forward to today and reset to {stage}.1 instead "
            f"(e.g. 2026.7.1-{stage}.1). See feedback_beta_cap_b9 in user memory."
        )
    return None


def _bump(new_version: str, sources: Iterable[VersionFile]) -> int:
    """Rewrite each source to *new_version*. Returns 0 if any change applied."""
    problem = writable_version_error(new_version)
    if problem is not None:
        raise SystemExit(f"error: {problem}")  # noqa: TRY003
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
        help="The new version literal (e.g. 2026.10.22-beta.2). With --check, the "
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
        current = _synchronized_version()
        if current is None:
            return 1
        print(current)
        return 0
    if args.check:
        return _check(args.version)
    if not args.version:
        parser.error("version is required unless --check or --get is passed")
    return _bump(args.version, SOURCES)


if __name__ == "__main__":
    sys.exit(main())
