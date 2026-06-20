"""CI gate: every place that carries the version string must stay in lock-step.

There are five sources of truth that all have to match exactly:

- ``addon/node/pyproject.toml`` — Python package version (PEP 440)
- ``addon/node/src/openclaw_node/__init__.py`` — fallback literal used when
  ``importlib.metadata`` cannot resolve the package (source/dev runs)
- ``addon/config.yaml`` — Home Assistant add-on metadata
- ``addon/build.yaml`` — Supervisor build label
- ``custom_components/openclaw_gateway/manifest.json`` — HACS shim version

Past versions drifted between these (e.g. UAT-PLAN expecting 2026.6.0 while
code was on 2026.6.8). The user called it cosmetic at the time, but a
mismatched version in startup logs is exactly the wrong signal during an
pre-1.0 install when the operator is trying to figure out which build they
are looking at. This test makes the drift fail CI loudly.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

# The repo root sits four levels above this test file:
# addon/node/tests/test_version_sync.py → addon/node/tests → addon/node → addon → repo
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _read_pyproject_version() -> str:
    raw = (_REPO_ROOT / "addon" / "node" / "pyproject.toml").read_bytes()
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
    text = (_REPO_ROOT / "addon" / "node" / "src" / "openclaw_node" / "__init__.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r'__version__ = "([^"]+)"', text)
    assert match, "no __version__ literal fallback found in __init__.py"
    return match.group(1)


def _read_addon_config_version() -> str:
    text = (_REPO_ROOT / "addon" / "config.yaml").read_text(encoding="utf-8")
    match = re.search(r'^version:\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "no top-level version in addon/config.yaml"
    return match.group(1)


def _read_build_yaml_version() -> str:
    text = (_REPO_ROOT / "addon" / "build.yaml").read_text(encoding="utf-8")
    match = re.search(r'io\.hass\.version:\s*"([^"]+)"', text)
    assert match, "no io.hass.version in addon/build.yaml"
    return match.group(1)


def _read_manifest_version() -> str:
    raw = (_REPO_ROOT / "custom_components" / "openclaw_gateway" / "manifest.json").read_text(
        encoding="utf-8"
    )
    data = json.loads(raw)
    version = data.get("version")
    assert isinstance(version, str), "manifest.json version must be a string"
    return version


def test_version_sources_match() -> None:
    """All five version sources must carry the exact same string."""
    versions = {
        "pyproject.toml": _read_pyproject_version(),
        "__init__.py fallback": _read_init_fallback(),
        "addon/config.yaml": _read_addon_config_version(),
        "addon/build.yaml": _read_build_yaml_version(),
        "custom_components/.../manifest.json": _read_manifest_version(),
    }
    distinct = set(versions.values())
    assert len(distinct) == 1, (
        f"version drift across sources: {versions}. "
        "Bump them all together — there is no other valid state."
    )


def test_installed_version_matches_pyproject() -> None:
    """The installed package metadata must match ``pyproject.toml``.

    This is the failure mode Rob saw in earlier runs: the package was
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
    the earlier alpha track ran through 2026.6.8a16). PEP 440 markers
    we accept: ``aN`` (alpha), ``bN`` (beta), ``rcN``, ``.devN``. Final
    releases without any of these are rejected so the version string in
    startup logs never claims false stability.
    """
    version = _read_pyproject_version()
    assert re.search(r"(a\d+|b\d+|rc\d+|\.dev\d+)$", version), (
        f"version {version!r} has no pre-release marker, pre-1.0 builds must "
        "carry one of aN/bN/rcN/.devN until the project ships a 1.0."
    )
