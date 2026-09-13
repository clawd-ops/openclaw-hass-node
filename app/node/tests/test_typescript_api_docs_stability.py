"""Regression test for #296: generated TypeScript API docs must not embed source lines.

TypeDoc's markdown output used to emit a `Defined in:` block linking to an
absolute line number. Any unrelated edit that shifted a symbol — a new import, a
comment, a whitespace tweak — invalidated those references, so the drift check
in `scripts/check-typescript-api-docs.sh` turned main red across every open PR
until someone regenerated. The failure was decoupled from the change that caused
it, and the stale part carried no meaning: the docs were still semantically
correct, only the line numbers were wrong.

Filtering the references out of the drift check would have kept links that point
at the wrong line and stopped anything from ever correcting them. Removing them
at the source is both simpler and more accurate, which is why `typedoc.json`
sets `disableSources`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_GENERATED = _ROOT / "docs" / "reference" / "api" / "typescript" / "generated"
_TYPEDOC_CONFIG = _ROOT / "plugins" / "openclaw-hass-node-assist-tools" / "typedoc.json"

# `#L123` or `path.ts:123` — the two shapes TypeDoc emitted.
_SOURCE_LINE_REFERENCE = re.compile(r"#L\d+|\.ts:\d+")


def test_typedoc_disables_source_references() -> None:
    """The generator, not a post-filter, is what keeps line numbers out."""
    config = json.loads(_TYPEDOC_CONFIG.read_text(encoding="utf-8"))

    assert config.get("disableSources") is True


def test_generated_api_docs_contain_no_source_line_references() -> None:
    """No generated page may pin a symbol to a line number."""
    pages = sorted(_GENERATED.rglob("*.md"))
    assert pages, "expected generated TypeScript API pages to exist"

    offenders = [
        f"{page.relative_to(_ROOT)}:{number}"
        for page in pages
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), start=1)
        if _SOURCE_LINE_REFERENCE.search(line)
    ]

    assert not offenders, "generated API docs pin symbols to source lines: " + ", ".join(offenders)
