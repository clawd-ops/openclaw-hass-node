"""Focused tests for the release stamping script.

Deliberately small. The script edits tracked files and trusts git for recovery,
so there is no transaction machinery to exercise — only the behaviour that
matters: given this manual ledger and this version, produce that ledger.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

import pytest

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


def _run_in(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "scripts/mark-commands-shipped.py"), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def _clone_repo(tmp_path: Path) -> Path:
    """Working copy so stamping probes never mutate the real tree."""
    work = tmp_path / "repo"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(_ROOT), str(work)],
        check=True,
        capture_output=True,
    )
    # A clone carries committed state, so copy in the scripts under test; these
    # probes must exercise the working tree, not the last commit.
    for script in ("mark-commands-shipped.py", "generate-command-coverage.py", "bump-version.py"):
        shutil.copy2(_ROOT / "scripts" / script, work / "scripts" / script)
    return work


def _tracked_version(repo: Path) -> str:
    """Read the synchronized version the stamper will insist on.

    That is the literal app/config.yaml carries (the dash form and the tag);
    pyproject.toml holds its PEP 440 normalized form.
    """
    current = (repo / "app/config.yaml").read_text(encoding="utf-8")
    match = re.search(r'^version: "([^"]+)"$', current, re.MULTILINE)
    assert match is not None, "app/config.yaml must carry a version"
    return match.group(1)


def test_stamping_rewrites_every_unreleased_entry() -> None:
    """The behavior the script exists for, which had no test at all."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _clone_repo(Path(tmp))
        version = json.loads(
            (repo / "docs/reference/command-coverage.json").read_text(encoding="utf-8")
        )
        assert version is not None

        manual = repo / "contracts/command-coverage-manual.json"
        # Create the pending state this test is about rather than depending on
        # the repository happening to have some. A release sweep stamps every
        # pending command, so immediately after one there are none, and reading
        # them from the tree made this test fail on the release commit itself.
        raw = manual.read_bytes()
        data = json.loads(raw.decode("utf-8"))
        pending = sorted(list(data["commands"])[:2])
        assert pending, "the ledger must define commands for this to mean anything"
        for name in pending:
            data["commands"][name]["first_shipped_in"] = "unreleased"
        indent = 2 if raw.startswith(b"{\n  ") else 4
        manual.write_text(
            json.dumps(data, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        stamp = _tracked_version(repo)

        result = _run_in(repo, stamp)
        assert result.returncode == 0, result.stderr

        after = json.loads(manual.read_text(encoding="utf-8"))["commands"]
        assert all(after[name]["first_shipped_in"] == stamp for name in pending)
        assert not any(e["first_shipped_in"] == "unreleased" for e in after.values())


def test_stamping_regenerates_even_with_nothing_to_stamp() -> None:
    """A version-only release still moves `latest_release`.

    Returning early before regeneration left the ledger describing the previous
    release while the repo claimed the new one.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = _clone_repo(Path(tmp))
        stamp = _tracked_version(repo)

        assert _run_in(repo, stamp).returncode == 0
        second = _run_in(repo, stamp)

        assert second.returncode == 0, second.stderr
        assert "regenerated artifacts only" in second.stdout
        # The generator must have run: the check gate agrees with the tree.
        check = subprocess.run(
            [sys.executable, "scripts/generate-command-coverage.py", "--check"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert check.returncode == 0, check.stdout + check.stderr


def test_check_release_bump_rejects_a_malformed_ref() -> None:
    """A bad ref must produce the CLI's error contract, not a traceback.

    Asserting only a nonzero status accepted a raw traceback, which is how that
    failure mode survived: the test was too weak to catch what it was for.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = _clone_repo(Path(tmp))
        result = _run_in(repo, "--check-release-bump", "not-a-ref")
        assert result.returncode == 2
        assert result.stderr.startswith("error: ")
        assert "Traceback" not in result.stderr


def test_check_release_bump_reports_malformed_current_ledger_without_traceback() -> None:
    """Current ledger validation failures use the controlled CLI diagnostic."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _clone_repo(Path(tmp))
        manual = repo / "contracts/command-coverage-manual.json"
        data = json.loads(manual.read_text(encoding="utf-8"))
        first = sorted(data["commands"])[0]
        data["commands"][first]["first_shipped_in"] = "not-a-release"
        manual.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

        result = _run_in(repo, "--check-release-bump", "HEAD")

        assert result.returncode == 2
        assert result.stderr.startswith("error: ")
        assert "Traceback" not in result.stderr


def test_check_release_bump_reports_malformed_base_ledger_without_traceback() -> None:
    """Base ledger validation failures use the controlled CLI diagnostic."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _clone_repo(Path(tmp))
        manual = repo / "contracts/command-coverage-manual.json"
        original = manual.read_text(encoding="utf-8")
        data = json.loads(original)
        first = sorted(data["commands"])[0]
        data["commands"][first]["first_shipped_in"] = "not-a-release"
        manual.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        malformed_base = _commit_all(repo, "malformed base ledger")
        manual.write_text(original, encoding="utf-8")
        _commit_all(repo, "restore current ledger")

        result = _run_in(repo, "--check-release-bump", malformed_base)

        assert result.returncode == 2
        assert result.stderr.startswith("error: ")
        assert "Traceback" not in result.stderr


def test_check_release_bump_refuses_a_version_argument() -> None:
    """The two modes are mutually exclusive; taking both would be ambiguous."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _clone_repo(Path(tmp))
        result = _run_in(repo, "--check-release-bump", "HEAD", "2026.9.12b1")
        assert result.returncode == 2
        assert "takes no version argument" in result.stderr


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-q",
            "-m",
            message,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _pep440(version: str) -> str:
    """`2026.10.22-beta.1` -> `2026.10.22b1`; anything else is unchanged."""
    stage = {"alpha": "a", "beta": "b", "rc": "rc"}
    return re.sub(r"-(alpha|beta|rc)\.(\d+)$", lambda m: f"{stage[m[1]]}{m[2]}", version)


def _set_versions(repo: Path, version: str) -> None:
    """Move all seven tracked version sources so the gate sees a real bump."""
    normalized = _pep440(version)
    for rel, pattern, repl in (
        ("app/node/pyproject.toml", r'^version = "[^"]+"$', f'version = "{normalized}"'),
        ("app/config.yaml", r'^version: "[^"]+"$', f'version: "{version}"'),
        ("app/build.yaml", r'^  io\.hass\.version: "[^"]+"$', f'  io.hass.version: "{version}"'),
        (
            "app/node/src/openclaw_node/__init__.py",
            r'__version__ = "[^"]+"',
            f'__version__ = "{normalized}"',
        ),
        (
            "custom_components/openclaw_hass_node_assist/manifest.json",
            r'"version": "[^"]+"',
            f'"version": "{version}"',
        ),
        (
            "plugins/openclaw-hass-node-assist-tools/package.json",
            r'^  "version": "[^"]+",$',
            f'  "version": "{version}",',
        ),
        (
            "skills/openclaw-hass-node-skill/SKILL.md",
            r"^version: .+$",
            f"version: {version}",
        ),
    ):
        path = repo / rel
        if path.exists():
            path.write_text(
                re.sub(pattern, repl, path.read_text(encoding="utf-8"), flags=re.MULTILINE),
                encoding="utf-8",
            )


def _baseline(repo: Path) -> str:
    """Commit a steady state where every command already has a shipped value.

    The real base predates `first_shipped_in`, so CI only ever takes the
    one-time initialization branch. Without this the steady-state policy — the
    branch that enforces version advancement, immutable released history and
    exact stamping — is never executed by any test.
    """
    manual = repo / "contracts/command-coverage-manual.json"
    data = json.loads(manual.read_text(encoding="utf-8"))
    version = _tracked_version(repo)
    names = sorted(data["commands"])
    # Construct the steady state deliberately rather than inheriting whichever
    # commands the tree happens to carry as pending. This used to stamp all but
    # the last already-pending command, which silently produced no pending
    # command at all once a release sweep had stamped every one of them.
    #
    # Writing "unreleased" here is establishing the baseline commit, not
    # rewriting history: the gate compares this base against the head, and both
    # agree until a test deliberately diverges them.
    pending_name = names[-1]
    for name in names:
        data["commands"][name]["first_shipped_in"] = (
            "unreleased" if name == pending_name else version
        )
    manual.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    subprocess.run(
        [sys.executable, "scripts/generate-command-coverage.py"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return _commit_all(repo, "baseline: all commands shipped")


def _pending_command(repo: Path) -> str:
    """Name the command the baseline deliberately left unreleased."""
    data = json.loads((repo / "contracts/command-coverage-manual.json").read_text(encoding="utf-8"))
    pending = sorted(
        n for n, e in data["commands"].items() if e["first_shipped_in"] == "unreleased"
    )
    assert len(pending) == 1, pending
    name: str = pending[0]
    return name


def test_steady_state_unchanged_version_passes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = _clone_repo(Path(tmp))
        base = _baseline(repo)
        result = _run_in(repo, "--check-release-bump", base)
        assert result.returncode == 0, result.stdout + result.stderr


def test_steady_state_rejects_rewriting_released_history() -> None:
    """Released history is immutable; a rewrite must fail the gate."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _clone_repo(Path(tmp))
        base = _baseline(repo)
        manual = repo / "contracts/command-coverage-manual.json"
        data = json.loads(manual.read_text(encoding="utf-8"))
        victim = sorted(data["commands"])[0]
        data["commands"][victim]["first_shipped_in"] = "2026.1.1b1"
        manual.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        subprocess.run(
            [sys.executable, "scripts/generate-command-coverage.py"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        _commit_all(repo, "rewrite released history")

        result = _run_in(repo, "--check-release-bump", base)
        assert result.returncode == 1
        assert victim in result.stderr


def test_steady_state_rejects_an_unstamped_release_bump() -> None:
    """Bumping the version without stamping pending commands must fail."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _clone_repo(Path(tmp))
        base = _baseline(repo)

        manual = repo / "contracts/command-coverage-manual.json"
        data = json.loads(manual.read_text(encoding="utf-8"))
        pending = sorted(data["commands"])[0]
        data["commands"][pending]["first_shipped_in"] = "unreleased"
        manual.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        _set_versions(repo, "2099.1.1b1")
        subprocess.run(
            [sys.executable, "scripts/generate-command-coverage.py"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        _commit_all(repo, "bump without stamping")

        result = _run_in(repo, "--check-release-bump", base)
        assert result.returncode == 1
        assert pending in result.stderr


def _stamp_pending(repo: Path, version: str) -> None:
    """Stamp the baseline's pending command with *version* and regenerate."""
    manual = repo / "contracts/command-coverage-manual.json"
    data = json.loads(manual.read_text(encoding="utf-8"))
    data["commands"][_pending_command(repo)]["first_shipped_in"] = version
    manual.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    subprocess.run(
        [sys.executable, "scripts/generate-command-coverage.py"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_transition_from_the_old_form_to_the_dash_form_passes() -> None:
    """`2026.10.15b1` -> `2026.10.22-beta.1`: the base is old-form, the head is not."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _clone_repo(Path(tmp))
        base = _baseline(repo)
        assert _tracked_version(repo) == "2026.10.15b1"
        _set_versions(repo, "2026.10.22-beta.1")
        _stamp_pending(repo, "2026.10.22-beta.1")
        _commit_all(repo, "release 2026.10.22-beta.1")

        result = _run_in(repo, "--check-release-bump", base)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "2026.10.15b1 to 2026.10.22-beta.1" in result.stdout


def test_dash_to_dash_beta_bump_passes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = _clone_repo(Path(tmp))
        _set_versions(repo, "2026.10.22-beta.1")
        base = _baseline(repo)
        _set_versions(repo, "2026.10.22-beta.2")
        _stamp_pending(repo, "2026.10.22-beta.2")
        _commit_all(repo, "release 2026.10.22-beta.2")

        result = _run_in(repo, "--check-release-bump", base)
        assert result.returncode == 0, result.stdout + result.stderr


def test_dash_form_with_no_bump_is_not_mistaken_for_a_release() -> None:
    """pyproject holds the normalized form; that must not read as a version change."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _clone_repo(Path(tmp))
        _set_versions(repo, "2026.10.22-beta.1")
        base = _baseline(repo)

        result = _run_in(repo, "--check-release-bump", base)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "no release version bump" in result.stdout


def test_head_in_the_old_form_is_rejected() -> None:
    """New versions must use the dash form even though the old one stays readable."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _clone_repo(Path(tmp))
        base = _baseline(repo)
        _set_versions(repo, "2026.10.22b1")
        _stamp_pending(repo, "2026.10.22b1")
        _commit_all(repo, "release in the old form")

        result = _run_in(repo, "--check-release-bump", base)
        assert result.returncode == 1
        assert "old pre-release form" in result.stderr


def test_a_version_that_does_not_advance_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = _clone_repo(Path(tmp))
        _set_versions(repo, "2026.10.22-beta.2")
        base = _baseline(repo)
        _set_versions(repo, "2026.10.22-beta.1")
        _stamp_pending(repo, "2026.10.22-beta.1")
        _commit_all(repo, "go backwards")

        result = _run_in(repo, "--check-release-bump", base)
        assert result.returncode == 1
        assert "version must advance" in result.stderr


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mark_commands_shipped_under_test", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("new", "old"),
    [
        ("2026.10.22-beta.2", "2026.10.22-beta.1"),
        ("2026.10.22", "2026.10.22-beta.2"),
        ("2026.10.22-beta.1", "2026.10.15b1"),
        ("2026.10.22-rc.1", "2026.10.22-beta.9"),
        ("2026.11.1-beta.1", "2026.10.31"),
        ("2026.10.22-beta.1", "2026.10.9b1"),  # two-digit day vs one-digit day
    ],
)
def test_awesomeversion_gate_orders_increases(new: str, old: str) -> None:
    script = _load_script()
    assert script._awesome_greater(new, old) is True
    assert script._awesome_greater(old, new) is False


def test_awesomeversion_gate_requires_the_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing pin fails loudly with an install hint instead of skipping the check."""
    monkeypatch.setitem(sys.modules, "awesomeversion", None)
    script = _load_script()
    with pytest.raises(script.ShipError, match=r"awesomeversion==25\.8\.0"):
        script._awesome_greater("2026.10.22-beta.1", "2026.10.15b1")


def test_awesomeversion_pin_matches_across_workflows_and_dev_dependencies() -> None:
    """CI installs the same AwesomeVersion the dev group pins (Home Assistant's pin)."""
    pin = re.search(r'"(awesomeversion==[\d.]+)"', (_ROOT / "pyproject.toml").read_text())
    assert pin is not None
    for workflow in ("ci.yaml", "release-on-version-bump.yml"):
        text = (_ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
        assert f"pip install {pin[1]}" in text, workflow
