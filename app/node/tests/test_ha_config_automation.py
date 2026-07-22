"""Tests for openclaw_node.commands.ha_config_automation."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from openclaw_node.commands.dispatcher import _REGISTRY
from openclaw_node.commands.ha_config_automation import (
    handle_ha_config_automation_delete,
    handle_ha_config_automation_get,
    handle_ha_config_automation_list,
    handle_ha_config_automation_save,
)
from openclaw_node.ha_client import HAClientError

_LIST_PATH = "/api/config/automation/config"


# ---------------------------------------------------------------------------
# ha.config.automation.list
# ---------------------------------------------------------------------------


async def test_list_happy_path() -> None:
    automations = [
        {"id": "1683999999", "alias": "Sunset lights"},
        {"id": "morning", "alias": "Coffee"},
    ]
    mock = AsyncMock(return_value=automations)
    with patch("openclaw_node.commands.ha_config_automation.ha_get", mock):
        result = await handle_ha_config_automation_list({})
    assert result == {"ok": True, "count": 2, "automations": automations}
    mock.assert_awaited_once_with(_LIST_PATH)


async def test_list_bad_response_shape() -> None:
    mock = AsyncMock(return_value={"not": "a list"})
    with patch("openclaw_node.commands.ha_config_automation.ha_get", mock):
        result = await handle_ha_config_automation_list({})
    assert result["ok"] is False
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_list_ha_error_propagates() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_AUTH", "401"))
    with patch("openclaw_node.commands.ha_config_automation.ha_get", mock):
        result = await handle_ha_config_automation_list({})
    assert result["ok"] is False
    assert result["error"] == "HA_AUTH"


# ---------------------------------------------------------------------------
# ha.config.automation.get
# ---------------------------------------------------------------------------


async def test_get_happy_path() -> None:
    config = {"id": "morning", "alias": "Coffee", "trigger": []}
    mock = AsyncMock(return_value=config)
    with patch("openclaw_node.commands.ha_config_automation.ha_get", mock):
        result = await handle_ha_config_automation_get({"id": "morning"})
    assert result == {"ok": True, "id": "morning", "config": config}
    mock.assert_awaited_once_with(f"{_LIST_PATH}/morning")


async def test_get_missing_id() -> None:
    result = await handle_ha_config_automation_get({})
    assert result["error"] == "MISSING_PARAM"


async def test_get_id_wrong_type() -> None:
    result = await handle_ha_config_automation_get({"id": 42})
    assert result["error"] == "MISSING_PARAM"


async def test_get_id_empty_string() -> None:
    result = await handle_ha_config_automation_get({"id": "   "})
    assert result["error"] == "MISSING_PARAM"


@pytest.mark.parametrize(
    "bad_id",
    [
        "../etc/passwd",
        "foo/bar",
        "foo bar",
        "foo?bar",
        "foo%20bar",
        "foo\nbar",
        "a" * 129,
    ],
)
async def test_get_invalid_id_format(bad_id: str) -> None:
    result = await handle_ha_config_automation_get({"id": bad_id})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"


async def test_get_bad_response_shape() -> None:
    mock = AsyncMock(return_value=["not", "a", "dict"])
    with patch("openclaw_node.commands.ha_config_automation.ha_get", mock):
        result = await handle_ha_config_automation_get({"id": "morning"})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_get_ha_error_propagates() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_NOT_FOUND", "no such id"))
    with patch("openclaw_node.commands.ha_config_automation.ha_get", mock):
        result = await handle_ha_config_automation_get({"id": "morning"})
    assert result["error"] == "HA_NOT_FOUND"


# ---------------------------------------------------------------------------
# ha.config.automation.save
# ---------------------------------------------------------------------------


async def test_save_missing_proposal_id() -> None:
    result = await handle_ha_config_automation_save({"id": "morning", "config": {"alias": "x"}})
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_save_empty_proposal_id() -> None:
    result = await handle_ha_config_automation_save(
        {"id": "morning", "config": {"alias": "x"}, "proposal_id": "   "}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_save_direct_proposal_id_refused() -> None:
    result = await handle_ha_config_automation_save(
        {"id": "morning", "config": {"alias": "x"}, "proposal_id": "direct"}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_save_non_string_proposal_id() -> None:
    result = await handle_ha_config_automation_save(
        {"id": "morning", "config": {"alias": "x"}, "proposal_id": 7}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_save_missing_id() -> None:
    result = await handle_ha_config_automation_save({"config": {"alias": "x"}, "proposal_id": "p1"})
    assert result["error"] == "MISSING_PARAM"


async def test_save_invalid_id() -> None:
    result = await handle_ha_config_automation_save(
        {"id": "../bad", "config": {"alias": "x"}, "proposal_id": "p1"}
    )
    assert result["error"] == "INVALID_PARAM"


async def test_save_missing_config() -> None:
    result = await handle_ha_config_automation_save({"id": "morning", "proposal_id": "p1"})
    assert result["error"] == "MISSING_PARAM"


async def test_save_config_wrong_type() -> None:
    result = await handle_ha_config_automation_save(
        {"id": "morning", "config": "yaml goes here", "proposal_id": "p1"}
    )
    assert result["error"] == "MISSING_PARAM"


async def test_save_happy_path() -> None:
    config = {"alias": "Coffee", "trigger": [], "action": []}
    ha_response = {"result": "ok"}
    mock = AsyncMock(return_value=ha_response)
    with patch("openclaw_node.commands.ha_config_automation.ha_post", mock):
        result = await handle_ha_config_automation_save(
            {"id": "morning", "config": config, "proposal_id": "p1"}
        )
    assert result == {
        "ok": True,
        "id": "morning",
        "proposal_id": "p1",
        "result": ha_response,
    }
    mock.assert_awaited_once_with(f"{_LIST_PATH}/morning", config)


async def test_save_ha_error_propagates() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_HTTP_ERROR", "500"))
    with patch("openclaw_node.commands.ha_config_automation.ha_post", mock):
        result = await handle_ha_config_automation_save(
            {"id": "morning", "config": {"alias": "x"}, "proposal_id": "p1"}
        )
    assert result["ok"] is False
    assert result["error"] == "HA_HTTP_ERROR"


# ---------------------------------------------------------------------------
# ha.config.automation.delete
# ---------------------------------------------------------------------------


async def test_delete_missing_proposal_id() -> None:
    result = await handle_ha_config_automation_delete({"id": "morning"})
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_delete_direct_proposal_id_refused() -> None:
    result = await handle_ha_config_automation_delete({"id": "morning", "proposal_id": "direct"})
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_delete_missing_id() -> None:
    result = await handle_ha_config_automation_delete({"proposal_id": "p1"})
    assert result["error"] == "MISSING_PARAM"


async def test_delete_invalid_id() -> None:
    result = await handle_ha_config_automation_delete({"id": "../etc/passwd", "proposal_id": "p1"})
    assert result["error"] == "INVALID_PARAM"


async def test_delete_happy_path() -> None:
    ha_response = {"result": "ok"}
    mock = AsyncMock(return_value=ha_response)
    with patch("openclaw_node.commands.ha_config_automation.ha_delete", mock):
        result = await handle_ha_config_automation_delete({"id": "morning", "proposal_id": "p1"})
    assert result == {
        "ok": True,
        "id": "morning",
        "proposal_id": "p1",
        "result": ha_response,
    }
    mock.assert_awaited_once_with(f"{_LIST_PATH}/morning")


async def test_delete_ha_error_propagates() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_NOT_FOUND", "gone"))
    with patch("openclaw_node.commands.ha_config_automation.ha_delete", mock):
        result = await handle_ha_config_automation_delete({"id": "morning", "proposal_id": "p1"})
    assert result["error"] == "HA_NOT_FOUND"


# ---------------------------------------------------------------------------
# dispatcher registration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "ha.config.automation.list",
        "ha.config.automation.get",
        "ha.config.automation.save",
        "ha.config.automation.delete",
    ],
)
def test_command_registered(command: str) -> None:
    assert command in _REGISTRY


def test_module_exports_are_awaitable_handlers() -> None:
    import inspect

    handlers = [
        handle_ha_config_automation_list,
        handle_ha_config_automation_get,
        handle_ha_config_automation_save,
        handle_ha_config_automation_delete,
    ]
    for h in handlers:
        assert inspect.iscoroutinefunction(h)
