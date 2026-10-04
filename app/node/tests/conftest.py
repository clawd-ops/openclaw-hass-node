"""Explicit fixtures for isolated HA configuration API-adapter tests."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def trusted_config_approval_adapter_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate API-adapter tests from the approval-marker boundary.

    Only explicitly marked adapter tests may replace the seam; the
    authorization regressions use the real boundary unchanged.
    """
    from openclaw_node.commands.dispatcher import _REGISTRY

    for command in _REGISTRY:
        if command.startswith("ha.config."):
            domain = command.removeprefix("ha.config.")
            module = importlib.import_module(f"openclaw_node.commands.ha_config_{domain}")
            monkeypatch.setattr(module, "consume_approval_marker", lambda *_args: None)
