"""Documentation consistency guards.

Phase 2 doc-architecture reshape (PR after #168/#169) made
`docs/COMMAND-SURFACE.md` the canonical source of truth for the command
registry. These tests fail loudly if drift creeps back into the docs:

1. The command count claimed in `COMMAND-SURFACE.md` matches the live
   `_REGISTRY` in `commands/dispatcher.py`.
2. Non-canonical internal docs do NOT hard-code a `<N> commands` claim
   (they should link to `COMMAND-SURFACE.md` instead).

User-facing docs (`README.md`, `docs/INSTALL.md`, `docs/UAT-PLAN.md`)
are exempt — they intentionally stay self-contained so a user landing
cold does not have to chase links.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DISPATCHER = _REPO_ROOT / "addon" / "node" / "src" / "openclaw_node" / "commands" / "dispatcher.py"

_CANONICAL_DOC = _REPO_ROOT / "docs" / "COMMAND-SURFACE.md"

# Non-canonical docs that must NOT hardcode "N commands". They should
# either omit the count or link to COMMAND-SURFACE.md.
_NON_CANONICAL_DOCS = (
    _REPO_ROOT / "docs" / "STATUS.md",
    _REPO_ROOT / "docs" / "MEMORY.md",
    _REPO_ROOT / "docs" / "OVERVIEW.md",
)

# User-facing files exempt from the no-hardcoded-count rule. These stay
# self-contained on purpose (Rob's explicit directive: users shouldn't
# have to chase links to install the project).
_USER_FACING_EXEMPT = (
    _REPO_ROOT / "README.md",
    _REPO_ROOT / "docs" / "INSTALL.md",
    _REPO_ROOT / "docs" / "UAT-PLAN.md",
)

_COUNT_PATTERN = re.compile(r"\b(\d{2,3})\s+commands?\b", re.IGNORECASE)


def _live_command_count() -> int:
    """Parse the `_REGISTRY` dict literal from dispatcher.py source.

    Reading the source (rather than `len(_REGISTRY)`) avoids
    contamination from other tests that call `register_handler` and
    mutate the runtime registry without reverting.
    """
    tree = ast.parse(_DISPATCHER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_REGISTRY"
            and isinstance(node.value, ast.Dict)
        ):
            return len(node.value.keys)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "_REGISTRY"
                    and isinstance(node.value, ast.Dict)
                ):
                    return len(node.value.keys)
    raise AssertionError("Could not locate _REGISTRY dict literal in dispatcher.py")


def test_canonical_doc_count_matches_registry() -> None:
    """COMMAND-SURFACE.md must claim the actual registered count."""
    text = _CANONICAL_DOC.read_text(encoding="utf-8")
    live = _live_command_count()
    matches = {int(m.group(1)) for m in _COUNT_PATTERN.finditer(text)}
    assert live in matches, (
        f"docs/COMMAND-SURFACE.md does not claim {live} commands; "
        f"saw {matches or 'no count'}. Update the canonical doc."
    )


def test_non_canonical_docs_do_not_hardcode_count() -> None:
    """Internal docs must not hardcode '<N> commands' — link instead."""
    violations: list[str] = []
    for doc in _NON_CANONICAL_DOCS:
        if not doc.exists():
            continue
        text = doc.read_text(encoding="utf-8")
        for match in _COUNT_PATTERN.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            violations.append(
                f"{doc.relative_to(_REPO_ROOT)}:{line_no} hardcodes "
                f"'{match.group(0)}' — link to docs/COMMAND-SURFACE.md "
                f"instead, or exempt the doc in test_doc_consistency.py."
            )
    assert not violations, (
        "Hardcoded command counts found in non-canonical docs:\n  " + "\n  ".join(violations)
    )


def test_user_facing_exemptions_exist() -> None:
    """Guard against silently dropping the user-facing exemption list."""
    missing = [p for p in _USER_FACING_EXEMPT if not p.exists()]
    assert not missing, (
        "Exempt paths referenced in test_doc_consistency.py no longer "
        f"exist: {missing}. Update the exemption list."
    )
