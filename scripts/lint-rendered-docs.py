#!/usr/bin/env python3
"""Fail when Markdown extension syntax survives into the rendered HTML.

MkDocs `--strict` does not catch this class of bug. Markdown that uses an
extension which is not enabled in `mkdocs.yml` is not an error: the parser
simply does not recognise the syntax and passes it through as literal text.
The build stays green and the page ships with `[x]` or `~~text~~` visible to
the reader. That is exactly how 100 task-list lines across COMPLETION-ROADMAP
and CONTRIBUTING rendered as literal brackets without anyone noticing.

Rather than maintain a mapping from each syntax to the extension that enables
it, this checks the only thing that actually matters: markup that should have
been consumed is still present in the output. Any future extension syntax that
someone writes without enabling gets caught by adding one pattern here.

Code is excluded. `[ ]`, `~~` and `==` are all ordinary content inside a code
span or block, and the docs legitimately contain them there.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple


class _Syntax(NamedTuple):
    """One unrendered-markup pattern and both ways out of it.

    `extension` and `alternative` are why this reports rather than merely
    detects. A message that says only "unconsumed markup" leaves the author
    exactly where the operator in #347 was: told something is wrong, given no
    path to fixing it. Naming the extension that consumes the syntax *and* the
    supported markup that needs no extension covers both decisions an author can
    make.
    """

    label: str
    pattern: re.Pattern[str]
    extension: str
    alternative: str


PATTERNS: tuple[_Syntax, ...] = (
    _Syntax(
        "task list",
        re.compile(r"(?<![\w`])\[[ xX]\](?=\s)"),
        "pymdownx.tasklist",
        "a plain list item without the bracket marker",
    ),
    _Syntax(
        "strikethrough",
        re.compile(r"~~[^\s~][^~]*~~"),
        "pymdownx.tilde",
        "a literal <del>...</del> element, which Markdown passes through",
    ),
    # `==text==` is pymdownx.mark. pymdownx.caret is `^^insert^^` and `^sup^`,
    # and naming it here would send an author to an extension that cannot
    # consume what they wrote.
    _Syntax(
        "highlight",
        re.compile(r"==[^\s=][^=]*=="),
        "pymdownx.mark",
        "a literal <mark>...</mark> element, which Markdown passes through",
    ),
)

# Directories of build output that are not authored documentation.
SKIP_DIRS = frozenset({"assets", "search"})

# Tags that do not interrupt a run of text. Markdown emits extension syntax as
# one logical run, but inline markup splits it across nodes: `~~a *b* c~~` with
# the extension disabled renders as three text nodes, and scanning each node in
# isolation finds no delimiter pair in any of them.
#
# Anything not listed is treated as a block boundary, which is the conservative
# default: an unknown tag ends the run rather than joining two unrelated ones.
# That risks missing a marker spanning an unrecognised inline element, and
# avoids inventing one across two paragraphs. A false failure blocks the build
# for everyone; a missed one costs what the status quo already costs.
INLINE_TAGS = frozenset(
    {
        "a",
        "abbr",
        "b",
        "bdi",
        "bdo",
        "cite",
        "data",
        "del",
        "dfn",
        "em",
        "i",
        "ins",
        "kbd",
        "label",
        "mark",
        "q",
        "rp",
        "rt",
        "ruby",
        "s",
        "samp",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "time",
        "u",
        "var",
        "wbr",
    }
)


class _Text(HTMLParser):
    """Collect runs of visible text, one per block-level container.

    Two failure modes bracket this, and both were shipped before being caught.

    Concatenating the whole page invents text nobody sees:
    ``<p>~~left</p><p>right~~</p>`` becomes ``~~leftright~~`` and reports
    strikethrough present in neither element.

    Scanning each text node alone misses real markup: with the extension
    disabled, ``~~a *b* c~~`` renders as three text nodes and no single node
    holds a delimiter pair, so genuinely unrendered syntax passes.

    A run therefore spans inline markup and stops at block boundaries. Content
    inside ``code``, ``pre``, ``script`` and ``style`` is dropped, because
    ``[ ]`` and ``~~`` are ordinary characters there.
    """

    _SUPPRESSED = ("code", "pre", "script", "style")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # A counter, not a flag: `pre > code` is the ordinary fenced-block
        # shape, and a flag would resume at the inner `</code>`.
        self._suppress = 0
        self._current: list[str] = []
        self.runs: list[str] = []

    def _flush(self) -> None:
        if self._current:
            self.runs.append("".join(self._current))
            self._current = []

    def _boundary(self, tag: str) -> None:
        if tag not in INLINE_TAGS:
            self._flush()

    def handle_starttag(self, tag: str, _attrs: object) -> None:
        self._boundary(tag)
        if tag in self._SUPPRESSED:
            self._suppress += 1

    def handle_startendtag(self, tag: str, _attrs: object) -> None:
        self._boundary(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SUPPRESSED and self._suppress:
            self._suppress -= 1
        self._boundary(tag)

    def handle_data(self, data: str) -> None:
        if not self._suppress:
            self._current.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _page_text_runs(html: str) -> list[str]:
    parser = _Text()
    parser.feed(html)
    parser.close()
    return parser.runs


def main(argv: list[str]) -> int:
    """Scan a built site, returning 1 if any unrendered syntax is found."""
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
        for run in _page_text_runs(path.read_text(encoding="utf-8")):
            for syntax in PATTERNS:
                for match in syntax.pattern.finditer(run):
                    context = " ".join(run[max(0, match.start() - 40) : match.end() + 40].split())
                    failures.append(
                        f"{path.relative_to(site)}: unrendered {syntax.label} syntax "
                        f"{match.group(0)!r}\n"
                        f"    ...{context}...\n"
                        f"    fix: enable {syntax.extension} in mkdocs.yml, "
                        f"or use {syntax.alternative}"
                    )

    if failures:
        print(f"Unrendered Markdown syntax in {len(failures)} place(s):\n", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        # Each finding already carries its own fix. This explains why the build
        # was green, which is the part no individual finding can say, and does
        # not repeat "use supported syntax" at the reader.
        print(
            "\nThe MkDocs build passed because syntax from an unenabled extension "
            "is not an error:\nit is emitted as literal text, so the page ships "
            "with the markup visible to readers.\nSee the Documentation markup "
            "section of docs/CONTRIBUTING.md for which are enabled.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: no unrendered Markdown syntax across {scanned} page(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
