"""Tests for the ``YYYY.M.D-beta.N`` version format and its tooling.

Covers parsing, per-file storage in ``scripts/bump-version.py``, the
normalized-comparison Version Sync check, the ledger's handling of the dash
form, and the release workflow's pre-release detection.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_BUMP = _ROOT / "scripts" / "bump-version.py"
_GENERATOR = _ROOT / "scripts" / "generate-command-coverage.py"
_WORKFLOW = _ROOT / ".github" / "workflows" / "release-on-version-bump.yml"

# Mirrors the layout scripts/bump-version.py expects; (path, template).
_LAYOUT = {
    "app/config.yaml": 'version: "{v}"\n',
    "app/build.yaml": '  io.hass.version: "{v}"\n',
    "app/node/pyproject.toml": 'version = "{p}"\n',
    "app/node/src/openclaw_node/__init__.py": '    __version__ = "{p}"\n',
    "custom_components/openclaw_hass_node_assist/manifest.json": '  "version": "{v}"\n',
    "plugins/openclaw-hass-node-assist-tools/package.json": '  "version": "{v}",\n',
    "skills/openclaw-hass-node-skill/SKILL.md": "version: {v}\n",
}


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves its own module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bump() -> ModuleType:
    return _load(_BUMP, "bump_version_dash_tests")


@pytest.fixture(scope="module")
def generator() -> ModuleType:
    return _load(_GENERATOR, "generator_dash_tests")


def _tree(tmp_path: Path, verbatim: str, python: str) -> Path:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(_BUMP, scripts / "bump-version.py")
    for rel, template in _LAYOUT.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(template.format(v=verbatim, p=python), encoding="utf-8")
    return tmp_path


def _run(tree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(tree / "scripts" / "bump-version.py"), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=tree,
    )


@pytest.mark.parametrize(
    "version",
    [
        "2026.10.22-beta.1",
        "2026.10.22-beta.9",
        "2026.10.22-alpha.3",
        "2026.10.22-rc.1",
        "2026.1.5-beta.2",
        "2026.10.22",
        "2026.7.1",
    ],
)
def test_dash_form_parses(bump: ModuleType, version: str) -> None:
    assert bump._VERSION_RE.fullmatch(version)


@pytest.mark.parametrize(
    "version",
    [
        "2026.10.22-beta.0",
        "2026.10.22-beta.01",
        "2026.10.22-beta1",
        "2026.10.22-beta.",
        "2026.10.22-beta.1\n",
        "2026.10.22-Beta.1",
        "2026.10.22-gamma.1",
        "2026.10.022-beta.1",
        "2026.010.22-beta.1",
        "2026.10.22b1",
        "2026.10-beta.1",
        "v2026.10.22-beta.1",
    ],
)
def test_malformed_dash_forms_do_not_parse(bump: ModuleType, version: str) -> None:
    assert not bump._VERSION_RE.fullmatch(version)


def test_beta10_parses_but_is_refused_by_the_cap(bump: ModuleType, tmp_path: Path) -> None:
    """``-beta.10`` is well formed; the cap, not the parser, refuses it."""
    assert bump._VERSION_RE.fullmatch("2026.10.22-beta.10")
    tree = _tree(tmp_path, "2026.10.15b1", "2026.10.15b1")
    result = _run(tree, "2026.10.22-beta.10")
    assert result.returncode != 0
    assert "exceeds the beta.9 prerelease cap" in result.stderr


@pytest.mark.parametrize(
    ("dash", "pep440"),
    [
        ("2026.10.22-beta.1", "2026.10.22b1"),
        ("2026.10.22-alpha.3", "2026.10.22a3"),
        ("2026.10.22-rc.1", "2026.10.22rc1"),
        ("2026.10.22", "2026.10.22"),
        ("2026.10.15b1", "2026.10.15b1"),
    ],
)
def test_normalize_matches_pep440(
    bump: ModuleType, generator: ModuleType, dash: str, pep440: str
) -> None:
    assert bump.normalize_version(dash) == pep440
    assert generator._normalize_version(dash) == pep440
    packaging_version = pytest.importorskip("packaging.version")
    assert str(packaging_version.Version(dash)) == pep440


def test_normalize_rejects_garbage(bump: ModuleType) -> None:
    with pytest.raises(ValueError, match="unsupported version"):
        bump.normalize_version("2026.10.22-beta1")


def test_bump_stores_each_file_in_its_own_form(tmp_path: Path) -> None:
    """Dash form verbatim except pyproject.toml and the __init__.py fallback."""
    tree = _tree(tmp_path, "2026.10.15b1", "2026.10.15b1")
    result = _run(tree, "2026.10.22-beta.1")
    assert result.returncode == 0, result.stderr
    for rel, template in _LAYOUT.items():
        expected = template.format(v="2026.10.22-beta.1", p="2026.10.22b1")
        assert (tree / rel).read_text(encoding="utf-8") == expected, rel
    assert _run(tree, "--check").returncode == 0
    assert _run(tree, "--get").stdout.strip() == "2026.10.22-beta.1"


def test_bump_is_idempotent_across_forms(tmp_path: Path) -> None:
    """Bumping to the version already carried changes nothing, even though two
    files hold the normalized form rather than the literal argument."""
    tree = _tree(tmp_path, "2026.10.22-beta.1", "2026.10.22b1")
    result = _run(tree, "2026.10.22-beta.1")
    assert result.returncode == 0
    assert "no changes" in result.stdout


def test_beta_to_stable(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "2026.10.22-beta.2", "2026.10.22b2")
    assert _run(tree, "2026.10.22").returncode == 0
    assert _run(tree, "--check", "2026.10.22").returncode == 0


def test_check_accepts_the_old_form_everywhere(tmp_path: Path) -> None:
    """The tree before the switch must still pass Version Sync."""
    tree = _tree(tmp_path, "2026.10.15b1", "2026.10.15b1")
    assert _run(tree, "--check").returncode == 0


def test_check_compares_normalized_forms(tmp_path: Path) -> None:
    """``2026.10.22b1`` in pyproject is not drift against the dash form."""
    tree = _tree(tmp_path, "2026.10.22-beta.1", "2026.10.22b1")
    result = _run(tree, "--check", "2026.10.22-beta.1")
    assert result.returncode == 0, result.stderr


def test_check_requires_the_dash_form_verbatim_in_config_and_manifest(tmp_path: Path) -> None:
    """A normalized literal in a verbatim file is drift, not an equivalent."""
    tree = _tree(tmp_path, "2026.10.22-beta.1", "2026.10.22b1")
    for rel, template in (
        ("app/config.yaml", 'version: "{}"\n'),
        ("custom_components/openclaw_hass_node_assist/manifest.json", '  "version": "{}"\n'),
    ):
        path = tree / rel
        original = path.read_text(encoding="utf-8")
        path.write_text(template.format("2026.10.22b1"), encoding="utf-8")
        result = _run(tree, "--check")
        assert result.returncode == 1, rel
        assert "version drift" in result.stderr
        path.write_text(original, encoding="utf-8")


@pytest.mark.parametrize(
    "python_literal", ["2026.10.22-beta.1", "2026.10.22b2", "2026.10.15b1", "2026.10.22"]
)
def test_check_flags_a_python_side_mismatch(tmp_path: Path, python_literal: str) -> None:
    tree = _tree(tmp_path, "2026.10.22-beta.1", "2026.10.22b1")
    path = tree / "app/node/pyproject.toml"
    path.write_text(f'version = "{python_literal}"\n', encoding="utf-8")
    result = _run(tree, "--check")
    assert result.returncode == 1
    assert "version drift" in result.stderr


def test_check_flags_a_genuine_difference(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "2026.10.22-beta.1", "2026.10.22b1")
    (tree / "skills/openclaw-hass-node-skill/SKILL.md").write_text(
        "version: 2026.10.22-beta.2\n", encoding="utf-8"
    )
    assert _run(tree, "--check").returncode == 1


def test_check_rejects_a_wrong_expected_version(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "2026.10.22-beta.1", "2026.10.22b1")
    assert _run(tree, "--check", "2026.10.22-beta.2").returncode == 1


def test_cap_rolls_forward_to_beta1(tmp_path: Path) -> None:
    """Past beta.9 the date rolls forward and the counter resets."""
    tree = _tree(tmp_path, "2026.10.22-beta.9", "2026.10.22b9")
    assert _run(tree, "2026.10.22-beta.10").returncode != 0
    result = _run(tree, "2026.10.23-beta.1")
    assert result.returncode == 0, result.stderr
    assert _run(tree, "--get").stdout.strip() == "2026.10.23-beta.1"


@pytest.mark.parametrize(
    "version",
    ["2026.10.22-beta.1", "2026.10.22-alpha.3", "2026.10.22-rc.1", "2026.10.22", "2026.10.15b1"],
)
def test_ledger_accepts_released_versions(generator: ModuleType, version: str) -> None:
    generator._validate_first_shipped_in("example", version)


@pytest.mark.parametrize("version", ["2026.10.22-beta.0", "2026.10.22-beta1", "2026.10.022-beta.1"])
def test_ledger_rejects_malformed_dash_versions(generator: ModuleType, version: str) -> None:
    with pytest.raises(generator.LedgerError):
        generator._validate_first_shipped_in("example", version)


def test_ledger_sort_key_orders_dash_and_old_forms_together(generator: ModuleType) -> None:
    ordered = [
        "2026.10.15b1",
        "2026.10.22-alpha.1",
        "2026.10.22-beta.1",
        "2026.10.22-beta.2",
        "2026.10.22-beta.9",
        "2026.10.22-rc.1",
        "2026.10.22",
        "2026.10.23-beta.1",
    ]
    assert sorted(reversed(ordered), key=generator._version_sort_key) == ordered


def test_pre_release_regex_matches_the_dash_form() -> None:
    """Run the workflow's own `[[ =~ ]]` expression, not a re-typed copy."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r'if \[\[ "\$VERSION" =~ (\(.+\)\[0-9\]\+\$) \]\]; then', text)
    assert match, "pre-release detection line not found in the release workflow"
    pattern = match.group(1)

    def is_prerelease(version: str) -> bool:
        script = f'[[ "$1" =~ {pattern} ]]'
        return subprocess.run(["bash", "-c", script, "_", version], check=False).returncode == 0

    for version in (
        "2026.10.22-beta.1",
        "2026.10.22-alpha.3",
        "2026.10.22-rc.2",
        "2026.10.22-beta.9",
        "2026.10.15b1",  # old forms stay recognised
        "2026.6.8a8",
        "2026.9.12rc1",
        "2026.9.12.dev1",
    ):
        assert is_prerelease(version), version
    for version in ("2026.10.22", "2026.7.1", "1.0.0"):
        assert not is_prerelease(version), version


