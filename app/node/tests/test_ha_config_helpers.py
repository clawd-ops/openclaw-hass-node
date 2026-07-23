"""Tests for ha.config.helpers command."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from openclaw_node.commands.dispatcher import _REGISTRY as DISPATCH
from openclaw_node.commands.ha_config_helpers import handle_ha_config_helpers
from openclaw_node.ha_client import HAClientError

pytestmark = pytest.mark.asyncio


def test_command_registered() -> None:
    assert DISPATCH["ha.config.helpers"] is handle_ha_config_helpers


async def test_missing_action() -> None:
    result = await handle_ha_config_helpers({"helper_type": "input_boolean"})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"


@pytest.mark.parametrize("bad_action", [42, [], {}, ["list"], {"action": "list"}])
async def test_action_wrong_type(bad_action: object) -> None:
    result = await handle_ha_config_helpers({"action": bad_action, "helper_type": "input_boolean"})
    assert result["error"] == "INVALID_PARAM"


async def test_unknown_action() -> None:
    result = await handle_ha_config_helpers({"action": "bogus", "helper_type": "input_boolean"})
    assert result["error"] == "INVALID_PARAM"


async def test_missing_helper_type() -> None:
    result = await handle_ha_config_helpers({"action": "list"})
    assert result["error"] == "MISSING_PARAM"


async def test_unknown_helper_type() -> None:
    result = await handle_ha_config_helpers({"action": "list", "helper_type": "bogus"})
    assert result["error"] == "INVALID_PARAM"


@pytest.mark.parametrize(
    "helper_type",
    [
        "input_boolean",
        "input_text",
        "input_number",
        "input_select",
        "input_datetime",
        "counter",
        "timer",
        "schedule",
    ],
)
async def test_list_happy_path_all_types(helper_type: str) -> None:
    mock = AsyncMock(return_value=[{"id": "1"}])
    with patch("openclaw_node.commands.ha_config_helpers.ha_ws_call", mock):
        result = await handle_ha_config_helpers({"action": "list", "helper_type": helper_type})
    assert result["ok"] is True
    assert result["helper_type"] == helper_type
    assert result["count"] == 1
    mock.assert_awaited_once_with(f"{helper_type}/list")


async def test_list_bad_response() -> None:
    mock = AsyncMock(return_value={"not": "a list"})
    with patch("openclaw_node.commands.ha_config_helpers.ha_ws_call", mock):
        result = await handle_ha_config_helpers({"action": "list", "helper_type": "input_boolean"})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_list_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_AUTH", "401"))
    with patch("openclaw_node.commands.ha_config_helpers.ha_ws_call", mock):
        result = await handle_ha_config_helpers({"action": "list", "helper_type": "counter"})
    assert result["error"] == "HA_AUTH"


async def test_get_action_rejected() -> None:
    """HA storage-collection surface has no `<domain>/get` frame."""
    result = await handle_ha_config_helpers(
        {"action": "get", "helper_type": "counter", "counter_id": "abc"}
    )
    assert result["error"] == "INVALID_PARAM"


@pytest.mark.parametrize("action", ["create", "update", "delete"])
async def test_mutating_missing_proposal(action: str) -> None:
    result = await handle_ha_config_helpers(
        {
            "action": action,
            "helper_type": "input_boolean",
            "input_boolean_id": "foo",
            "attrs": {"name": "foo"},
        }
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


@pytest.mark.parametrize("action", ["create", "update", "delete"])
async def test_mutating_direct_proposal_refused(action: str) -> None:
    result = await handle_ha_config_helpers(
        {
            "action": action,
            "helper_type": "input_boolean",
            "input_boolean_id": "foo",
            "attrs": {"name": "foo"},
            "proposal_id": "direct",
        }
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_create_happy_path() -> None:
    mock = AsyncMock(return_value={"id": "new"})
    with patch("openclaw_node.commands.ha_config_helpers.ha_ws_call", mock):
        result = await handle_ha_config_helpers(
            {
                "action": "create",
                "helper_type": "input_boolean",
                "attrs": {"name": "New Boolean"},
                "proposal_id": "prop-1",
            }
        )
    assert result["ok"] is True
    assert result["proposal_id"] == "prop-1"
    mock.assert_awaited_once_with("input_boolean/create", {"name": "New Boolean"})


async def test_create_missing_attrs() -> None:
    result = await handle_ha_config_helpers(
        {
            "action": "create",
            "helper_type": "input_boolean",
            "proposal_id": "p",
        }
    )
    assert result["error"] == "MISSING_PARAM"


async def test_update_rejects_conflicting_item_key_in_attrs() -> None:
    """`attrs` containing `<helper_type>_id` must be rejected outright.

    Prevents a caller from logging one id but targeting a different helper
    in the actual WS frame via dict-merge shadowing.
    """
    result = await handle_ha_config_helpers(
        {
            "action": "update",
            "helper_type": "input_number",
            "input_number_id": "expected",
            "attrs": {"input_number_id": "other", "name": "x"},
            "proposal_id": "p",
        }
    )
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"


async def test_update_happy_path_uses_domain_id_key() -> None:
    """update MUST use `<helper_type>_id`, not `entity_id`."""
    mock = AsyncMock(return_value={"updated": True})
    with patch("openclaw_node.commands.ha_config_helpers.ha_ws_call", mock):
        result = await handle_ha_config_helpers(
            {
                "action": "update",
                "helper_type": "input_number",
                "input_number_id": "abc",
                "attrs": {"name": "renamed"},
                "proposal_id": "prop-2",
            }
        )
    assert result["ok"] is True
    mock.assert_awaited_once_with(
        "input_number/update",
        {"input_number_id": "abc", "name": "renamed"},
    )


async def test_delete_happy_path_uses_domain_id_key() -> None:
    """delete MUST use `<helper_type>_id`, not `entity_id`."""
    mock = AsyncMock(return_value=None)
    with patch("openclaw_node.commands.ha_config_helpers.ha_ws_call", mock):
        result = await handle_ha_config_helpers(
            {
                "action": "delete",
                "helper_type": "counter",
                "counter_id": "abc",
                "proposal_id": "prop-3",
            }
        )
    assert result["ok"] is True
    mock.assert_awaited_once_with("counter/delete", {"counter_id": "abc"})


async def test_ha_error_propagates() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_TIMEOUT", "timeout"))
    with patch("openclaw_node.commands.ha_config_helpers.ha_ws_call", mock):
        result = await handle_ha_config_helpers(
            {
                "action": "delete",
                "helper_type": "counter",
                "counter_id": "abc",
                "proposal_id": "p",
            }
        )
    assert result["error"] == "HA_TIMEOUT"


async def test_non_string_proposal_id() -> None:
    result = await handle_ha_config_helpers(
        {
            "action": "create",
            "helper_type": "input_boolean",
            "attrs": {"name": "x"},
            "proposal_id": 42,
        }
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_attrs_wrong_type_on_create() -> None:
    result = await handle_ha_config_helpers(
        {
            "action": "create",
            "helper_type": "input_boolean",
            "attrs": "not a dict",
            "proposal_id": "p",
        }
    )
    assert result["error"] == "MISSING_PARAM"


async def test_helper_type_wrong_type() -> None:
    result = await handle_ha_config_helpers({"action": "list", "helper_type": 42})
    assert result["error"] == "MISSING_PARAM"


async def test_helper_type_empty_string() -> None:
    result = await handle_ha_config_helpers({"action": "list", "helper_type": "  "})
    assert result["error"] == "MISSING_PARAM"


async def test_create_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_500", "x"))
    with patch("openclaw_node.commands.ha_config_helpers.ha_ws_call", mock):
        result = await handle_ha_config_helpers(
            {
                "action": "create",
                "helper_type": "input_boolean",
                "attrs": {"name": "x"},
                "proposal_id": "p",
            }
        )
    assert result["error"] == "HA_500"


async def test_update_missing_item_id() -> None:
    result = await handle_ha_config_helpers(
        {
            "action": "update",
            "helper_type": "counter",
            "attrs": {"name": "x"},
            "proposal_id": "p",
        }
    )
    assert result["error"] == "MISSING_PARAM"


async def test_update_missing_attrs() -> None:
    result = await handle_ha_config_helpers(
        {
            "action": "update",
            "helper_type": "counter",
            "counter_id": "abc",
            "proposal_id": "p",
        }
    )
    assert result["error"] == "MISSING_PARAM"


async def test_update_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_500", "x"))
    with patch("openclaw_node.commands.ha_config_helpers.ha_ws_call", mock):
        result = await handle_ha_config_helpers(
            {
                "action": "update",
                "helper_type": "counter",
                "counter_id": "abc",
                "attrs": {"name": "x"},
                "proposal_id": "p",
            }
        )
    assert result["error"] == "HA_500"


async def test_delete_missing_item_id() -> None:
    result = await handle_ha_config_helpers(
        {
            "action": "delete",
            "helper_type": "counter",
            "proposal_id": "p",
        }
    )
    assert result["error"] == "MISSING_PARAM"


async def test_action_empty_string() -> None:
    result = await handle_ha_config_helpers({"action": "  ", "helper_type": "counter"})
    assert result["error"] == "INVALID_PARAM"
