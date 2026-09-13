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

# Pattern, and the extension that would have consumed it. The extension name is
# reported so the failure tells the reader what to enable rather than only that
# something is wrong.
PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("task list", re.compile(r"(?<![\w`])\[[ xX]\](?=\s)"), "pymdownx.tasklist"),
    ("strikethrough", re.compile(r"~~[^\s~][^~]*~~"), "pymdownx.tilde"),
    ("highlight", re.compile(r"==[^\s=][^=]*=="), "pymdownx.caret (mark)"),
)

# Directories of build output that are not authored documentation.
SKIP_DIRS = frozenset({"assets", "search"})


class _Text(HTMLParser):
    """Collect page text, dropping anything inside code, pre, or script."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._suppress = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, _attrs: object) -> None:
        if tag in ("code", "pre", "script", "style"):
            self._suppress += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("code", "pre", "script", "style") and self._suppress:
            self._suppress -= 1

    def handle_data(self, data: str) -> None:
        if not self._suppress:
            self.chunks.append(data)


def _page_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return "".join(parser.chunks)


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
        text = _page_text(path.read_text(encoding="utf-8"))
        for label, pattern, extension in PATTERNS:
            for match in pattern.finditer(text):
                context = " ".join(text[max(0, match.start() - 40) : match.end() + 40].split())
                failures.append(
                    f"{path.relative_to(site)}: unrendered {label} syntax "
                    f"{match.group(0)!r} (enable {extension})\n    ...{context}..."
                )

    if failures:
        print(f"Unrendered Markdown syntax in {len(failures)} place(s):\n", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        print(
            "\nThe build passed because unsupported syntax is not an error: it is "
            "emitted as literal text.\nEnable the extension in mkdocs.yml, or rewrite "
            "the source to use supported syntax.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: no unrendered Markdown syntax across {scanned} page(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
