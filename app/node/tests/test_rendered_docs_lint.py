"""Tests for scripts/lint-rendered-docs.py.

The lint exists because `mkdocs build --strict` cannot fail on Markdown that
uses an unenabled extension: the syntax is not recognised, so it is emitted as
literal text and the build is green.

It deliberately checks one invariant rather than markup in general. Three
earlier review rounds each found a new defect in a general scanner, because
deciding "would this extension have consumed this text" from rendered HTML means
reimplementing Python-Markdown's grammar. The cases that defeated it are kept
below as negative tests, so a future attempt to generalise has to confront them.

Fixtures are synthetic HTML, so each case states the exact input it depends on.
The Docs workflow runs the lint against the real built site, which is what keeps
the fixtures honest.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "lint-rendered-docs.py"


def _run(site: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(site)],
        capture_output=True,
        check=False,
        text=True,
    )


def _site(tmp_path: Path, pages: dict[str, str]) -> Path:
    site = tmp_path / "site"
    for relative, body in pages.items():
        page = site / relative
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(f"<html><body>{body}</body></html>", encoding="utf-8")
    site.mkdir(exist_ok=True)
    return site


@pytest.mark.parametrize(
    "body",
    [
        "<ul><li>[ ] unchecked item</li></ul>",
        "<ul><li>[x] checked item</li></ul>",
        "<ul><li>[X] capitalised item</li></ul>",
        # The real shape: wrapped continuation text inside the same item.
        "<ul><li>[ ] One authoritative contract defines every supported\n  command.</li></ul>",
    ],
)
def test_literal_marker_in_a_list_item_fails(tmp_path: Path, body: str) -> None:
    """This is the regression that motivated the lint."""
    result = _run(_site(tmp_path, {"page.html": body}))
    assert result.returncode == 1
    assert "page.html" in result.stderr
    assert "pymdownx.tasklist" in result.stderr


def test_rendered_checkbox_passes(tmp_path: Path) -> None:
    """Markup copied from a real strict build with the extension enabled."""
    body = (
        '<ul class="task-list">\n'
        '<li class="task-list-item"><label class="task-list-control">'
        '<input type="checkbox" disabled/><span class="task-list-indicator"></span>'
        "</label> One authoritative contract defines every supported command.</li>\n"
        "</ul>"
    )
    result = _run(_site(tmp_path, {"page.html": body}))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "body",
    [
        # Each of these defeated the general scanner in a review round. They are
        # not task-list markers, and reporting them recommended a non-fix.
        "<p>Select [ ] blank for no.</p>",
        "<p>~~text ~~</p>",
        "<p>~~left<br/>right~~</p>",
        "<p>==high  <br/>light==</p>",
        # A marker mid-item is prose, not a task marker: the extension only
        # consumes it at the start of the item.
        "<ul><li>choose [ ] when unsure</li></ul>",
        # Citation-style and empty brackets.
        "<ul><li>[1] a reference</li><li>[] empty</li></ul>",
    ],
)
def test_prose_is_not_reported(tmp_path: Path, body: str) -> None:
    result = _run(_site(tmp_path, {"page.html": body}))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "body",
    [
        "<ul><li><code>[ ] item</code> is the source syntax</li></ul>",
        "<ul><li><pre>[ ] inside a block</pre></li></ul>",
    ],
)
def test_code_in_a_list_item_is_not_reported(tmp_path: Path, body: str) -> None:
    """`[ ]` is ordinary content inside code, and the docs show it that way."""
    result = _run(_site(tmp_path, {"page.html": body}))
    assert result.returncode == 0, result.stderr


def test_nested_list_item_is_checked_on_its_own(tmp_path: Path) -> None:
    """A nested item is its own item, not trailing text of its parent."""
    body = "<ul><li>parent text<ul><li>[ ] nested marker</li></ul></li></ul>"
    result = _run(_site(tmp_path, {"page.html": body}))
    assert result.returncode == 1, result.stdout


def test_parent_item_text_before_a_nested_list_is_not_confused(tmp_path: Path) -> None:
    """Opening a nested list ends the parent's leading text rather than merging."""
    body = "<ul><li>parent<ul><li>child</li></ul>[ ] trailing prose</li></ul>"
    result = _run(_site(tmp_path, {"page.html": body}))
    assert result.returncode == 0, result.stderr


def test_build_assets_are_skipped(tmp_path: Path) -> None:
    """Bundled JS ships arbitrary text and is not authored documentation."""
    site = _site(
        tmp_path,
        {
            "assets/javascripts/bundle.html": "<ul><li>[ ] in a vendored bundle</li></ul>",
            "search/search_index.html": "<ul><li>[ ] indexed copy</li></ul>",
        },
    )
    result = _run(site)
    assert result.returncode == 0, result.stderr


def test_missing_site_directory_is_a_usage_error(tmp_path: Path) -> None:
    result = _run(tmp_path / "never-built")
    assert result.returncode == 2
    assert "mkdocs build" in result.stderr


def test_failure_message_names_the_fix(tmp_path: Path) -> None:
    """Asserted against real stderr, not the format string.

    A diagnostic naming no remedy leaves the reader where they started, which is
    the defect tracked in #348.
    """
    result = _run(_site(tmp_path, {"page.html": "<ul><li>[ ] task</li></ul>"}))
    assert result.returncode == 1
    assert "pymdownx.tasklist" in result.stderr
    assert "mkdocs.yml" in result.stderr
    assert "checkboxes" in result.stderr
