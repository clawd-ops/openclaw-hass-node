#!/usr/bin/env python3
"""Stamp unreleased commands while preparing an atomic version-bump PR.

Run this locally after ``scripts/bump-version.py <version>`` and commit the
manual ledger, both generated artifacts, all five version sources, and the
CHANGELOG entry in the same PR. This is release preparation, not tag
automation. CI uses ``--check-release-bump`` to reject a version-bump PR that
still has current commands marked ``unreleased``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
MANUAL_PATH = ROOT / "contracts" / "command-coverage-manual.json"
GENERATOR = ROOT / "scripts" / "generate-command-coverage.py"
VERSION_SOURCE = "app/node/pyproject.toml"

UNRELEASED = "unreleased"

# Both prerelease markers are accepted. 28 commands first shipped in `2026.6.8a8`,
# an alpha tag, so a beta-only pattern would reject real history. Keep `[ab]`.
VERSION_RE = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}[ab]\d+$")
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


def _candidate_outputs(version: str) -> tuple[dict[Path, bytes], list[str]]:
    """Build all replacement bytes without mutating any tracked path."""
    original, data = _read_manual()
    generator = _load_generator()
    commands = _preflight_manual(data, generator)
    stamped = sorted(
        name for name, entry in commands.items() if entry["first_shipped_in"] == UNRELEASED
    )
    for name in stamped:
        commands[name]["first_shipped_in"] = version
    indent = 2 if original.startswith(b"{\n  ") else 4
    candidate_manual = (json.dumps(data, indent=indent, ensure_ascii=False) + "\n").encode()

    temporary_path: Path | None = None
    original_generator_manual = generator.MANUAL
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".command-coverage-manual.", suffix=".json", delete=False
        ) as temporary:
            temporary.write(candidate_manual)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        generator.MANUAL = temporary_path
        generated = generator._serialized_outputs()
    finally:
        generator.MANUAL = original_generator_manual
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    candidates = {MANUAL_PATH: candidate_manual}
    candidates.update({path: content.encode() for path, content in generated.items()})
    replacements = {
        path: content
        for path, content in candidates.items()
        if not path.exists() or path.read_bytes() != content
    }
    return replacements, stamped


def _write_temporary_sibling(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=f".{path.name}.", dir=path.parent, delete=False
    ) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        return Path(temporary.name)


def _transactional_replace(
    replacements: dict[Path, bytes],
    *,
    replace: Callable[[Path, Path], None] = os.replace,
) -> None:
    """Replace every target, rolling back byte-for-byte on any failure."""
    originals = {path: path.read_bytes() if path.exists() else None for path in replacements}
    candidates = {
        path: _write_temporary_sibling(path, content) for path, content in replacements.items()
    }
    replaced: list[Path] = []
    try:
        for path, temporary in candidates.items():
            replace(temporary, path)
            replaced.append(path)
    except OSError as exc:
        rollback_errors: list[str] = []
        for path in reversed(replaced):
            original = originals[path]
            try:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    rollback = _write_temporary_sibling(path, original)
                    os.replace(rollback, path)
            except OSError as rollback_exc:
                rollback_errors.append(f"{path}: {rollback_exc}")
        if rollback_errors:
            raise ShipError(
                f"replacement failed ({exc}); rollback also failed: {'; '.join(rollback_errors)}"
            ) from exc
        raise ShipError(f"replacement failed; all tracked files restored: {exc}") from exc
    finally:
        for temporary in candidates.values():
            temporary.unlink(missing_ok=True)


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
    result = subprocess.run(
        ["git", "show", f"{ref}:{VERSION_SOURCE}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ShipError(result.stderr.strip() or f"cannot read version at {ref}")
    match = PYPROJECT_VERSION_RE.search(result.stdout)
    if match is None:
        raise ShipError(f"version pattern did not match {VERSION_SOURCE} at {ref}")
    return match.group(1)


def _check_release_bump(base_ref: str) -> int:
    generator = _load_generator()
    _, data = _read_manual()
    commands = _preflight_manual(data, generator)
    base_version = _version_at_ref(base_ref)
    current_version = _current_version()
    if base_version == current_version:
        print(f"ok: no release version bump ({current_version})")
        return 0
    unreleased = sorted(
        name for name, entry in commands.items() if entry["first_shipped_in"] == UNRELEASED
    )
    if unreleased:
        print(
            f"error: release version changed from {base_version} to {current_version}, "
            f"but {len(unreleased)} current command(s) remain {UNRELEASED!r}:",
            file=sys.stderr,
        )
        for name in unreleased:
            print(f"  {name}", file=sys.stderr)
        print(
            f"run scripts/mark-commands-shipped.py {current_version} locally and commit "
            "the source plus generated artifacts in this version-bump PR",
            file=sys.stderr,
        )
        return 1
    print(
        f"ok: release version changed from {base_version} to {current_version}; "
        "no current command remains unreleased"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "version",
        nargs="?",
        help="release version to stamp, e.g. 2026.9.12b1 (no leading 'v')",
    )
    parser.add_argument(
        "--check-release-bump",
        metavar="BASE_REF",
        help="fail if the version differs from BASE_REF while a current command is unreleased",
    )
    args = parser.parse_args()
    if args.check_release_bump:
        if args.version:
            parser.error("version cannot be combined with --check-release-bump")
        try:
            return _check_release_bump(args.check_release_bump)
        except (OSError, ShipError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if not args.version:
        parser.error("version is required unless --check-release-bump is used")
    version = args.version
    if version.startswith("v"):
        print(
            f"error: pass the version without a leading 'v' (got {version!r}); "
            "tags carry the prefix, ledger values do not",
            file=sys.stderr,
        )
        return 2
    if not VERSION_RE.fullmatch(version):
        print(
            f"error: {version!r} does not match YYYY.M.D[ab]N, e.g. 2026.9.12b1",
            file=sys.stderr,
        )
        return 2

    try:
        _require_current_version(version)
        replacements, stamped = _candidate_outputs(version)
        if not replacements:
            print(f"nothing to do: no command is marked {UNRELEASED!r}")
            return 0
        _transactional_replace(replacements)
    except (OSError, ShipError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if stamped:
        print(f"stamped {len(stamped)} command(s) with first_shipped_in={version!r}:")
        for name in stamped:
            print(f"  {name}")
    else:
        print(f"updated generated release summary for {version}; no commands needed stamping")
    print("\nCommit this alongside all version sources and the CHANGELOG in the release PR.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
