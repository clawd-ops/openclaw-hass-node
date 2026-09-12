"""Regression tests for scripts/check-active-docs-schema.py.

The reviewer for #283 noted that a pure count/tally check cannot catch
a same-count substitution (swap one advertised command for a stale
name) or a duplicated entry in the operator-facing
`gateway.nodes.commands.allow` example. These tests copy the repo into
a scratch tree, mutate `docs/INSTALL.md`, and re-run the gate to prove
each drift shape is rejected.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_REL = Path("scripts/check-active-docs-schema.py")


def _run_gate(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(cwd / _SCRIPT_REL)],
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
    )


def _stage_repo(tmp_path: Path) -> Path:
    """Copy the minimum tree the gate reads into an isolated scratch dir."""
    scratch = tmp_path / "repo"
    needed = (
        "scripts/check-active-docs-schema.py",
        "app/node/src/openclaw_node/gateway_ws.py",
        "README.md",
        "docs/INSTALL.md",
        "docs/CONTRIBUTING.md",
        "docs/operations/UAT-PLAN.md",
        "docs/design/COMMAND-TIERS.md",
        "plugins/openclaw-hass-node-assist-tools/README.md",
    )
    for rel in needed:
        src = _ROOT / rel
        dst = scratch / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return scratch


def test_gate_passes_on_pristine_tree(tmp_path: Path) -> None:
    scratch = _stage_repo(tmp_path)
    result = _run_gate(scratch)
    assert result.returncode == 0, result.stderr + result.stdout


def test_same_count_substitution_fails(tmp_path: Path) -> None:
    """Swap `ha.get_state` (real) for `ha.get_state_stale` (fake). Total unchanged."""
    scratch = _stage_repo(tmp_path)
    install = scratch / "docs/INSTALL.md"
    original = install.read_text()
    mutated = re.sub(r'"ha\.get_state"', '"ha.get_state_stale"', original, count=1)
    assert mutated != original, "test setup failed to mutate a real command name"
    install.write_text(mutated)

    result = _run_gate(scratch)
    assert result.returncode == 1
    combined = result.stderr + result.stdout
    assert "ha.get_state_stale" in combined
    assert "ha.get_state" in combined


def test_duplicate_entry_fails(tmp_path: Path) -> None:
    """Duplicate `ping` in the JSON block — set equality alone would miss it."""
    scratch = _stage_repo(tmp_path)
    install = scratch / "docs/INSTALL.md"
    original = install.read_text()
    mutated = original.replace('"ping",', '"ping", "ping",', 1)
    assert mutated != original, "test setup failed to duplicate a command"
    install.write_text(mutated)

    result = _run_gate(scratch)
    assert result.returncode == 1
    combined = result.stderr + result.stdout
    assert "duplicate" in combined.lower()
    assert "ping" in combined


def test_missing_command_fails(tmp_path: Path) -> None:
    """Removing an advertised command from the example must be rejected."""
    scratch = _stage_repo(tmp_path)
    install = scratch / "docs/INSTALL.md"
    original = install.read_text()
    mutated = re.sub(r'\s*"ha\.check_config",', "", original, count=1)
    assert mutated != original, "test setup failed to remove a real command"
    install.write_text(mutated)

    result = _run_gate(scratch)
    assert result.returncode == 1
    combined = result.stderr + result.stdout
    assert "ha.check_config" in combined


@pytest.mark.parametrize("missing_file", ["docs/INSTALL.md"])
def test_missing_json_block_fails(tmp_path: Path, missing_file: str) -> None:
    scratch = _stage_repo(tmp_path)
    (scratch / missing_file).write_text("# INSTALL\n\nno json block here\n")
    result = _run_gate(scratch)
    assert result.returncode != 0
