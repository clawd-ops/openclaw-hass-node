"""Tests for openclaw_node.commands.ha_config_lovelace."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from openclaw_node.commands.dispatcher import _REGISTRY
from openclaw_node.commands.ha_config_lovelace import handle_ha_config_lovelace
from openclaw_node.ha_client import HAClientError

# ---------------------------------------------------------------------------
# action dispatch (missing / unknown / invalid)
# ---------------------------------------------------------------------------


async def test_missing_action() -> None:
    result = await handle_ha_config_lovelace({})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"


async def test_action_wrong_type() -> None:
    result = await handle_ha_config_lovelace({"action": 42})
    assert result["error"] == "INVALID_PARAM"


async def test_action_empty_string() -> None:
    result = await handle_ha_config_lovelace({"action": "   "})
    assert result["error"] == "INVALID_PARAM"


async def test_unknown_action() -> None:
    result = await handle_ha_config_lovelace({"action": "delete_everything"})
    assert result["error"] == "INVALID_PARAM"


# ---------------------------------------------------------------------------
# action=get
# ---------------------------------------------------------------------------


async def test_get_default_dashboard_calls_lovelace_config() -> None:
    config = {"views": [{"title": "Home"}]}
    mock = AsyncMock(return_value=config)
    with patch("openclaw_node.commands.ha_config_lovelace.ha_ws_call", mock):
        result = await handle_ha_config_lovelace({"action": "get"})
    assert result == {"ok": True, "url_path": None, "config": config}
    mock.assert_awaited_once_with("lovelace/config", {})


async def test_get_named_dashboard_forwards_url_path() -> None:
    config: dict[str, object] = {"views": []}
    mock = AsyncMock(return_value=config)
    with patch("openclaw_node.commands.ha_config_lovelace.ha_ws_call", mock):
        result = await handle_ha_config_lovelace({"action": "get", "url_path": "energy"})
    assert result["ok"] is True
    assert result["url_path"] == "energy"
    mock.assert_awaited_once_with("lovelace/config", {"url_path": "energy"})


async def test_get_empty_url_path_treated_as_default() -> None:
    mock = AsyncMock(return_value={"views": []})
    with patch("openclaw_node.commands.ha_config_lovelace.ha_ws_call", mock):
        result = await handle_ha_config_lovelace({"action": "get", "url_path": "   "})
    assert result["url_path"] is None
    mock.assert_awaited_once_with("lovelace/config", {})


async def test_get_invalid_url_path_type() -> None:
    result = await handle_ha_config_lovelace({"action": "get", "url_path": 42})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"


async def test_get_ha_error_propagates() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_AUTH", "401"))
    with patch("openclaw_node.commands.ha_config_lovelace.ha_ws_call", mock):
        result = await handle_ha_config_lovelace({"action": "get"})
    assert result["ok"] is False
    assert result["error"] == "HA_AUTH"


async def test_get_bad_response_shape() -> None:
    mock = AsyncMock(return_value=["not", "a", "dict"])
    with patch("openclaw_node.commands.ha_config_lovelace.ha_ws_call", mock):
        result = await handle_ha_config_lovelace({"action": "get"})
    assert result["error"] == "HA_BAD_RESPONSE"


# ---------------------------------------------------------------------------
# action=save
# ---------------------------------------------------------------------------


async def test_save_missing_proposal_id() -> None:
    result = await handle_ha_config_lovelace({"action": "save", "config": {"views": []}})
    assert result["ok"] is False
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_save_empty_proposal_id() -> None:
    result = await handle_ha_config_lovelace(
        {"action": "save", "config": {"views": []}, "proposal_id": "   "}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_save_direct_proposal_id_refused() -> None:
    result = await handle_ha_config_lovelace(
        {"action": "save", "config": {"views": []}, "proposal_id": "direct"}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_save_non_string_proposal_id() -> None:
    result = await handle_ha_config_lovelace(
        {"action": "save", "config": {"views": []}, "proposal_id": 123}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_save_missing_config() -> None:
    result = await handle_ha_config_lovelace({"action": "save", "proposal_id": "prop-1"})
    assert result["error"] == "MISSING_PARAM"


async def test_save_config_wrong_type() -> None:
    result = await handle_ha_config_lovelace(
        {"action": "save", "config": "yaml goes here", "proposal_id": "prop-1"}
    )
    assert result["error"] == "MISSING_PARAM"


async def test_save_default_dashboard_happy_path() -> None:
    config = {"views": [{"title": "Home"}]}
    mock = AsyncMock(return_value=None)
    with patch("openclaw_node.commands.ha_config_lovelace.ha_ws_call", mock):
        result = await handle_ha_config_lovelace(
            {"action": "save", "config": config, "proposal_id": "prop-1"}
        )
    assert result == {"ok": True, "url_path": None, "proposal_id": "prop-1"}
    mock.assert_awaited_once_with("lovelace/config/save", {"config": config})


async def test_save_named_dashboard_forwards_url_path() -> None:
    config: dict[str, object] = {"views": []}
    mock = AsyncMock(return_value=None)
    with patch("openclaw_node.commands.ha_config_lovelace.ha_ws_call", mock):
        result = await handle_ha_config_lovelace(
            {
                "action": "save",
                "config": config,
                "url_path": "energy",
                "proposal_id": "prop-42",
            }
        )
    assert result["ok"] is True
    assert result["url_path"] == "energy"
    mock.assert_awaited_once_with(
        "lovelace/config/save",
        {"config": config, "url_path": "energy"},
    )


async def test_save_invalid_url_path_type() -> None:
    result = await handle_ha_config_lovelace(
        {
            "action": "save",
            "config": {"views": []},
            "url_path": 42,
            "proposal_id": "prop-1",
        }
    )
    assert result["error"] == "INVALID_PARAM"


async def test_save_ha_error_propagates() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_WS_ERROR", "bad payload"))
    with patch("openclaw_node.commands.ha_config_lovelace.ha_ws_call", mock):
        result = await handle_ha_config_lovelace(
            {"action": "save", "config": {"views": []}, "proposal_id": "prop-1"}
        )
    assert result["ok"] is False
    assert result["error"] == "HA_WS_ERROR"


# ---------------------------------------------------------------------------
# action=dashboards_list
# ---------------------------------------------------------------------------


async def test_dashboards_list_returns_list() -> None:
    dashboards = [{"id": "abc", "url_path": "energy", "title": "Energy"}]
    mock = AsyncMock(return_value=dashboards)
    with patch("openclaw_node.commands.ha_config_lovelace.ha_ws_call", mock):
        result = await handle_ha_config_lovelace({"action": "dashboards_list"})
    assert result == {"ok": True, "count": 1, "dashboards": dashboards}
    mock.assert_awaited_once_with("lovelace/dashboards/list")


async def test_dashboards_list_bad_response() -> None:
    mock = AsyncMock(return_value={"not": "a list"})
    with patch("openclaw_node.commands.ha_config_lovelace.ha_ws_call", mock):
        result = await handle_ha_config_lovelace({"action": "dashboards_list"})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_dashboards_list_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_NETWORK", "down"))
    with patch("openclaw_node.commands.ha_config_lovelace.ha_ws_call", mock):
        result = await handle_ha_config_lovelace({"action": "dashboards_list"})
    assert result["error"] == "HA_NETWORK"


# ---------------------------------------------------------------------------
# action=resources_list
# ---------------------------------------------------------------------------


async def test_resources_list_returns_list() -> None:
    resources = [{"id": "r1", "url": "/local/x.js", "type": "module"}]
    mock = AsyncMock(return_value=resources)
    with patch("openclaw_node.commands.ha_config_lovelace.ha_ws_call", mock):
        result = await handle_ha_config_lovelace({"action": "resources_list"})
    assert result == {"ok": True, "count": 1, "resources": resources}
    mock.assert_awaited_once_with("lovelace/resources")


async def test_resources_list_bad_response() -> None:
    mock = AsyncMock(return_value="oops")
    with patch("openclaw_node.commands.ha_config_lovelace.ha_ws_call", mock):
        result = await handle_ha_config_lovelace({"action": "resources_list"})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_resources_list_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_AUTH", "401"))
    with patch("openclaw_node.commands.ha_config_lovelace.ha_ws_call", mock):
        result = await handle_ha_config_lovelace({"action": "resources_list"})
    assert result["error"] == "HA_AUTH"


# ---------------------------------------------------------------------------
# action=resources_create
# ---------------------------------------------------------------------------


async def test_resources_create_requires_proposal() -> None:
    result = await handle_ha_config_lovelace(
        {"action": "resources_create", "url": "/local/x.js", "res_type": "module"}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_resources_create_direct_proposal_refused() -> None:
    result = await handle_ha_config_lovelace(
        {
            "action": "resources_create",
            "url": "/local/x.js",
            "res_type": "module",
            "proposal_id": "direct",
        }
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_resources_create_missing_url() -> None:
    result = await handle_ha_config_lovelace(
        {"action": "resources_create", "res_type": "module", "proposal_id": "p1"}
    )
    assert result["error"] == "MISSING_PARAM"


async def test_resources_create_missing_res_type() -> None:
    result = await handle_ha_config_lovelace(
        {"action": "resources_create", "url": "/local/x.js", "proposal_id": "p1"}
    )
    assert result["error"] == "MISSING_PARAM"


async def test_resources_create_invalid_res_type() -> None:
    result = await handle_ha_config_lovelace(
        {
            "action": "resources_create",
            "url": "/local/x.js",
            "res_type": "exe",
            "proposal_id": "p1",
        }
    )
    assert result["error"] == "INVALID_PARAM"


async def test_resources_create_url_wrong_type() -> None:
    result = await handle_ha_config_lovelace(
        {
            "action": "resources_create",
            "url": 123,
            "res_type": "module",
            "proposal_id": "p1",
        }
    )
    assert result["error"] == "MISSING_PARAM"


async def test_resources_create_url_empty_string() -> None:
    result = await handle_ha_config_lovelace(
        {
            "action": "resources_create",
            "url": "   ",
            "res_type": "module",
            "proposal_id": "p1",
        }
    )
    assert result["error"] == "MISSING_PARAM"


async def test_resources_create_happy_path() -> None:
    ws_response = {"id": "abc", "url": "/local/x.js", "type": "module"}
    mock = AsyncMock(return_value=ws_response)
    with patch("openclaw_node.commands.ha_config_lovelace.ha_ws_call", mock):
        result = await handle_ha_config_lovelace(
            {
                "action": "resources_create",
                "url": "/local/x.js",
                "res_type": "module",
                "proposal_id": "p1",
            }
        )
    assert result == {"ok": True, "resource": ws_response, "proposal_id": "p1"}
    mock.assert_awaited_once_with(
        "lovelace/resources/create",
        {"url": "/local/x.js", "res_type": "module"},
    )


async def test_resources_create_ha_error_propagates() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_WS_ERROR", "dup"))
    with patch("openclaw_node.commands.ha_config_lovelace.ha_ws_call", mock):
        result = await handle_ha_config_lovelace(
            {
                "action": "resources_create",
                "url": "/local/x.js",
                "res_type": "module",
                "proposal_id": "p1",
            }
        )
    assert result["error"] == "HA_WS_ERROR"


# ---------------------------------------------------------------------------
# dispatcher registration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["ha.config.lovelace"])
def test_command_registered(command: str) -> None:
    assert command in _REGISTRY


def test_module_export_is_async_handler() -> None:
    assert inspect.iscoroutinefunction(handle_ha_config_lovelace)


def test_old_per_verb_commands_removed() -> None:
    for old in (
        "ha.config.lovelace.get",
        "ha.config.lovelace.save",
        "ha.config.lovelace.dashboards_list",
        "ha.config.lovelace.resources_list",
        "ha.config.lovelace.resources_create",
    ):
        assert old not in _REGISTRY
