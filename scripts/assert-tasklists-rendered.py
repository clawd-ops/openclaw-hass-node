#!/usr/bin/env python3
"""Fail if the built roadmap has no rendered task-list controls.

`mkdocs build --strict` cannot catch a missing Markdown extension. Syntax from
an extension the site does not enable is not an error: Python-Markdown does not
recognise it, emits it as literal text, and the build stays green. That is how
100 task-list lines across `COMPLETION-ROADMAP.md` and `CONTRIBUTING.md` shipped
as literal `[x]` and `[ ]`.

## Why this checks the build output for the extension's own markup

Four earlier revisions tried to decide, from either the rendered HTML or the
Markdown source, whether a given `[ ]` *should* have been consumed. Every one
was wrong, in both directions, because that question needs Python-Markdown's
grammar:

* `- \\[ \\] literal choice` escapes the brackets deliberately and renders as
  `<li>[ ] literal choice</li>`, byte-identical to an unconsumed marker. So no
  check on rendered text can tell a missing extension from an intended literal.
* `-  [ ] item`, `-\\t[ ] item`, `> - [ ] item` and `1. [ ] item` all render as
  task controls, while an indented `    - [ ] example` renders as code. A source
  regex that does not reimplement the grammar gets all five wrong.

This asks a question with no such ambiguity: **did `pymdownx.tasklist` run?**
The extension emits its own `task-list-item` class, which nothing else produces
and no authored Markdown can fake. Absence of that class on a page known to use
task lists means the extension did not run, whatever the reason.

It also cannot be fooled by configuration that looks right but is not. An
earlier version read the first `markdown_extensions:` block out of `mkdocs.yml`
by regex; a second such key later in the file wins in MkDocs, so the check
passed while the roadmap rendered zero controls. Reading the build output
sidesteps the effective-configuration question entirely.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Pages known to use task-list syntax. A page listed here that renders no
# controls is the regression this exists to catch.
PAGES = ("COMPLETION-ROADMAP/index.html", "CONTRIBUTING/index.html")

# Emitted by pymdownx.tasklist and by nothing else.
MARKER = "task-list-item"


def main(argv: list[str]) -> int:
    """Return 1 if any known task-list page rendered no controls."""
    site = Path(argv[1] if len(argv) > 1 else "site")
    if not site.is_dir():
        print(f"error: {site} is not a directory; run `mkdocs build` first", file=sys.stderr)
        return 2

    failures: list[str] = []
    for relative in PAGES:
        page = site / relative
        if not page.is_file():
            failures.append(f"{relative}: page missing from the build")
            continue
        count = page.read_text(encoding="utf-8").count(MARKER)
        print(f"  {relative}: {count} task-list controls")
        if count == 0:
            failures.append(f"{relative}: no rendered task-list controls")

    if failures:
        print("\nTask lists did not render:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        print(
            "\nThese pages use task-list syntax, so their `- [ ]` and `- [x]` items\n"
            "shipped to readers as literal brackets. The MkDocs build passes "
            "regardless:\nsyntax from an extension that is not enabled is emitted as "
            "literal text.\nFix: ensure pymdownx.tasklist is enabled in mkdocs.yml.",
            file=sys.stderr,
        )
        return 1

    print("OK: task lists rendered on every page known to use them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
