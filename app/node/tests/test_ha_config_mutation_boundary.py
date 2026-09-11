"""Prove every HA config mutation is refused before any HA request."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openclaw_node import ha_client
from openclaw_node.commands.dispatcher import _REGISTRY, dispatch_async
from openclaw_node.commands.ha_config_helpers import _HELPER_TYPES

# Explicit command/action inventory: changing the shipped config surface must
# also extend the zero-request regression matrix, not silently skip a new action.
CONFIG_ACTIONS = {
    "automation": ({"get"}, {"save", "delete"}),
    "script": ({"get"}, {"save", "delete"}),
    "scene": ({"get"}, {"save", "delete"}),
    "lovelace": ({"get", "dashboards_list", "resources_list"}, {"save", "resources_create"}),
    "helpers": ({"list"}, {"create", "update", "delete"}),
    "area_registry": ({"list"}, {"create", "update", "delete"}),
    "device_registry": ({"list"}, {"update"}),
    "entity_registry": ({"list", "get"}, {"update", "remove"}),
    "config_entries": ({"get"}, {"disable", "enable"}),
}
MUTATIONS = [
    (domain, action)
    for domain, (_, mutations) in CONFIG_ACTIONS.items()
    for action in sorted(mutations)
]
UNTRUSTED_AUTHORIZATION = [
    {},
    {"proposal_id": None},
    {"proposal_id": ""},
    {"proposal_id": " "},
    {"proposal_id": "direct"},
    {"proposal_id": 123},
    {"proposal_id": {"status": "approved"}},
    {"proposal_id": "p1"},
    {"proposal_id": "d77abef3-a3b8-449d-8fd6-36aa411349dc"},
    {
        "proposal_id": "approved-proposal",
        "approved": True,
        "actor": "operator",
        "role": "admin",
        "admin_token": "untrusted-caller-token",
        "agent_bridge": False,
        "dry_run": False,
    },
]


@pytest.fixture
def no_ha_requests(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[AsyncMock]]:
    """Observe both module aliases and central client entrypoints."""
    mocks = []
    modules = [ha_client] + [
        importlib.import_module(f"openclaw_node.commands.ha_config_{domain}")
        for domain in CONFIG_ACTIONS
    ]
    for module in modules:
        for name in ("ha_get", "ha_post", "ha_delete", "ha_ws_call"):
            if hasattr(module, name):
                mock = AsyncMock(name=f"{module.__name__}.{name}")
                monkeypatch.setattr(module, name, mock)
                mocks.append(mock)
    yield mocks
    for mock in mocks:
        mock.assert_not_called()
        mock.assert_not_awaited()


def mutation_params(action: str) -> dict[str, Any]:
    """Supply valid target and payload fields for every config command."""
    return {
        "action": action,
        "id": "example",
        "config": {"alias": "Example", "views": []},
        "url_path": "example",
        "url": "/local/example.js",
        "res_type": "module",
        "helper_type": "input_boolean",
        "input_boolean_id": "example",
        "name": "Example",
        "attrs": {"name": "Updated"},
        "area_id": "example",
        "device_id": "example",
        "entity_id": "input_boolean.example",
        "entry_id": "example",
    }


def test_mutation_inventory_covers_every_config_command_and_action() -> None:
    assert {name for name in _REGISTRY if name.startswith("ha.config.")} == {
        f"ha.config.{domain}" for domain in CONFIG_ACTIONS
    }
    for domain, (reads, mutations) in CONFIG_ACTIONS.items():
        module = importlib.import_module(f"openclaw_node.commands.ha_config_{domain}")
        assert reads.isdisjoint(mutations)
        assert reads | mutations == module._ACTIONS


@pytest.mark.parametrize(("domain", "action"), MUTATIONS)
@pytest.mark.parametrize("authorization", UNTRUSTED_AUTHORIZATION)
@pytest.mark.parametrize("route", ["handler", "dispatcher"])
async def test_config_mutations_never_contact_ha(
    domain: str,
    action: str,
    authorization: dict[str, Any],
    route: str,
    no_ha_requests: list[AsyncMock],
) -> None:
    command = f"ha.config.{domain}"
    params = {**mutation_params(action), **authorization}
    if route == "dispatcher":
        result = await dispatch_async(command, params)
    else:
        module = importlib.import_module(f"openclaw_node.commands.ha_config_{domain}")
        result = await getattr(module, f"handle_ha_config_{domain}")(params)
    assert result["ok"] is False
    assert result["error"] == "PROPOSAL_REQUIRED"
    assert "trusted approval verifier" in result["message"]
    assert "proposal_id alone is not authorization" in result["message"]


@pytest.mark.parametrize(("domain", "action"), MUTATIONS)
async def test_mutation_denial_precedes_payload_validation(
    domain: str, action: str, no_ha_requests: list[AsyncMock]
) -> None:
    # Missing targets, whitespace-normalized actions and an apparent approval
    # must still fail closed, not reach a fallback request.
    result = await dispatch_async(
        f"ha.config.{domain}", {"action": f" {action} ", "proposal_id": "p1"}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


@pytest.mark.parametrize("helper_type", sorted(_HELPER_TYPES))
@pytest.mark.parametrize("action", ["create", "update", "delete"])
async def test_all_helper_namespaces_fail_closed(
    helper_type: str, action: str, no_ha_requests: list[AsyncMock]
) -> None:
    result = await dispatch_async(
        "ha.config.helpers",
        {
            **mutation_params(action),
            "helper_type": helper_type,
            f"{helper_type}_id": "example",
            "proposal_id": "p1",
        },
    )
    assert result["error"] == "PROPOSAL_REQUIRED"
