"""The docs must not use Markdown syntax the site does not enable.

`mkdocs build --strict` cannot catch this. Syntax from an unenabled extension is
not an error: Python-Markdown does not recognise it, emits it as literal text,
and the build stays green. That is how 100 task-list lines across
`COMPLETION-ROADMAP.md` and `CONTRIBUTING.md` shipped as literal `[x]` and `[ ]`.

## Why this reads the source and not the built HTML

Three earlier attempts scanned rendered HTML. Every one was wrong, because the
question is about the *source* and rendering destroys the evidence:

* `- \\[ \\] literal choice` escapes the brackets deliberately. Python-Markdown
  correctly leaves it as prose and emits `<li>[ ] literal choice</li>`, which is
  byte-identical to an unconsumed task marker. Entity escapes do the same.
* `- *[ ]* emphasized literal` is also deliberate, and flattening inline markup
  loses that distinction too.

So no check on rendered HTML can tell "the extension was missing" from "the
author meant a literal bracket". The claimed invariant was false, and the
scanner reported real, correct documentation as broken while recommending an
extension that was already enabled.

The source has the information the HTML lost. This asserts the one thing that
actually matters and is provable: **if a page uses task-list syntax, the
extension that consumes it must be enabled.**
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
_DOCS = _ROOT / "docs"
_MKDOCS = _ROOT / "mkdocs.yml"

# A task-list item: a list bullet, then an unescaped bracket pair, then text.
# `\[` does not match, which is the distinction rendered HTML could not keep.
_TASK_ITEM = re.compile(r"^\s*[-*+] \[[ xX]\] \S")

_FENCE = re.compile(r"^\s*(```|~~~)")


def _enabled_extensions() -> set[str]:
    """Extension names from `mkdocs.yml`, whether or not they carry options."""
    # MkDocs config uses Python-specific YAML tags (`!!python/name:...`), which
    # the safe loader rejects. Only the extension names are needed here, so read
    # that one block rather than the whole document.
    config = yaml.safe_load(
        re.search(
            r"^markdown_extensions:\n(?:(?:[ \t-].*)?\n)*",
            _MKDOCS.read_text(encoding="utf-8"),
            re.M,
        ).group(0)
    )
    names: set[str] = set()
    for entry in config["markdown_extensions"]:
        names.add(entry if isinstance(entry, str) else next(iter(entry)))
    return names


def _prose_lines(text: str) -> list[str]:
    """Lines outside fenced code blocks.

    A fenced block showing `- [ ] item` as an example is documentation about the
    syntax, not a use of it.
    """
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return lines


def _pages_using_task_lists() -> dict[Path, int]:
    counts: dict[Path, int] = {}
    for path in sorted(_DOCS.rglob("*.md")):
        hits = sum(
            1 for line in _prose_lines(path.read_text(encoding="utf-8")) if _TASK_ITEM.match(line)
        )
        if hits:
            counts[path.relative_to(_ROOT)] = hits
    return counts


def test_task_list_syntax_requires_its_extension() -> None:
    """The original regression, asserted where it is decidable."""
    pages = _pages_using_task_lists()
    assert pages, "no page uses task-list syntax; this guard is now vacuous"
    assert "pymdownx.tasklist" in _enabled_extensions(), (
        "These pages use task-list syntax and would ship as literal `[x]` / `[ ]`:\n"
        + "\n".join(f"  {path} ({n} lines)" for path, n in pages.items())
        + "\n\nEnable pymdownx.tasklist in mkdocs.yml."
    )


def test_escaped_brackets_are_not_counted_as_task_syntax() -> None:
    """The false positive that defeated every rendered-HTML attempt.

    An author escaping the brackets means a literal, and the extension correctly
    leaves it alone. In the built HTML this is indistinguishable from a missing
    extension; in the source it is unambiguous.
    """
    assert _TASK_ITEM.match("- [ ] real task item")
    assert not _TASK_ITEM.match(r"- \[ \] literal choice")
    assert not _TASK_ITEM.match("- *[ ]* emphasized literal")
    assert not _TASK_ITEM.match("- [1] a citation")
    assert not _TASK_ITEM.match("- [] empty brackets")
    assert not _TASK_ITEM.match("text [ ] mid-line")


def test_fenced_examples_are_not_counted_as_usage() -> None:
    """Showing the syntax in a code block is not using it."""
    text = "```\n- [ ] shown as an example\n```\n\n- [ ] actually used\n"
    assert sum(1 for line in _prose_lines(text) if _TASK_ITEM.match(line)) == 1


def test_enabled_extensions_parses_both_forms() -> None:
    """Extensions appear bare and with an options mapping; both must be seen."""
    enabled = _enabled_extensions()
    assert "pymdownx.tasklist" in enabled, "mapping form with options"
    assert "admonition" in enabled, "bare string form"
