"""Tests for openclaw_node.commands.ha_config_automation."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from openclaw_node.commands.dispatcher import _REGISTRY
from openclaw_node.commands.ha_config_automation import handle_ha_config_automation
from openclaw_node.ha_client import HAClientError

# ---------------------------------------------------------------------------
# action dispatch (missing / unknown / invalid)
# ---------------------------------------------------------------------------


async def test_missing_action() -> None:
    result = await handle_ha_config_automation({})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"


@pytest.mark.parametrize("bad_action", [42, [], {}, ["get"], {"action": "get"}])
async def test_action_wrong_type(bad_action: object) -> None:
    result = await handle_ha_config_automation({"action": bad_action})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"


async def test_action_empty_string() -> None:
    result = await handle_ha_config_automation({"action": "   "})
    assert result["error"] == "INVALID_PARAM"


async def test_unknown_action() -> None:
    result = await handle_ha_config_automation({"action": "purge"})
    assert result["error"] == "INVALID_PARAM"


# ---------------------------------------------------------------------------
# action=list
# ---------------------------------------------------------------------------


async def test_list_returns_list() -> None:
    automations = [{"id": "1", "alias": "morning"}, {"id": "2", "alias": "night"}]
    mock = AsyncMock(return_value=automations)
    with patch("openclaw_node.commands.ha_config_automation.ha_get", mock):
        result = await handle_ha_config_automation({"action": "list"})
    assert result == {"ok": True, "count": 2, "automations": automations}
    mock.assert_awaited_once_with("/api/config/automation/config")


async def test_list_bad_response() -> None:
    mock = AsyncMock(return_value={"not": "a list"})
    with patch("openclaw_node.commands.ha_config_automation.ha_get", mock):
        result = await handle_ha_config_automation({"action": "list"})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_list_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_AUTH", "401"))
    with patch("openclaw_node.commands.ha_config_automation.ha_get", mock):
        result = await handle_ha_config_automation({"action": "list"})
    assert result["error"] == "HA_AUTH"


async def test_list_never_uses_ws() -> None:
    """list MUST be REST-only; no WS frame is ever sent."""
    get_mock = AsyncMock(return_value=[])
    ws_mock = AsyncMock(return_value=None)
    with (
        patch("openclaw_node.commands.ha_config_automation.ha_get", get_mock),
        patch("openclaw_node.ha_client.ha_ws_call", ws_mock),
    ):
        result = await handle_ha_config_automation({"action": "list"})
    assert result["ok"] is True
    get_mock.assert_awaited_once_with("/api/config/automation/config")
    ws_mock.assert_not_called()


# ---------------------------------------------------------------------------
# action=get
# ---------------------------------------------------------------------------


async def test_get_happy_path() -> None:
    config = {"id": "42", "alias": "morning", "trigger": []}
    mock = AsyncMock(return_value=config)
    with patch("openclaw_node.commands.ha_config_automation.ha_get", mock):
        result = await handle_ha_config_automation({"action": "get", "id": "42"})
    assert result == {"ok": True, "id": "42", "config": config}
    mock.assert_awaited_once_with("/api/config/automation/config/42")


async def test_get_missing_id() -> None:
    result = await handle_ha_config_automation({"action": "get"})
    assert result["error"] == "MISSING_PARAM"


async def test_get_invalid_id_type() -> None:
    result = await handle_ha_config_automation({"action": "get", "id": 42})
    assert result["error"] == "MISSING_PARAM"


async def test_get_empty_id() -> None:
    result = await handle_ha_config_automation({"action": "get", "id": "   "})
    assert result["error"] == "MISSING_PARAM"


async def test_get_bad_response_shape() -> None:
    mock = AsyncMock(return_value=["not", "a", "dict"])
    with patch("openclaw_node.commands.ha_config_automation.ha_get", mock):
        result = await handle_ha_config_automation({"action": "get", "id": "42"})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_get_ha_error_propagates() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_NOT_FOUND", "gone"))
    with patch("openclaw_node.commands.ha_config_automation.ha_get", mock):
        result = await handle_ha_config_automation({"action": "get", "id": "42"})
    assert result["error"] == "HA_NOT_FOUND"


# ---------------------------------------------------------------------------
# action=save
# ---------------------------------------------------------------------------


async def test_save_missing_proposal_id() -> None:
    result = await handle_ha_config_automation(
        {"action": "save", "id": "1", "config": {"alias": "x"}}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_save_empty_proposal_id() -> None:
    result = await handle_ha_config_automation(
        {"action": "save", "id": "1", "config": {"alias": "x"}, "proposal_id": "   "}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_save_direct_proposal_id_refused() -> None:
    result = await handle_ha_config_automation(
        {"action": "save", "id": "1", "config": {"alias": "x"}, "proposal_id": "direct"}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_save_non_string_proposal_id() -> None:
    result = await handle_ha_config_automation(
        {"action": "save", "id": "1", "config": {"alias": "x"}, "proposal_id": 123}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_save_missing_id() -> None:
    result = await handle_ha_config_automation(
        {"action": "save", "config": {"alias": "x"}, "proposal_id": "p1"}
    )
    assert result["error"] == "MISSING_PARAM"


async def test_save_invalid_id_type() -> None:
    result = await handle_ha_config_automation(
        {"action": "save", "id": 42, "config": {"alias": "x"}, "proposal_id": "p1"}
    )
    assert result["error"] == "MISSING_PARAM"


async def test_save_missing_config() -> None:
    result = await handle_ha_config_automation({"action": "save", "id": "1", "proposal_id": "p1"})
    assert result["error"] == "MISSING_PARAM"


async def test_save_config_wrong_type() -> None:
    result = await handle_ha_config_automation(
        {"action": "save", "id": "1", "config": "yaml", "proposal_id": "p1"}
    )
    assert result["error"] == "MISSING_PARAM"


async def test_save_happy_path() -> None:
    config = {"alias": "morning", "trigger": []}
    mock = AsyncMock(return_value={"result": "ok"})
    with patch("openclaw_node.commands.ha_config_automation.ha_post", mock):
        result = await handle_ha_config_automation(
            {"action": "save", "id": "42", "config": config, "proposal_id": "p1"}
        )
    assert result == {"ok": True, "id": "42", "proposal_id": "p1"}
    mock.assert_awaited_once_with("/api/config/automation/config/42", config)


async def test_save_ha_error_propagates() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_HTTP_ERROR", "boom"))
    with patch("openclaw_node.commands.ha_config_automation.ha_post", mock):
        result = await handle_ha_config_automation(
            {"action": "save", "id": "1", "config": {"a": 1}, "proposal_id": "p1"}
        )
    assert result["error"] == "HA_HTTP_ERROR"


# ---------------------------------------------------------------------------
# action=delete
# ---------------------------------------------------------------------------


async def test_delete_missing_proposal_id() -> None:
    result = await handle_ha_config_automation({"action": "delete", "id": "1"})
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_delete_empty_proposal_id() -> None:
    result = await handle_ha_config_automation(
        {"action": "delete", "id": "1", "proposal_id": "   "}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_delete_direct_proposal_id_refused() -> None:
    result = await handle_ha_config_automation(
        {"action": "delete", "id": "1", "proposal_id": "direct"}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_delete_non_string_proposal_id() -> None:
    result = await handle_ha_config_automation({"action": "delete", "id": "1", "proposal_id": 123})
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_delete_missing_id() -> None:
    result = await handle_ha_config_automation({"action": "delete", "proposal_id": "p1"})
    assert result["error"] == "MISSING_PARAM"


async def test_delete_invalid_id_type() -> None:
    result = await handle_ha_config_automation({"action": "delete", "id": 42, "proposal_id": "p1"})
    assert result["error"] == "MISSING_PARAM"


async def test_delete_happy_path() -> None:
    mock = AsyncMock(return_value={"result": "ok"})
    with patch("openclaw_node.commands.ha_config_automation.ha_delete", mock):
        result = await handle_ha_config_automation(
            {"action": "delete", "id": "42", "proposal_id": "p1"}
        )
    assert result == {"ok": True, "id": "42", "proposal_id": "p1"}
    mock.assert_awaited_once_with("/api/config/automation/config/42")


async def test_delete_ha_error_propagates() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_NOT_FOUND", "gone"))
    with patch("openclaw_node.commands.ha_config_automation.ha_delete", mock):
        result = await handle_ha_config_automation(
            {"action": "delete", "id": "1", "proposal_id": "p1"}
        )
    assert result["error"] == "HA_NOT_FOUND"


# ---------------------------------------------------------------------------
# dispatcher registration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["ha.config.automation"])
def test_command_registered(command: str) -> None:
    assert command in _REGISTRY


def test_module_export_is_async_handler() -> None:
    assert inspect.iscoroutinefunction(handle_ha_config_automation)


def test_no_per_verb_commands_registered() -> None:
    for old in (
        "ha.config.automation.list",
        "ha.config.automation.get",
        "ha.config.automation.save",
        "ha.config.automation.delete",
    ):
        assert old not in _REGISTRY
