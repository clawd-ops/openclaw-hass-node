"""Focused tests for the release stamping script.

Deliberately small. The script edits tracked files and trusts git for recovery,
so there is no transaction machinery to exercise — only the behaviour that
matters: given this manual ledger and this version, produce that ledger.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts/mark-commands-shipped.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _manual() -> dict[str, dict[str, str]]:
    path = _ROOT / "contracts/command-coverage-manual.json"
    commands: dict[str, dict[str, str]] = json.loads(path.read_text(encoding="utf-8"))["commands"]
    return commands


def test_rejects_a_missing_version() -> None:
    result = _run()
    assert result.returncode == 2
    assert "version is required" in result.stderr


def test_rejects_a_malformed_version() -> None:
    result = _run("not-a-version")
    assert result.returncode == 2
    assert "not a valid version string" in result.stderr


def test_rejects_a_version_the_repo_is_not_bumped_to() -> None:
    """A bare date without a prerelease marker is a valid pattern.

    It is still refused, because stamping a version the tracked files do not
    carry would record a release that was never cut.
    """
    result = _run("2026.9.12")
    assert result.returncode == 2
    assert "does not match synchronized tracked version" in result.stderr


def test_rejects_a_leading_v() -> None:
    """Tags carry the `v` prefix; ledger values never do."""
    result = _run("v2026.9.12b1")
    assert result.returncode == 2
    assert "without a leading 'v'" in result.stderr


def test_rejects_a_version_that_is_not_the_current_one() -> None:
    """Stamping a version the repo has not been bumped to would be a lie."""
    result = _run("1999.1.1b1")
    assert result.returncode == 2
    assert result.stderr


def test_version_pattern_uses_fullmatch() -> None:
    """A trailing newline must not sneak past the validator.

    `re.match` accepts it because `$` matches before a final newline, which let
    the ledger and the CLI disagree about what counted as a valid version.
    """
    result = _run("2026.9.12b1\n")
    assert result.returncode == 2
    assert "not a valid version string" in result.stderr


def test_unreleased_entries_exist_to_be_stamped() -> None:
    """Guards the fixture: the stamping path is meaningless with nothing pending."""
    values = {entry["first_shipped_in"] for entry in _manual().values()}
    assert "unreleased" in values
