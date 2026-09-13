"""Explicit fixtures for isolated HA configuration API-adapter tests."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def trusted_config_approval_adapter_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate dormant API adapters from the currently fail-closed boundary.

    This is not a shipped approval path or evidence that mutations work today.
    Only explicitly marked adapter tests may replace the trusted seam; the
    cross-command authorization regressions use the real boundary unchanged.
    """
    from openclaw_node.commands.dispatcher import _REGISTRY

    for command in _REGISTRY:
        if command.startswith("ha.config."):
            domain = command.removeprefix("ha.config.")
            module = importlib.import_module(f"openclaw_node.commands.ha_config_{domain}")
            monkeypatch.setattr(module, "require_config_mutation_approval", lambda *_args: None)
