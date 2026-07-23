"""Tests for the three ha.config.*_registry commands."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from openclaw_node.commands.dispatcher import _REGISTRY as DISPATCH
from openclaw_node.commands.ha_config_area_registry import (
    handle_ha_config_area_registry,
)
from openclaw_node.commands.ha_config_device_registry import (
    handle_ha_config_device_registry,
)
from openclaw_node.commands.ha_config_entity_registry import (
    handle_ha_config_entity_registry,
)
from openclaw_node.ha_client import HAClientError

pytestmark = pytest.mark.asyncio


# -----------------------------------------------------------------------
# area_registry
# -----------------------------------------------------------------------


def test_area_registered() -> None:
    assert DISPATCH["ha.config.area_registry"] is handle_ha_config_area_registry


async def test_area_missing_action() -> None:
    assert (await handle_ha_config_area_registry({}))["error"] == "INVALID_PARAM"


@pytest.mark.parametrize("bad_action", [42, [], {}, ["list"], {"action": "list"}])
async def test_area_action_wrong_type(bad_action: object) -> None:
    result = await handle_ha_config_area_registry({"action": bad_action})
    assert result["error"] == "INVALID_PARAM"


async def test_area_unknown_action() -> None:
    result = await handle_ha_config_area_registry({"action": "bogus"})
    assert result["error"] == "INVALID_PARAM"


async def test_area_list_happy() -> None:
    mock = AsyncMock(return_value=[{"area_id": "a1"}])
    with patch("openclaw_node.commands.ha_config_area_registry.ha_ws_call", mock):
        result = await handle_ha_config_area_registry({"action": "list"})
    assert result == {"ok": True, "count": 1, "areas": [{"area_id": "a1"}]}
    mock.assert_awaited_once_with("config/area_registry/list")


async def test_area_list_bad_response() -> None:
    mock = AsyncMock(return_value={"not": "a list"})
    with patch("openclaw_node.commands.ha_config_area_registry.ha_ws_call", mock):
        assert (await handle_ha_config_area_registry({"action": "list"}))[
            "error"
        ] == "HA_BAD_RESPONSE"


async def test_area_list_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_AUTH", "401"))
    with patch("openclaw_node.commands.ha_config_area_registry.ha_ws_call", mock):
        assert (await handle_ha_config_area_registry({"action": "list"}))["error"] == "HA_AUTH"


@pytest.mark.parametrize("action", ["create", "update", "delete"])
async def test_area_mutating_needs_proposal(action: str) -> None:
    params: dict[str, Any] = {
        "action": action,
        "name": "x",
        "area_id": "a1",
        "attrs": {"name": "y"},
    }
    assert (await handle_ha_config_area_registry(params))["error"] == "PROPOSAL_REQUIRED"


async def test_area_direct_proposal_refused() -> None:
    result = await handle_ha_config_area_registry(
        {"action": "create", "name": "x", "proposal_id": "direct"}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_area_create_happy() -> None:
    mock = AsyncMock(return_value={"area_id": "new"})
    with patch("openclaw_node.commands.ha_config_area_registry.ha_ws_call", mock):
        result = await handle_ha_config_area_registry(
            {"action": "create", "name": "Kitchen", "proposal_id": "p1"}
        )
    assert result["ok"] is True
    mock.assert_awaited_once_with("config/area_registry/create", {"name": "Kitchen"})


async def test_area_create_missing_name() -> None:
    result = await handle_ha_config_area_registry({"action": "create", "proposal_id": "p"})
    assert result["error"] == "MISSING_PARAM"


async def test_area_update_happy() -> None:
    mock = AsyncMock(return_value={"area_id": "a1", "name": "renamed"})
    with patch("openclaw_node.commands.ha_config_area_registry.ha_ws_call", mock):
        result = await handle_ha_config_area_registry(
            {
                "action": "update",
                "area_id": "a1",
                "attrs": {"name": "renamed"},
                "proposal_id": "p",
            }
        )
    assert result["ok"] is True
    mock.assert_awaited_once_with(
        "config/area_registry/update", {"area_id": "a1", "name": "renamed"}
    )


async def test_area_update_missing_attrs() -> None:
    result = await handle_ha_config_area_registry(
        {"action": "update", "area_id": "a1", "proposal_id": "p"}
    )
    assert result["error"] == "MISSING_PARAM"


async def test_area_delete_happy() -> None:
    mock = AsyncMock(return_value=None)
    with patch("openclaw_node.commands.ha_config_area_registry.ha_ws_call", mock):
        result = await handle_ha_config_area_registry(
            {"action": "delete", "area_id": "a1", "proposal_id": "p"}
        )
    assert result["ok"] is True
    mock.assert_awaited_once_with("config/area_registry/delete", {"area_id": "a1"})


# -----------------------------------------------------------------------
# device_registry
# -----------------------------------------------------------------------


def test_device_registered() -> None:
    assert DISPATCH["ha.config.device_registry"] is handle_ha_config_device_registry


async def test_device_missing_action() -> None:
    assert (await handle_ha_config_device_registry({}))["error"] == "INVALID_PARAM"


@pytest.mark.parametrize("bad_action", [42, [], {}, ["list"], {"action": "list"}])
async def test_device_action_wrong_type(bad_action: object) -> None:
    result = await handle_ha_config_device_registry({"action": bad_action})
    assert result["error"] == "INVALID_PARAM"


async def test_device_list_happy() -> None:
    mock = AsyncMock(return_value=[{"id": "d1"}])
    with patch("openclaw_node.commands.ha_config_device_registry.ha_ws_call", mock):
        result = await handle_ha_config_device_registry({"action": "list"})
    assert result["ok"] is True
    assert result["count"] == 1


async def test_device_update_needs_proposal() -> None:
    result = await handle_ha_config_device_registry(
        {"action": "update", "device_id": "d1", "attrs": {"name": "x"}}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_device_update_happy() -> None:
    mock = AsyncMock(return_value={"id": "d1"})
    with patch("openclaw_node.commands.ha_config_device_registry.ha_ws_call", mock):
        result = await handle_ha_config_device_registry(
            {
                "action": "update",
                "device_id": "d1",
                "attrs": {"name": "renamed"},
                "proposal_id": "p",
            }
        )
    assert result["ok"] is True
    mock.assert_awaited_once_with(
        "config/device_registry/update", {"device_id": "d1", "name": "renamed"}
    )


async def test_device_update_missing_id() -> None:
    result = await handle_ha_config_device_registry(
        {"action": "update", "attrs": {"name": "x"}, "proposal_id": "p"}
    )
    assert result["error"] == "MISSING_PARAM"


async def test_device_no_create_or_delete() -> None:
    # Devices are integration-populated; HA doesn't expose create/delete.
    for action in ("create", "delete"):
        result = await handle_ha_config_device_registry({"action": action})
        assert result["error"] == "INVALID_PARAM"


# -----------------------------------------------------------------------
# entity_registry
# -----------------------------------------------------------------------


def test_entity_registered() -> None:
    assert DISPATCH["ha.config.entity_registry"] is handle_ha_config_entity_registry


async def test_entity_missing_action() -> None:
    assert (await handle_ha_config_entity_registry({}))["error"] == "INVALID_PARAM"


@pytest.mark.parametrize("bad_action", [42, [], {}, ["get"], {"action": "get"}])
async def test_entity_action_wrong_type(bad_action: object) -> None:
    result = await handle_ha_config_entity_registry({"action": bad_action})
    assert result["error"] == "INVALID_PARAM"


async def test_entity_list_happy() -> None:
    mock = AsyncMock(return_value=[{"entity_id": "sensor.a"}])
    with patch("openclaw_node.commands.ha_config_entity_registry.ha_ws_call", mock):
        result = await handle_ha_config_entity_registry({"action": "list"})
    assert result["ok"] is True
    assert result["count"] == 1


async def test_entity_get_happy() -> None:
    mock = AsyncMock(return_value={"entity_id": "sensor.a"})
    with patch("openclaw_node.commands.ha_config_entity_registry.ha_ws_call", mock):
        result = await handle_ha_config_entity_registry({"action": "get", "entity_id": "sensor.a"})
    assert result["ok"] is True
    mock.assert_awaited_once_with("config/entity_registry/get", {"entity_id": "sensor.a"})


async def test_entity_get_missing_id() -> None:
    result = await handle_ha_config_entity_registry({"action": "get"})
    assert result["error"] == "MISSING_PARAM"


async def test_entity_update_needs_proposal() -> None:
    result = await handle_ha_config_entity_registry(
        {"action": "update", "entity_id": "sensor.a", "attrs": {"name": "n"}}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_entity_update_happy() -> None:
    mock = AsyncMock(return_value={"entity_id": "sensor.a"})
    with patch("openclaw_node.commands.ha_config_entity_registry.ha_ws_call", mock):
        result = await handle_ha_config_entity_registry(
            {
                "action": "update",
                "entity_id": "sensor.a",
                "attrs": {"name": "renamed"},
                "proposal_id": "p",
            }
        )
    assert result["ok"] is True


async def test_entity_remove_needs_proposal() -> None:
    result = await handle_ha_config_entity_registry({"action": "remove", "entity_id": "sensor.a"})
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_entity_remove_happy() -> None:
    mock = AsyncMock(return_value=None)
    with patch("openclaw_node.commands.ha_config_entity_registry.ha_ws_call", mock):
        result = await handle_ha_config_entity_registry(
            {"action": "remove", "entity_id": "sensor.a", "proposal_id": "p"}
        )
    assert result["ok"] is True
    mock.assert_awaited_once_with("config/entity_registry/remove", {"entity_id": "sensor.a"})


# -----------------------------------------------------------------------
# Additional error-path coverage
# -----------------------------------------------------------------------


async def test_area_list_ha_error_propagates() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_TIMEOUT", "t"))
    with patch("openclaw_node.commands.ha_config_area_registry.ha_ws_call", mock):
        assert (await handle_ha_config_area_registry({"action": "list"}))["error"] == "HA_TIMEOUT"


async def test_area_create_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_500", "x"))
    with patch("openclaw_node.commands.ha_config_area_registry.ha_ws_call", mock):
        result = await handle_ha_config_area_registry(
            {"action": "create", "name": "K", "proposal_id": "p"}
        )
    assert result["error"] == "HA_500"


async def test_area_update_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_500", "x"))
    with patch("openclaw_node.commands.ha_config_area_registry.ha_ws_call", mock):
        result = await handle_ha_config_area_registry(
            {"action": "update", "area_id": "a1", "attrs": {"n": 1}, "proposal_id": "p"}
        )
    assert result["error"] == "HA_500"


async def test_area_delete_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_500", "x"))
    with patch("openclaw_node.commands.ha_config_area_registry.ha_ws_call", mock):
        result = await handle_ha_config_area_registry(
            {"action": "delete", "area_id": "a1", "proposal_id": "p"}
        )
    assert result["error"] == "HA_500"


async def test_area_create_with_extra_attrs() -> None:
    mock = AsyncMock(return_value={"area_id": "new"})
    with patch("openclaw_node.commands.ha_config_area_registry.ha_ws_call", mock):
        result = await handle_ha_config_area_registry(
            {"action": "create", "name": "K", "attrs": {"icon": "mdi:x"}, "proposal_id": "p"}
        )
    assert result["ok"] is True
    mock.assert_awaited_once_with("config/area_registry/create", {"name": "K", "icon": "mdi:x"})


async def test_area_non_string_proposal() -> None:
    result = await handle_ha_config_area_registry(
        {"action": "create", "name": "K", "proposal_id": 42}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_area_empty_proposal() -> None:
    result = await handle_ha_config_area_registry(
        {"action": "create", "name": "K", "proposal_id": "   "}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_area_create_name_wrong_type() -> None:
    result = await handle_ha_config_area_registry(
        {"action": "create", "name": 42, "proposal_id": "p"}
    )
    assert result["error"] == "MISSING_PARAM"


async def test_area_update_missing_area_id() -> None:
    result = await handle_ha_config_area_registry(
        {"action": "update", "attrs": {"n": 1}, "proposal_id": "p"}
    )
    assert result["error"] == "MISSING_PARAM"


async def test_device_list_bad_response() -> None:
    mock = AsyncMock(return_value={"nope": True})
    with patch("openclaw_node.commands.ha_config_device_registry.ha_ws_call", mock):
        assert (await handle_ha_config_device_registry({"action": "list"}))[
            "error"
        ] == "HA_BAD_RESPONSE"


async def test_device_list_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_500", "x"))
    with patch("openclaw_node.commands.ha_config_device_registry.ha_ws_call", mock):
        assert (await handle_ha_config_device_registry({"action": "list"}))["error"] == "HA_500"


async def test_device_update_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_500", "x"))
    with patch("openclaw_node.commands.ha_config_device_registry.ha_ws_call", mock):
        result = await handle_ha_config_device_registry(
            {"action": "update", "device_id": "d1", "attrs": {"n": "x"}, "proposal_id": "p"}
        )
    assert result["error"] == "HA_500"


async def test_device_update_missing_attrs() -> None:
    result = await handle_ha_config_device_registry(
        {"action": "update", "device_id": "d1", "proposal_id": "p"}
    )
    assert result["error"] == "MISSING_PARAM"


async def test_device_non_string_proposal() -> None:
    result = await handle_ha_config_device_registry(
        {"action": "update", "device_id": "d1", "attrs": {"n": 1}, "proposal_id": 42}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_device_direct_proposal_refused() -> None:
    result = await handle_ha_config_device_registry(
        {"action": "update", "device_id": "d1", "attrs": {"n": 1}, "proposal_id": "direct"}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_entity_list_bad_response() -> None:
    mock = AsyncMock(return_value={"nope": True})
    with patch("openclaw_node.commands.ha_config_entity_registry.ha_ws_call", mock):
        assert (await handle_ha_config_entity_registry({"action": "list"}))[
            "error"
        ] == "HA_BAD_RESPONSE"


async def test_entity_list_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_500", "x"))
    with patch("openclaw_node.commands.ha_config_entity_registry.ha_ws_call", mock):
        assert (await handle_ha_config_entity_registry({"action": "list"}))["error"] == "HA_500"


async def test_entity_get_bad_response() -> None:
    mock = AsyncMock(return_value="nope")
    with patch("openclaw_node.commands.ha_config_entity_registry.ha_ws_call", mock):
        result = await handle_ha_config_entity_registry({"action": "get", "entity_id": "sensor.a"})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_entity_get_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_500", "x"))
    with patch("openclaw_node.commands.ha_config_entity_registry.ha_ws_call", mock):
        result = await handle_ha_config_entity_registry({"action": "get", "entity_id": "sensor.a"})
    assert result["error"] == "HA_500"


async def test_entity_update_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_500", "x"))
    with patch("openclaw_node.commands.ha_config_entity_registry.ha_ws_call", mock):
        result = await handle_ha_config_entity_registry(
            {
                "action": "update",
                "entity_id": "sensor.a",
                "attrs": {"name": "x"},
                "proposal_id": "p",
            }
        )
    assert result["error"] == "HA_500"


async def test_entity_remove_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_500", "x"))
    with patch("openclaw_node.commands.ha_config_entity_registry.ha_ws_call", mock):
        result = await handle_ha_config_entity_registry(
            {"action": "remove", "entity_id": "sensor.a", "proposal_id": "p"}
        )
    assert result["error"] == "HA_500"


async def test_entity_update_missing_attrs() -> None:
    result = await handle_ha_config_entity_registry(
        {"action": "update", "entity_id": "sensor.a", "proposal_id": "p"}
    )
    assert result["error"] == "MISSING_PARAM"


async def test_entity_non_string_proposal() -> None:
    result = await handle_ha_config_entity_registry(
        {"action": "update", "entity_id": "sensor.a", "attrs": {"n": 1}, "proposal_id": 42}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_entity_direct_proposal_refused() -> None:
    result = await handle_ha_config_entity_registry(
        {"action": "remove", "entity_id": "sensor.a", "proposal_id": "direct"}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_entity_id_wrong_type() -> None:
    result = await handle_ha_config_entity_registry({"action": "get", "entity_id": 42})
    assert result["error"] == "MISSING_PARAM"
