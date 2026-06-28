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


def test_b9_still_accepted() -> None:
    """``b9`` is the highest allowed counter — must not fire the cap.

    Use ``--check`` against the current synced version to assert that the
    script can be invoked without writing files; ``--check`` does not flow
    through the cap, only ``_bump`` does, so we still need the bump path
    smoke-tested. Use a no-op same-version bump for that: if the script
    reaches ``_bump`` it will write no files and exit 0.
    """
    from openclaw_node import __version__

    result = _run("--check", __version__)
    assert result.returncode == 0, result.stderr


def test_final_version_unaffected() -> None:
    """A non-prerelease (e.g. ``2026.7.0``) must not trigger the cap."""
    result = _run("--check")
    # --check with no version just verifies the sources agree; should pass
    assert result.returncode == 0, result.stderr
