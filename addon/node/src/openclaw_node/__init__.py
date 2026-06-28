"""OpenClaw HASS Node — gateway peripheral for Home Assistant.

This package implements an OpenClaw gateway node that runs inside a Home
Assistant add-on (or standalone Docker container) and exposes filesystem,
shell, HA control, and Assist conversation surfaces to the OpenClaw gateway.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("openclaw-node")
except (
    PackageNotFoundError
):  # pragma: no cover — only hit when running from a non-installed source tree
    # Fallback for editable/source-only runs where the package metadata is not
    # available. The CI gate `test_version_sources_match` keeps this in sync
    # with pyproject.toml, addon/config.yaml, build.yaml, and manifest.json.
    __version__ = "2026.6.28b4"
