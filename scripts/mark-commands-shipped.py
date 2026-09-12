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
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
MANUAL_PATH = ROOT / "contracts" / "command-coverage-manual.json"
GENERATOR = ROOT / "scripts" / "generate-command-coverage.py"
VERSION_SOURCE = "app/node/pyproject.toml"
MANUAL_SOURCE = "contracts/command-coverage-manual.json"

UNRELEASED = "unreleased"

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
    for name, entry in data["commands"].items():
        if not isinstance(name, str):
            raise ShipError(f"base manual command name must be a string, got {name!r}")
        if not isinstance(entry, dict):
            raise ShipError(f"base manual command entry for {name} must be an object")
        generator._validate_first_shipped_in(name, entry.get("first_shipped_in"))
        commands[name] = entry
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


def _write_temporary_sibling(
    path: Path,
    content: bytes,
    *,
    mode: int | None = None,
    times_ns: tuple[int, int] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", dir=path.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            if mode is not None:
                os.fchmod(temporary.fileno(), mode)
            os.fsync(temporary.fileno())
        if times_ns is not None:
            os.utime(temporary_path, ns=times_ns)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def _transactional_replace(
    replacements: dict[Path, bytes],
    *,
    replace: Callable[[Path, Path], None] = os.replace,
    rollback_replace: Callable[[Path, Path], None] = os.replace,
) -> None:
    """Best-effort multi-file replacement with fail-closed preparation and rollback."""
    originals = {
        path: (path.read_bytes(), path.stat()) if path.exists() else None for path in replacements
    }
    candidates: dict[Path, Path] = {}
    attempted: list[Path] = []
    try:
        # Finish every fallible candidate write before changing any target.
        for path, content in replacements.items():
            snapshot = originals[path]
            mode = None if snapshot is None else snapshot[1].st_mode & 0o7777
            candidates[path] = _write_temporary_sibling(path, content, mode=mode)
        for path, temporary in candidates.items():
            # Record before the call: an interrupt can arrive immediately after the
            # filesystem replacement but before Python executes the next statement.
            attempted.append(path)
            replace(temporary, path)
    except BaseException as exc:
        rollback_errors: list[str] = []
        for path in reversed(attempted):
            snapshot = originals[path]
            rollback: Path | None = None
            try:
                if snapshot is None:
                    path.unlink(missing_ok=True)
                else:
                    original, metadata = snapshot
                    rollback = _write_temporary_sibling(
                        path,
                        original,
                        mode=metadata.st_mode & 0o7777,
                        times_ns=(metadata.st_atime_ns, metadata.st_mtime_ns),
                    )
                    rollback_replace(rollback, path)
            except BaseException as rollback_exc:
                rollback_errors.append(f"{path}: {rollback_exc}")
            finally:
                if rollback is not None:
                    rollback.unlink(missing_ok=True)
        if rollback_errors:
            raise ShipError(
                f"replacement failed or was interrupted ({exc}); incomplete recovery: "
                f"could not restore {'; '.join(rollback_errors)}; inspect the listed targets "
                "before retrying"
            ) from exc
        if not isinstance(exc, Exception):
            raise
        if not attempted:
            raise ShipError(f"candidate preparation failed; no targets changed: {exc}") from exc
        raise ShipError(f"replacement failed; all attempted targets restored: {exc}") from exc
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
    if base_version == current_version:
        print(f"ok: no release version bump ({current_version})")
        return 0
    base_commands = _read_manual_at_ref(base_ref, generator)
    errors: list[str] = []
    removed = sorted(set(base_commands) - set(commands))
    if removed:
        errors.append(
            "base command(s) were removed or renamed during the release transition: "
            + ", ".join(removed)
        )
    for name in sorted(set(base_commands) & set(commands)):
        base_value = base_commands[name]["first_shipped_in"]
        head_value = commands[name]["first_shipped_in"]
        expected = current_version if base_value == UNRELEASED else base_value
        if head_value != expected:
            errors.append(
                f"{name}: base {base_value!r}, head {head_value!r}, expected {expected!r}"
            )
    for name in sorted(set(commands) - set(base_commands)):
        head_value = commands[name]["first_shipped_in"]
        if head_value != current_version:
            errors.append(f"{name}: newly added with {head_value!r}, expected {current_version!r}")
    if errors:
        print(
            f"error: release version changed from {base_version} to {current_version}, "
            "but command shipment history does not match the required transition:",
            file=sys.stderr,
        )
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        print(
            f"run scripts/mark-commands-shipped.py {current_version} locally and commit "
            "the source plus generated artifacts in this version-bump PR",
            file=sys.stderr,
        )
        return 1
    print(
        f"ok: release version changed from {base_version} to {current_version}; "
        "released history is unchanged and every pending/new command is stamped exactly"
    )
    return 0


def main() -> int:
    """Validate arguments, prepare release artifacts, and apply replacements."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "version",
        nargs="?",
        help="release version to stamp, e.g. 2026.9.12b1 (no leading 'v')",
    )
    parser.add_argument(
        "--check-release-bump",
        metavar="BASE_REF",
        help="validate the command-history transition when the version differs from BASE_REF",
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
            f"error: {version!r} is not a supported PEP 440 release version, "
            "e.g. 2026.9.12a1, 2026.9.12b1, 2026.9.12rc1, or 2026.9.12",
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
