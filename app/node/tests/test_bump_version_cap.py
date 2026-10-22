"""CI gate: ``scripts/bump-version.py`` must refuse pre-release counters past 9.

The owner's rule (memory: ``feedback_beta_cap_b9``): for
``YYYY.M.D-{alpha,beta,rc}.N`` versions, never go past ``beta.9`` (or
``alpha.9`` / ``rc.9``). When the next bump would be ``beta.10``, roll the
calendar portion forward and reset the counter to ``beta.1`` instead.

Concrete failure mode this prevents: ``2026.6.20`` got stuck for a week
with bumps tacking on betas to a stale date, ending up at ``b11``. The
``/releases`` page then ordered prereleases unpredictably because there
was no ``Latest`` anchor (every release was a prerelease). Capping the
counter at 9 forces a date roll-forward before the prerelease backlog
gets ugly.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "bump-version.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )


def test_beta10_rejected_with_cap_message() -> None:
    """Bumping to ``beta.10`` exits non-zero, names the cap and the reset."""
    result = _run("2026.7.1-beta.10")
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "exceeds the beta.9 prerelease cap" in output
    assert "reset to beta.1" in output


def test_alpha10_rejected_with_cap_message() -> None:
    """The cap applies to the alpha track too."""
    result = _run("2026.7.1-alpha.10")
    assert result.returncode != 0
    assert "exceeds the alpha.9 prerelease cap" in (result.stdout + result.stderr)


def test_rc10_rejected_with_cap_message() -> None:
    """The cap applies to release candidates too."""
    result = _run("2026.7.1-rc.10")
    assert result.returncode != 0
    assert "exceeds the rc.9 prerelease cap" in (result.stdout + result.stderr)


def _run_bump_in_tmp(tmp_path: Path, new_version: str) -> subprocess.CompletedProcess[str]:
    """Copy the script + a minimal set of version-bearing sources into a
    throwaway tree and run the real ``_bump`` path against it.

    We can't run the in-repo bump for real (it would rewrite seven tracked
    files), so we mirror the file layout the script expects under *tmp_path*
    and invoke the script there. This actually exercises ``_bump`` —
    ``--check`` does not — which is the path the cap lives on.
    """
    import shutil

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(_SCRIPT, scripts_dir / "bump-version.py")

    layout = {
        "app/config.yaml": 'version: "2026.6.20-beta.9"\n',
        "app/build.yaml": '  io.hass.version: "2026.6.20-beta.9"\n',
        # The two Python-side sources hold the PEP 440 normalized form.
        "app/node/pyproject.toml": 'version = "2026.6.20b9"\n',
        "app/node/src/openclaw_node/__init__.py": '    __version__ = "2026.6.20b9"\n',
        "custom_components/openclaw_hass_node_assist/manifest.json": (
            '  "version": "2026.6.20-beta.9"\n'
        ),
        # Trailing comma: this key is mid-object, unlike the HACS manifest.
        "plugins/openclaw-hass-node-assist-tools/package.json": (
            '  "version": "2026.6.20-beta.9",\n'
        ),
        "skills/openclaw-hass-node-skill/SKILL.md": "version: 2026.6.20-beta.9\n",
    }
    for rel, content in layout.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    return subprocess.run(
        [sys.executable, str(scripts_dir / "bump-version.py"), new_version],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )


def test_beta9_still_accepted(tmp_path: Path) -> None:
    """``beta.9`` is the highest allowed counter — must not fire the cap.

    Runs the real ``_bump`` path (where the cap logic lives) against an
    isolated copy of the version-bearing sources.
    """
    result = _run_bump_in_tmp(tmp_path, "2026.7.1-beta.9")
    assert result.returncode == 0, result.stderr
    assert "exceeds" not in (result.stdout + result.stderr)


def test_final_version_unaffected(tmp_path: Path) -> None:
    """A non-prerelease (e.g. ``2026.7.0``) must not trigger the cap."""
    result = _run_bump_in_tmp(tmp_path, "2026.7.0")
    assert result.returncode == 0, result.stderr
    assert "exceeds" not in (result.stdout + result.stderr)


@pytest.mark.parametrize(
    "version",
    [
        "2026.9.12\n",
        "2026.9.12suffix",
        "2026.10.22-beta.0",  # counter starts at 1
        "2026.10.22-beta.01",  # no leading zeros in the counter
        "2026.10.22-beta1",  # the dot is required
        "2026.10.22-Beta.1",
        "2026.10.22-dev.1",
        "2026.10.022-beta.1",  # no leading zeros in day
        "2026.010.22-beta.1",  # ...or month
        "2026.10.22b1",  # the old form can no longer be written
        "2026.10.22.dev1",
        "v2026.10.22-beta.1",
    ],
)
def test_invalid_version_is_rejected(tmp_path: Path, version: str) -> None:
    """The bump command requires the entire argument to be canonical."""
    result = _run_bump_in_tmp(tmp_path, version)
    assert result.returncode != 0
    assert "is not a valid version" in (result.stdout + result.stderr)


def test_old_form_rejection_suggests_the_dash_form(tmp_path: Path) -> None:
    """A maintainer typing the old form is told what to type instead."""
    result = _run_bump_in_tmp(tmp_path, "2026.10.22b3")
    assert result.returncode != 0
    assert "use 2026.10.22-beta.3" in (result.stdout + result.stderr)
