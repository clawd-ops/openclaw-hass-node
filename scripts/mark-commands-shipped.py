#!/usr/bin/env python3
# ruff: noqa: TRY003
"""Stamp unreleased commands while preparing a version-bump PR.

Run this locally after ``scripts/bump-version.py <version>`` and commit the
manual ledger, both generated artifacts, all five version sources, and the
CHANGELOG entry in the same PR. This is release preparation, not tag
automation. CI and the release workflow use ``--check-release-bump`` to verify
the base-to-head command-history transition before a release is cut.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
MANUAL_PATH = ROOT / "contracts" / "command-coverage-manual.json"
GENERATOR = ROOT / "scripts" / "generate-command-coverage.py"
VERSION_SOURCE = "app/node/pyproject.toml"
MANUAL_SOURCE = "contracts/command-coverage-manual.json"

UNRELEASED = "unreleased"
BASELINE_MISSING = "__baseline_missing__"

# Keep this exactly aligned with scripts/bump-version.py::_PEP440_RE. Historical
# command releases include alpha and beta tags; current release tooling also
# supports release candidates, development releases, and final releases.
VERSION_RE = re.compile(r"^\d+(?:\.\d+){2}(?:(?:a|b|rc)\d+|\.dev\d+)?$")
PYPROJECT_VERSION_RE = re.compile(r'^version = "([^"]+)"$', re.MULTILINE)


class ShipError(RuntimeError):
    """Raised when release stamping cannot complete without partial mutation."""


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("command_coverage_generator", GENERATOR)
    if spec is None or spec.loader is None:
        raise ShipError(f"cannot load generator at {GENERATOR}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_manual() -> tuple[bytes, dict[str, object]]:
    if not MANUAL_PATH.is_file():
        raise ShipError(f"{MANUAL_PATH} not found")
    original = MANUAL_PATH.read_bytes()
    try:
        data = json.loads(original.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShipError(f"manual ledger is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ShipError("manual ledger must be a JSON object")
    return original, data


def _read_ref_file(ref: str, source: str) -> bytes:
    """Read *source* from a verified commit ref without accepting git options."""
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if resolved.returncode != 0:
        detail = resolved.stderr.strip()
        suffix = f": {detail}" if detail else ""
        raise ShipError(f"cannot resolve base ref {ref!r}{suffix}")
    commit = resolved.stdout.strip()
    result = subprocess.run(
        ["git", "show", f"{commit}:{source}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise ShipError(detail or f"cannot read {source} at {ref!r}")
    return result.stdout


def _read_manual_at_ref(ref: str, generator: ModuleType) -> dict[str, dict[str, object]]:
    """Load and validate shipment fields from the manual ledger at *ref*."""
    raw = _read_ref_file(ref, MANUAL_SOURCE)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShipError(f"base manual ledger is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("commands"), dict):
        raise ShipError("base manual ledger has no 'commands' object")
    commands: dict[str, dict[str, object]] = {}
    fields_present = 0
    fields_missing = 0
    for name, entry in data["commands"].items():
        if not isinstance(name, str):
            raise ShipError(f"base manual command name must be a string, got {name!r}")
        if not isinstance(entry, dict):
            raise ShipError(f"base manual command entry for {name} must be an object")
        if "first_shipped_in" in entry:
            generator._validate_first_shipped_in(name, entry["first_shipped_in"])
            fields_present += 1
        else:
            entry["first_shipped_in"] = BASELINE_MISSING
            fields_missing += 1
        commands[name] = entry
    if fields_present and fields_missing:
        raise ShipError("base manual ledger has partial first_shipped_in coverage")
    return commands


def _preflight_manual(
    data: dict[str, object], generator: ModuleType
) -> dict[str, dict[str, object]]:
    """Validate every command entry and then the complete generator contract."""
    commands = data.get("commands")
    if not isinstance(commands, dict):
        raise ShipError("manual ledger has no 'commands' object")
    validated: dict[str, dict[str, object]] = {}
    for name, entry in commands.items():
        if not isinstance(name, str):
            raise ShipError(f"manual command name must be a string, got {name!r}")
        if not isinstance(entry, dict):
            raise ShipError(f"manual command entry for {name} must be an object")
        generator._validate_first_shipped_in(name, entry.get("first_shipped_in"))
        validated[name] = entry

    # This validates all remaining manual metadata, exact command/action parity,
    # and every referenced source before any tracked file is touched.
    generator.build_ledger()
    return validated


def _current_version() -> str:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "bump-version.py"), "--get"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ShipError(result.stderr.strip() or "cannot read synchronized current version")
    return result.stdout.strip()


def _require_current_version(version: str) -> None:
    current = _current_version()
    if version != current:
        raise ShipError(
            f"stamp version {version!r} does not match synchronized tracked version {current!r}; "
            "run scripts/bump-version.py first"
        )


def _version_at_ref(ref: str) -> str:
    text = _read_ref_file(ref, VERSION_SOURCE).decode("utf-8")
    match = PYPROJECT_VERSION_RE.search(text)
    if match is None:
        raise ShipError(f"version pattern did not match {VERSION_SOURCE} at {ref}")
    return match.group(1)


def _check_release_bump(base_ref: str) -> int:
    generator = _load_generator()
    _, data = _read_manual()
    commands = _preflight_manual(data, generator)
    base_version = _version_at_ref(base_ref)
    current_version = _current_version()
    base_commands = _read_manual_at_ref(base_ref, generator)
    release_bump = base_version != current_version
    errors: list[str] = []
    baseline_initialization = bool(base_commands) and all(
        entry["first_shipped_in"] == BASELINE_MISSING for entry in base_commands.values()
    )
    if baseline_initialization:
        if release_bump:
            errors.append("cannot initialize shipment history during a release version change")
        missing_or_added = sorted(set(base_commands) ^ set(commands))
        if missing_or_added:
            errors.append(
                "shipment-history initialization changed the command inventory: "
                + ", ".join(missing_or_added)
            )
        if not errors:
            print(
                f"ok: initialized shipment history for {len(commands)} commands without a "
                "release version change"
            )
            return 0
    if release_bump and generator._version_sort_key(current_version) <= generator._version_sort_key(
        base_version
    ):
        errors.append(f"version must advance: base {base_version!r}, head {current_version!r}")
    removed = sorted(set(base_commands) - set(commands))
    if removed:
        errors.append(
            "base command(s) were removed or renamed during the release transition: "
            + ", ".join(removed)
        )
    for name in sorted(set(base_commands) & set(commands)):
        base_value = base_commands[name]["first_shipped_in"]
        head_value = commands[name]["first_shipped_in"]
        expected = current_version if release_bump and base_value == UNRELEASED else base_value
        if head_value != expected:
            errors.append(
                f"{name}: base {base_value!r}, head {head_value!r}, expected {expected!r}"
            )
    for name in sorted(set(commands) - set(base_commands)):
        head_value = commands[name]["first_shipped_in"]
        expected = current_version if release_bump else UNRELEASED
        if head_value != expected:
            errors.append(f"{name}: newly added with {head_value!r}, expected {expected!r}")
    if errors:
        transition = (
            f"release version changed from {base_version} to {current_version}"
            if release_bump
            else f"release version remains {current_version}"
        )
        print(
            f"error: {transition}, "
            "but command shipment history does not match the required transition:",
            file=sys.stderr,
        )
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        if release_bump:
            print(
                f"run scripts/mark-commands-shipped.py {current_version} locally and commit "
                "the source plus generated artifacts in this version-bump PR",
                file=sys.stderr,
            )
        return 1
    if not release_bump:
        print(
            f"ok: no release version bump ({current_version}); released history is unchanged "
            "and pending/new commands remain unreleased"
        )
        return 0
    print(
        f"ok: release version changed from {base_version} to {current_version}; "
        "released history is unchanged and every pending/new command is stamped exactly"
    )
    return 0


def _stamp(version: str) -> int:
    """Rewrite every `unreleased` entry to `version` and regenerate artifacts.

    No transaction, snapshot, or rollback machinery: these are tracked files, so
    if this dies partway the operator runs `git checkout -- .` and retries. git
    already provides the atomicity that custom recovery code would only
    approximate, and that code invented bugs of its own.
    """
    generator = _load_generator()
    raw, data = _read_manual()
    commands = _preflight_manual(data, generator)

    stamped = sorted(
        name for name, entry in commands.items() if entry["first_shipped_in"] == UNRELEASED
    )

    # Regenerate even when nothing needs stamping. A version-only release still
    # moves `latest_release`, so "New in this release" is stale until the
    # artifacts are rebuilt. Returning early here left the ledger describing the
    # previous release while the repo claimed the new one.
    if stamped:
        for name in stamped:
            commands[name]["first_shipped_in"] = version

        indent = 2 if raw.startswith(b"{\n  ") else 4
        MANUAL_PATH.write_text(
            json.dumps(data, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    if subprocess.run([sys.executable, str(GENERATOR)], cwd=ROOT).returncode != 0:
        print(
            "error: ledger regeneration failed; run `git checkout -- .` and retry",
            file=sys.stderr,
        )
        return 1

    if not stamped:
        print(f"no command was marked {UNRELEASED!r}; regenerated artifacts only")
        return 0

    print(f"stamped {len(stamped)} command(s) with first_shipped_in={version!r}:")
    for name in stamped:
        print(f"  {name}")
    print("\nCommit this alongside the version bumps in the release PR.")
    return 0


def main() -> int:
    """Parse arguments and run either the stamp or the release-bump gate."""
    parser = argparse.ArgumentParser(
        description="Stamp unreleased commands with a release version.",
        epilog=(
            "Run while preparing the version-bump PR; commit the result with the "
            "version bumps. Not run by CI."
        ),
    )
    parser.add_argument("version", nargs="?", help="release version, e.g. 2026.9.12b1")
    parser.add_argument(
        "--check-release-bump",
        metavar="BASE_SHA",
        help="verify the shipment-history transition against a base ref (CI gate)",
    )
    args = parser.parse_args()

    if args.check_release_bump:
        if args.version:
            print("error: --check-release-bump takes no version argument", file=sys.stderr)
            return 2
        # Check mode needs the same ShipError contract as stamping. Without it a
        # malformed or unreadable base ref exits on a raw traceback instead of
        # the documented `error:` line, and this runs as a CI gate where the
        # diagnostic is the whole output.
        try:
            return _check_release_bump(args.check_release_bump)
        except ShipError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    if not args.version:
        parser.error("a version is required unless --check-release-bump is used")

    version = args.version
    if version.startswith("v"):
        print(
            f"error: pass the version without a leading 'v' (got {version!r})",
            file=sys.stderr,
        )
        return 2
    if not VERSION_RE.fullmatch(version):
        print(f"error: {version!r} is not a valid version string", file=sys.stderr)
        return 2

    try:
        _require_current_version(version)
    except ShipError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        return _stamp(version)
    except ShipError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
