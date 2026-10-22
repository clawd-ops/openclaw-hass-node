"""CI gate: every place that carries the version string must stay in lock-step.

There are five sources of truth (``scripts/bump-version.py`` tracks two more:
the plugin ``package.json`` and the skill ``SKILL.md``). The dash form
(``2026.10.22-beta.1``) is carried verbatim by:

- ``app/config.yaml`` — Home Assistant add-on metadata
- ``app/build.yaml`` — Supervisor build label
- ``custom_components/openclaw_hass_node_assist/manifest.json`` — HACS integration version

and its PEP 440 normalized form (``2026.10.22b1``) by the two Python-side
files, which is what packaging tools and ``importlib.metadata`` report:

- ``app/node/pyproject.toml`` — Python package version
- ``app/node/src/openclaw_node/__init__.py`` — fallback literal used when
  ``importlib.metadata`` cannot resolve the package (source/dev runs)

Past versions drifted between these (e.g. UAT-PLAN expecting 2026.6.0 while
code was on 2026.6.8). The user called it cosmetic at the time, but a
mismatched version in startup logs is exactly the wrong signal during an
pre-1.0 install when the operator is trying to figure out which build they
are looking at. This test makes the drift fail CI loudly.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tomllib
from pathlib import Path
from types import ModuleType

# The repo root sits four levels above this test file:
# app/node/tests/test_version_sync.py → app/node/tests → app/node → app → repo
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _bump_version_module() -> ModuleType:
    path = _REPO_ROOT / "scripts" / "bump-version.py"
    spec = importlib.util.spec_from_file_location("bump_version_under_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves its own module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _read_pyproject_version() -> str:
    raw = (_REPO_ROOT / "app" / "node" / "pyproject.toml").read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    project = data.get("project")
    assert isinstance(project, dict), "pyproject.toml missing [project] table"
    version = project.get("version")
    assert isinstance(version, str), "pyproject.toml [project].version must be a string"
    return version


def _read_init_fallback() -> str:
    """Return the literal fallback string in ``__init__.py``.

    We avoid importing the package because the importlib.metadata path would
    short-circuit to the installed version and we explicitly want to assert
    the source-tree fallback also stays in sync.
    """
    text = (_REPO_ROOT / "app" / "node" / "src" / "openclaw_node" / "__init__.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r'__version__ = "([^"]+)"', text)
    assert match, "no __version__ literal fallback found in __init__.py"
    return match.group(1)


def _read_addon_config_version() -> str:
    text = (_REPO_ROOT / "app" / "config.yaml").read_text(encoding="utf-8")
    match = re.search(r'^version:\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "no top-level version in app/config.yaml"
    return match.group(1)


def _read_build_yaml_version() -> str:
    text = (_REPO_ROOT / "app" / "build.yaml").read_text(encoding="utf-8")
    match = re.search(r'io\.hass\.version:\s*"([^"]+)"', text)
    assert match, "no io.hass.version in app/build.yaml"
    return match.group(1)


def _read_manifest_version() -> str:
    manifest_path = _REPO_ROOT / "custom_components" / "openclaw_hass_node_assist" / "manifest.json"
    raw = manifest_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    version = data.get("version")
    assert isinstance(version, str), "manifest.json version must be a string"
    return version


def test_version_sources_match() -> None:
    """All sources carry the same version, each in its own form.

    Verbatim sources must be identical; the Python-side sources must hold the
    PEP 440 normalized form of that literal. Comparing normalized forms is what
    keeps ``2026.10.22b1`` from reading as drift against ``2026.10.22-beta.1``.
    """
    normalize = _bump_version_module().normalize_version
    verbatim = {
        "app/config.yaml": _read_addon_config_version(),
        "app/build.yaml": _read_build_yaml_version(),
        "custom_components/.../manifest.json": _read_manifest_version(),
    }
    assert len(set(verbatim.values())) == 1, (
        f"version drift across sources: {verbatim}. "
        "Bump them all together — there is no other valid state."
    )
    canonical = next(iter(verbatim.values()))
    python_side = {
        "pyproject.toml": _read_pyproject_version(),
        "__init__.py fallback": _read_init_fallback(),
    }
    assert set(python_side.values()) == {normalize(canonical)}, (
        f"Python-side sources {python_side} must hold the PEP 440 form "
        f"{normalize(canonical)!r} of {canonical!r}."
    )


def test_installed_version_matches_pyproject() -> None:
    """The installed package metadata must match ``pyproject.toml``.

    This is the failure mode the owner saw in earlier runs: the package was
    installed at version X, pyproject was bumped to Y, nobody reinstalled,
    and the startup log kept reporting X. We query
    :func:`importlib.metadata.version` directly so a missing dist
    (``PackageNotFoundError``) fails this test loudly instead of falling
    through to the source literal in ``__init__.py``. The CI install
    (``uv sync``) has already happened by the time pytest runs, so the
    metadata is expected to be present.
    """
    from importlib.metadata import version as _pkg_version

    assert _pkg_version("openclaw-node") == _read_pyproject_version()


def test_prerelease_tag_present() -> None:
    """Pre-1.0 releases must carry a PEP 440 prerelease marker.

    The project is explicitly pre-1.0 (currently on the beta track;
    the earlier alpha track ran through 2026.6.8a16). pyproject.toml holds the
    PEP 440 form of ``-alpha.N``/``-beta.N``/``-rc.N``: ``aN`` (alpha), ``bN``
    (beta), ``rcN`` (plus the legacy ``.devN``). Final
    releases without any of these are rejected so the version string in
    startup logs never claims false stability.
    """
    version = _read_pyproject_version()
    assert re.search(r"(a\d+|b\d+|rc\d+|\.dev\d+)$", version), (
        f"version {version!r} has no pre-release marker, pre-1.0 builds must "
        "carry one of aN/bN/rcN/.devN until the project ships a 1.0."
    )