def _sources_for(
    generator: ModuleType, tmp_path: Path, verbatim: str, python: str
) -> tuple[tuple[Path, re.Pattern[str]], ...]:
    templates = (
        'version: "{v}"\n',
        '  io.hass.version: "{v}"\n',
        'version = "{p}"\n',
        '    __version__ = "{p}"\n',
        '  "version": "{v}"\n',
    )
    sources = []
    for index, (_, pattern) in enumerate(generator._VERSION_SOURCES):
        text = next(
            t.format(v=verbatim, p=python) for t in templates if pattern.search(t.format(v=0, p=0))
        )
        path = tmp_path / f"version-{index}"
        path.write_text(text, encoding="utf-8")
        sources.append((path, pattern))
    return tuple(sources)


def test_tracked_release_version_is_the_dash_form(
    generator: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The release version is app/config.yaml's literal; pyproject's normalized
    form is not drift."""
    sources = _sources_for(generator, tmp_path, "2026.10.22-beta.1", "2026.10.22b1")
    monkeypatch.setattr(generator, "_VERSION_SOURCES", sources)
    assert generator._tracked_release_version() == "2026.10.22-beta.1"


def test_tracked_release_version_still_flags_real_drift(
    generator: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources = _sources_for(generator, tmp_path, "2026.10.22-beta.1", "2026.10.22b2")
    monkeypatch.setattr(generator, "_VERSION_SOURCES", sources)
    with pytest.raises(generator.LedgerError, match="version drift"):
        generator._tracked_release_version()


@pytest.mark.parametrize(
    ("observed", "stale"),
    [("2026.10.22b1", False), ("2026.10.22-beta.1", False), ("2026.10.15b1", True)],
)
def test_evidence_staleness_compares_normalized_versions(
    generator: ModuleType, monkeypatch: pytest.MonkeyPatch, observed: str, stale: bool
) -> None:
    """A node reports the normalized version; the release is the dash form."""
    manual = generator._load_manual()
    command = sorted(manual["commands"])[
        0
    ]  # any command; not named, so the ledger does not index this file
    manual["commands"][command]["caller_observations"] = {
        "assist_wrapper": [
            {
                "method": "PRODUCTION-LIVE",
                "outcome": "pass",
                "source": "docs/evidence/sweep-2026-09-13.md",
                "observation": "Returned config dict.",
                "observed_at": "2026-09-13",
                "node_version": observed,
                "plugin_version": "not recorded",
            }
        ]
    }
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)
    monkeypatch.setattr(generator, "_tracked_release_version", lambda: "2026.10.22-beta.1")

    ledger = generator.build_ledger()

    row = next(r for r in ledger["rows"] if r["id"] == command)
    live = [
        e for e in row["callers"]["assist_wrapper"]["evidence"] if e["method"] == "PRODUCTION-LIVE"
    ]
    assert live
    assert all(item["stale"] is stale for item in live)
