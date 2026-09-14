#!/usr/bin/env python3
"""Fail when a task-list marker survives into the rendered HTML.

`mkdocs build --strict` cannot catch this. Markdown that uses an extension the
site does not enable is not an error: the parser does not recognise the syntax,
passes it through as literal text, and the build stays green. That is how 100
task-list lines across COMPLETION-ROADMAP and CONTRIBUTING shipped as literal
`[x]` and `[ ]` without anyone noticing.

## Why this checks one thing rather than markup in general

Earlier revisions scanned for any unrendered extension syntax: strikethrough,
highlight, task lists. That requires answering "would this extension have
consumed this text", and answering it from rendered HTML means reimplementing
Python-Markdown's grammar. Three review rounds produced three different defects
from exactly that gap, in both directions:

* `Select [ ] blank for no.` in ordinary prose was reported as unrendered, with
  advice to enable an extension that was already enabled and would not have
  changed it.
* `~~text ~~` is not consumed by `pymdownx.tilde` at all, but was reported as
  unrendered strikethrough with advice to enable it.
* `~~left<br/>right~~` *is* consumed, and escaped the check entirely, because a
  hard break ended the scanned run.

A detector that misidentifies what an extension consumes recommends a non-fix,
which is worse than silence: it spends the reader's one good attempt.
`docs/design/CODING-PRINCIPLES.md` calls repeated defects in one defensive layer
a signal to delete the layer, and three rounds is enough evidence.

So the general problem was removed rather than narrowed. Every extension whose
syntax appears in these docs is now enabled in `mkdocs.yml`, leaving no
unsupported-syntax class to detect. What remains here is a regression test for
the specific bug that started this, expressed as an invariant needing no
grammar: with `pymdownx.tasklist` enabled, a list item beginning with a task
marker renders as a checkbox, so a rendered `<li>` whose own leading text still
begins with `[ ]` or `[x]` proves the extension did not run.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

# A marker only means "task list" at the start of a list item's own text.
# Anywhere else it is ordinary prose, which is the distinction the general
# scanner could not make.
_MARKER = re.compile(r"^\s*\[[ xX]\]\s")

# Build output that is not authored documentation.
SKIP_DIRS = frozenset({"assets", "search"})


class _ListItems(HTMLParser):
    """Collect the leading text of each list item.

    Only text belonging to the item itself counts. `code` and `pre` content is
    ordinary text where `[ ]` is legitimate, so it ends collection for the
    enclosing item.

    A nested list needs no handling of its own: its first `<li>` closes the
    enclosing item, and text between a `<ul>` and its first `<li>` is not valid
    content. Listing `ul`/`ol` here as well changed no behaviour and no test, so
    it was removed rather than kept as a defensive branch nothing exercises.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._open = False
        self._leading: list[str] = []
        self.items: list[str] = []

    def _close_item(self) -> None:
        if self._open:
            self.items.append("".join(self._leading))
            self._leading = []
            self._open = False

    def handle_starttag(self, tag: str, _attrs: object) -> None:
        if tag == "li":
            self._close_item()
            self._open = True
        elif tag in ("code", "pre"):
            self._close_item()

    def handle_endtag(self, tag: str) -> None:
        if tag == "li":
            self._close_item()

    def handle_data(self, data: str) -> None:
        if self._open:
            self._leading.append(data)

    def close(self) -> None:
        super().close()
        self._close_item()


def _list_item_text(html: str) -> list[str]:
    parser = _ListItems()
    parser.feed(html)
    parser.close()
    return parser.items


def main(argv: list[str]) -> int:
    """Scan a built site, returning 1 if any task marker survived rendering."""
    site = Path(argv[1] if len(argv) > 1 else "site")
    if not site.is_dir():
        print(f"error: {site} is not a directory; run `mkdocs build` first", file=sys.stderr)
        return 2

    failures: list[str] = []
    scanned = 0
    for path in sorted(site.rglob("*.html")):
        if SKIP_DIRS & set(path.relative_to(site).parts):
            continue
        scanned += 1
        for item in _list_item_text(path.read_text(encoding="utf-8")):
            if _MARKER.match(item):
                failures.append(f"{path.relative_to(site)}: {' '.join(item.split())[:80]}")

    if failures:
        print(
            f"Task-list markers rendered as literal text in {len(failures)} list item(s):\n",
            file=sys.stderr,
        )
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        print(
            "\nThese ship to readers as `[x]` and `[ ]` rather than checkboxes.\n"
            "The MkDocs build passes regardless: syntax from an extension that is not "
            "enabled\nis emitted as literal text, not reported as an error.\n"
            "Fix: ensure pymdownx.tasklist is enabled in mkdocs.yml.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: no literal task-list markers across {scanned} page(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
