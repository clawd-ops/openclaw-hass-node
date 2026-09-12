"""Explicit fixtures for isolated HA configuration API-adapter tests."""

from __future__ import annotations

import importlib
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _isolate_dispatcher_registry() -> Iterator[None]:
    """Snapshot and restore the dispatcher command registry per test.

    Several tests call ``register_handler`` (which mutates the module-level
    ``_REGISTRY`` dict directly) without teardown. Without isolation those
    entries leak into every subsequent test in the same worker process and
    corrupt the parity gate at ``test_advertised_matches_registry``. This
    autouse fixture restores the registry to its pre-test state so no
    filter or name-based escape hatch is needed in the gate.
    """
    from openclaw_node.commands import dispatcher

    snapshot = dict(dispatcher._REGISTRY)
    try:
        yield
    finally:
        dispatcher._REGISTRY.clear()
        dispatcher._REGISTRY.update(snapshot)


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
