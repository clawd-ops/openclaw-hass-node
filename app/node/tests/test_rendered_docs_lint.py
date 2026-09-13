"""Tests for scripts/lint-rendered-docs.py.

The lint exists because `mkdocs build --strict` cannot fail on Markdown that
uses an unenabled extension: the syntax is not recognised, so it is emitted as
literal text and the build is green. These fixtures assert both halves of that
job — the literal markup is caught, and the same characters appearing inside
code are not, since `[ ]` and `~~` are ordinary content in a code span.

Fixtures are synthetic HTML rather than a real build, so each case states the
exact input it depends on. The two `_RENDERED_*` constants are copied verbatim
from a real strict build of `docs/COMPLETION-ROADMAP.md` with and without the
extension; the node test environment has neither `markdown` nor
`pymdown-extensions`, so rendering them here is not possible. The Docs workflow
exercises the real MkDocs output, which is what keeps them honest.
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
        ("<p>==highlighted==</p>", "pymdownx.mark"),
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
        "<pre><code>grep -n docs</code>\n[ ] still inside the pre block\n</pre>",
        "<p>Write <code>[ ] item</code> to start a task.</p>",
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
    body = "<p>See [1] and [] and file[x] item and [ok] for detail.</p>"
    result = _run(_site(tmp_path, {"page.html": body}))
    assert result.returncode == 0, result.stderr


def test_marker_is_found_when_a_previous_element_ends_in_a_word(
    tmp_path: Path,
) -> None:
    """A real marker must not be hidden by the element before it.

    Concatenating text nodes turns `<p>word</p><li>[ ] task</li>` into
    `word[ ] task`, where the synthetic preceding character is a word character
    and the boundary condition suppresses a marker the reader can plainly see.
    Today's MkDocs output separates block elements with whitespace, so this is
    masked on the current build; the check must not depend on that.
    """
    body = "<p>word</p><li>[ ] task</li>"
    result = _run(_site(tmp_path, {"page.html": body}))
    assert result.returncode == 1, result.stdout
    assert "pymdownx.tasklist" in result.stderr


def test_delimiters_do_not_pair_across_elements(tmp_path: Path) -> None:
    """Two elements each holding one tilde are not strikethrough.

    Concatenation would join `<p>~~left</p><p>right~~</p>` into
    `~~leftright~~` and report markup that exists in neither element.
    """
    body = "<p>~~left</p><p>right~~</p>"
    result = _run(_site(tmp_path, {"page.html": body}))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "body",
    [
        # `~~a *b* c~~` with the extension disabled: three text nodes, and no
        # single one holds a delimiter pair. Scanning nodes in isolation missed
        # this entirely.
        "<p>~~left <em>emphasis</em> right~~</p>",
        "<p>~~left <!-- comment --> right~~</p>",
        "<li>[ ] task with <strong>bold</strong> text</li>",
        "<p>==high <em>light</em> ed==</p>",
    ],
)
def test_markup_split_by_inline_elements_is_still_found(tmp_path: Path, body: str) -> None:
    """Inline markup must not hide unrendered syntax.

    This is legitimate extension syntax, not an invented cross-element pair:
    enabling the extension consumes exactly this source.
    """
    result = _run(_site(tmp_path, {"page.html": body}))
    assert result.returncode == 1, result.stdout


@pytest.mark.parametrize("tag", ["script", "style"])
def test_each_suppressed_container_is_suppressed(tmp_path: Path, tag: str) -> None:
    """Every entry in the suppressed set must actually be doing work.

    A reviewer found that removing `script` or `style` individually left the
    whole suite green, so the set was asserted only in aggregate.
    """
    body = f"<{tag}>var s = '~~not strikethrough~~';</{tag}>"
    result = _run(_site(tmp_path, {"page.html": body}))
    assert result.returncode == 0, result.stderr


def test_failure_message_names_both_ways_out(tmp_path: Path) -> None:
    """A diagnostic that cannot be acted on is the defect in #348.

    Naming only the problem leaves the author where the operator was: told
    something is wrong, given no path to the fix. Assert against real output,
    not the format string.
    """
    result = _run(_site(tmp_path, {"page.html": "<p>~~struck~~</p>"}))
    assert result.returncode == 1
    assert "pymdownx.tilde" in result.stderr
    assert "<del>" in result.stderr
    assert "mkdocs.yml" in result.stderr


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
