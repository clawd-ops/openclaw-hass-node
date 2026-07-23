"""Tests for openclaw_node.commands.ha_config_script."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from openclaw_node.commands.dispatcher import _REGISTRY
from openclaw_node.commands.ha_config_script import handle_ha_config_script
from openclaw_node.ha_client import HAClientError

# ---------------------------------------------------------------------------
# action dispatch (missing / unknown / invalid)
# ---------------------------------------------------------------------------


async def test_missing_action() -> None:
    result = await handle_ha_config_script({})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"


@pytest.mark.parametrize("bad_action", [42, [], {}, ["get"], {"action": "get"}])
async def test_action_wrong_type(bad_action: object) -> None:
    result = await handle_ha_config_script({"action": bad_action})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"


async def test_action_empty_string() -> None:
    result = await handle_ha_config_script({"action": "   "})
    assert result["error"] == "INVALID_PARAM"


async def test_unknown_action() -> None:
    result = await handle_ha_config_script({"action": "purge"})
    assert result["error"] == "INVALID_PARAM"


# ---------------------------------------------------------------------------
# no action=list — HA does not expose /api/config/script/config as a
# collection route. Enumeration is via `script.*` entities from state.
# ---------------------------------------------------------------------------


async def test_list_action_rejected() -> None:
    """`action=list` must be rejected; enumeration goes through state."""
    result = await handle_ha_config_script({"action": "list"})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"


# ---------------------------------------------------------------------------
# action=get
# ---------------------------------------------------------------------------


async def test_get_happy_path() -> None:
    config = {"alias": "morning", "sequence": []}
    mock = AsyncMock(return_value=config)
    with patch("openclaw_node.commands.ha_config_script.ha_get", mock):
        result = await handle_ha_config_script({"action": "get", "id": "42"})
    assert result == {"ok": True, "id": "42", "config": config}
    mock.assert_awaited_once_with("/api/config/script/config/42")


async def test_get_missing_id() -> None:
    result = await handle_ha_config_script({"action": "get"})
    assert result["error"] == "MISSING_PARAM"


async def test_get_invalid_id_type() -> None:
    result = await handle_ha_config_script({"action": "get", "id": 42})
    assert result["error"] == "MISSING_PARAM"


@pytest.mark.parametrize(
    "bad_id",
    [
        "with/slash",
        "with space",
        "UPPERCASE",
        "with?query",
        "with-hyphen",
        "with.dot",
        "with%encoded",
        "with#hash",
    ],
)
async def test_get_id_slug_validation(bad_id: str) -> None:
    result = await handle_ha_config_script({"action": "get", "id": bad_id})
    assert result["error"] == "INVALID_PARAM"


async def test_get_empty_id() -> None:
    result = await handle_ha_config_script({"action": "get", "id": "   "})
    assert result["error"] == "MISSING_PARAM"


async def test_get_bad_response_shape() -> None:
    mock = AsyncMock(return_value=["not", "a", "dict"])
    with patch("openclaw_node.commands.ha_config_script.ha_get", mock):
        result = await handle_ha_config_script({"action": "get", "id": "42"})
    assert result["error"] == "HA_BAD_RESPONSE"


async def test_get_ha_error_propagates() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_NOT_FOUND", "gone"))
    with patch("openclaw_node.commands.ha_config_script.ha_get", mock):
        result = await handle_ha_config_script({"action": "get", "id": "42"})
    assert result["error"] == "HA_NOT_FOUND"


# ---------------------------------------------------------------------------
# action=save
# ---------------------------------------------------------------------------


async def test_save_missing_proposal_id() -> None:
    result = await handle_ha_config_script({"action": "save", "id": "1", "config": {"alias": "x"}})
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_save_empty_proposal_id() -> None:
    result = await handle_ha_config_script(
        {"action": "save", "id": "1", "config": {"alias": "x"}, "proposal_id": "   "}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_save_direct_proposal_id_refused() -> None:
    result = await handle_ha_config_script(
        {"action": "save", "id": "1", "config": {"alias": "x"}, "proposal_id": "direct"}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_save_non_string_proposal_id() -> None:
    result = await handle_ha_config_script(
        {"action": "save", "id": "1", "config": {"alias": "x"}, "proposal_id": 123}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_save_missing_id() -> None:
    result = await handle_ha_config_script(
        {"action": "save", "config": {"alias": "x"}, "proposal_id": "p1"}
    )
    assert result["error"] == "MISSING_PARAM"


async def test_save_invalid_id_type() -> None:
    result = await handle_ha_config_script(
        {"action": "save", "id": 42, "config": {"alias": "x"}, "proposal_id": "p1"}
    )
    assert result["error"] == "MISSING_PARAM"


async def test_save_missing_config() -> None:
    result = await handle_ha_config_script({"action": "save", "id": "1", "proposal_id": "p1"})
    assert result["error"] == "MISSING_PARAM"


async def test_save_config_wrong_type() -> None:
    result = await handle_ha_config_script(
        {"action": "save", "id": "1", "config": "yaml", "proposal_id": "p1"}
    )
    assert result["error"] == "MISSING_PARAM"


async def test_save_happy_path() -> None:
    config = {"alias": "morning", "sequence": []}
    mock = AsyncMock(return_value={"result": "ok"})
    with patch("openclaw_node.commands.ha_config_script.ha_post", mock):
        result = await handle_ha_config_script(
            {"action": "save", "id": "42", "config": config, "proposal_id": "p1"}
        )
    assert result == {"ok": True, "id": "42", "proposal_id": "p1"}
    mock.assert_awaited_once_with("/api/config/script/config/42", config)


async def test_save_ha_error_propagates() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_HTTP_ERROR", "boom"))
    with patch("openclaw_node.commands.ha_config_script.ha_post", mock):
        result = await handle_ha_config_script(
            {"action": "save", "id": "1", "config": {"a": 1}, "proposal_id": "p1"}
        )
    assert result["error"] == "HA_HTTP_ERROR"


# ---------------------------------------------------------------------------
# action=delete
# ---------------------------------------------------------------------------


async def test_delete_missing_proposal_id() -> None:
    result = await handle_ha_config_script({"action": "delete", "id": "1"})
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_delete_empty_proposal_id() -> None:
    result = await handle_ha_config_script({"action": "delete", "id": "1", "proposal_id": "   "})
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_delete_direct_proposal_id_refused() -> None:
    result = await handle_ha_config_script({"action": "delete", "id": "1", "proposal_id": "direct"})
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_delete_non_string_proposal_id() -> None:
    result = await handle_ha_config_script({"action": "delete", "id": "1", "proposal_id": 123})
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_delete_missing_id() -> None:
    result = await handle_ha_config_script({"action": "delete", "proposal_id": "p1"})
    assert result["error"] == "MISSING_PARAM"


async def test_delete_invalid_id_type() -> None:
    result = await handle_ha_config_script({"action": "delete", "id": 42, "proposal_id": "p1"})
    assert result["error"] == "MISSING_PARAM"


async def test_delete_happy_path() -> None:
    mock = AsyncMock(return_value={"result": "ok"})
    with patch("openclaw_node.commands.ha_config_script.ha_delete", mock):
        result = await handle_ha_config_script(
            {"action": "delete", "id": "42", "proposal_id": "p1"}
        )
    assert result == {"ok": True, "id": "42", "proposal_id": "p1"}
    mock.assert_awaited_once_with("/api/config/script/config/42")


async def test_delete_ha_error_propagates() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_NOT_FOUND", "gone"))
    with patch("openclaw_node.commands.ha_config_script.ha_delete", mock):
        result = await handle_ha_config_script({"action": "delete", "id": "1", "proposal_id": "p1"})
    assert result["error"] == "HA_NOT_FOUND"


# ---------------------------------------------------------------------------
# dispatcher registration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["ha.config.script"])
def test_command_registered(command: str) -> None:
    assert command in _REGISTRY


def test_module_export_is_async_handler() -> None:
    assert inspect.iscoroutinefunction(handle_ha_config_script)


def test_no_per_verb_commands_registered() -> None:
    for old in (
        "ha.config.script.list",
        "ha.config.script.get",
        "ha.config.script.save",
        "ha.config.script.delete",
    ):
        assert old not in _REGISTRY
