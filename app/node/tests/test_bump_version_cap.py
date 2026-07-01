"""CI gate: ``scripts/bump-version.py`` must refuse pre-release counters past 9.

Rob's rule (memory: ``feedback_beta_cap_b9``): for ``YYYY.M.D{a|b|rc}n``
versions, never go past ``b9`` (or ``a9`` / ``rc9``). When the next bump
would be ``b10``, roll the calendar portion forward and reset the counter
to ``b1`` instead.

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


def test_b10_rejected_with_cap_message() -> None:
    """Bumping to ``b10`` exits non-zero and names the cap."""
    result = _run("2026.7.1b10")
    assert result.returncode != 0
    assert "exceeds the b9 prerelease cap" in (result.stdout + result.stderr)


def test_a10_rejected_with_cap_message() -> None:
    """The cap applies to the alpha track too."""
    result = _run("2026.7.1a10")
    assert result.returncode != 0
    assert "exceeds the a9 prerelease cap" in (result.stdout + result.stderr)


def test_rc10_rejected_with_cap_message() -> None:
    """The cap applies to release candidates too."""
    result = _run("2026.7.1rc10")
    assert result.returncode != 0
    assert "exceeds the rc9 prerelease cap" in (result.stdout + result.stderr)


def _run_bump_in_tmp(tmp_path: Path, new_version: str) -> subprocess.CompletedProcess[str]:
    """Copy the script + a minimal set of version-bearing sources into a
    throwaway tree and run the real ``_bump`` path against it.

    We can't run the in-repo bump for real (it would rewrite five tracked
    files), so we mirror the file layout the script expects under *tmp_path*
    and invoke the script there. This actually exercises ``_bump`` —
    ``--check`` does not — which is the path the cap lives on.
    """
    import shutil

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(_SCRIPT, scripts_dir / "bump-version.py")

    layout = {
        "app/config.yaml": 'version: "2026.6.20b9"\n',
        "app/build.yaml": '  io.hass.version: "2026.6.20b9"\n',
        "app/node/pyproject.toml": 'version = "2026.6.20b9"\n',
        "app/node/src/openclaw_node/__init__.py": '    __version__ = "2026.6.20b9"\n',
        "custom_components/openclaw_hass_node_assist/manifest.json": '  "version": "2026.6.20b9"\n',
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


def test_b9_still_accepted(tmp_path: Path) -> None:
    """``b9`` is the highest allowed counter — must not fire the cap.

    Runs the real ``_bump`` path (where the cap logic lives) against an
    isolated copy of the version-bearing sources.
    """
    result = _run_bump_in_tmp(tmp_path, "2026.7.1b9")
    assert result.returncode == 0, result.stderr
    assert "exceeds" not in (result.stdout + result.stderr)


def test_final_version_unaffected(tmp_path: Path) -> None:
    """A non-prerelease (e.g. ``2026.7.0``) must not trigger the cap."""
    result = _run_bump_in_tmp(tmp_path, "2026.7.0")
    assert result.returncode == 0, result.stderr
    assert "exceeds" not in (result.stdout + result.stderr)
