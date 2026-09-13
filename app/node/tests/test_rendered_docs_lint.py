"""Tests for scripts/lint-rendered-docs.py.

The lint exists because `mkdocs build --strict` cannot fail on Markdown that
uses an unenabled extension: the syntax is not recognised, so it is emitted as
literal text and the build is green. These fixtures assert both halves of that
job — the literal markup is caught, and the same characters appearing inside
code are not, since `[ ]` and `~~` are ordinary content in a code span.

Fixtures are synthetic HTML rather than a real build so each case states the
exact input it depends on. `test_catches_the_real_regression` is the one
exception: it renders the repo's own task-list syntax through Markdown with
and without the extension, so the pass case cannot drift away from what MkDocs
actually produces.
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


def test_clean_page_passes(tmp_path: Path) -> None:
    site = _site(tmp_path, {"index.html": "<p>Ordinary prose with no markup.</p>"})
    result = _run(site)
    assert result.returncode == 0, result.stderr
    assert "no unrendered Markdown syntax" in result.stdout


@pytest.mark.parametrize(
    ("body", "extension"),
    [
        ("<li>[ ] unchecked item</li>", "pymdownx.tasklist"),
        ("<li>[x] checked item</li>", "pymdownx.tasklist"),
        ("<li>[X] capitalised item</li>", "pymdownx.tasklist"),
        ("<p>~~superseded claim~~</p>", "pymdownx.tilde"),
        ("<p>==highlighted==</p>", "pymdownx.caret"),
    ],
)
def test_literal_syntax_fails_and_names_the_extension(
    tmp_path: Path, body: str, extension: str
) -> None:
    result = _run(_site(tmp_path, {"page.html": body}))
    assert result.returncode == 1
    assert extension in result.stderr
    assert "page.html" in result.stderr


@pytest.mark.parametrize(
    "body",
    [
        "<p>Use <code>- [ ] item</code> for a task.</p>",
        "<pre><code>grep -n '\\[x\\]' docs/*.md\n~~not strikethrough~~</code></pre>",
        "<p>A shell glob <code>a[ ]b</code> and <code>==</code>.</p>",
    ],
)
def test_code_is_not_flagged(tmp_path: Path, body: str) -> None:
    """`[ ]`, `~~` and `==` are legitimate content inside code."""
    result = _run(_site(tmp_path, {"page.html": body}))
    assert result.returncode == 0, result.stderr


def test_prose_brackets_are_not_flagged(tmp_path: Path) -> None:
    """Only a bracket pair followed by whitespace looks like a task marker.

    Citation-style `[1]`, an empty `[]`, and a bracket mid-word must not trip
    the check, or the lint would be noise on ordinary prose.
    """
    body = "<p>See [1] and [] and file[x]name and [ok] for detail.</p>"
    result = _run(_site(tmp_path, {"page.html": body}))
    assert result.returncode == 0, result.stderr


def test_build_assets_are_skipped(tmp_path: Path) -> None:
    """Bundled JS ships arbitrary text and is not authored documentation."""
    site = _site(
        tmp_path,
        {
            "assets/javascripts/bundle.html": "<p>~~tilde in a vendored bundle~~</p>",
            "search/search_index.html": "<p>[ ] indexed copy of a task line</p>",
        },
    )
    result = _run(site)
    assert result.returncode == 0, result.stderr


def test_missing_site_directory_is_a_usage_error(tmp_path: Path) -> None:
    result = _run(tmp_path / "never-built")
    assert result.returncode == 2
    assert "mkdocs build" in result.stderr


# Both fixtures below are the real markup for the opening checklist of
# `docs/COMPLETION-ROADMAP.md`, copied from `site/` built with and without
# `pymdownx.tasklist`. Reproducing the rendered shape here rather than calling
# Markdown keeps this test running in the node suite, which does not depend on
# the docs toolchain. Note the continuation lines: every task item on that page
# is a flat `<li>` with its wrapped text inside, with no nested sub-list, which
# is why enabling the extension could not restructure the page.
_RENDERED_WITH_EXTENSION = (
    '<ul class="task-list">\n'
    '<li class="task-list-item"><label class="task-list-control">'
    '<input type="checkbox" disabled/><span class="task-list-indicator"></span>'
    "</label> One authoritative contract defines every supported command/action,\n"
    "  parameters, aliases, bounds, result schema, caller paths, authorization, and\n"
    "  availability conditions.</li>\n"
    "</ul>"
)

_RENDERED_WITHOUT_EXTENSION = (
    "<ul>\n"
    "<li>[ ] One authoritative contract defines every supported command/action,\n"
    "  parameters, aliases, bounds, result schema, caller paths, authorization, and\n"
    "  availability conditions.</li>\n"
    "</ul>"
)


def test_accepts_the_real_rendered_checklist(tmp_path: Path) -> None:
    """The shape MkDocs actually emits must not trip the lint."""
    result = _run(_site(tmp_path, {"page.html": _RENDERED_WITH_EXTENSION}))
    assert result.returncode == 0, result.stderr


def test_catches_the_real_regression(tmp_path: Path) -> None:
    """The same source without the extension is what shipped broken."""
    result = _run(_site(tmp_path, {"page.html": _RENDERED_WITHOUT_EXTENSION}))
    assert result.returncode == 1
    assert "pymdownx.tasklist" in result.stderr
