"""Tests for ha.config.config_entries command."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from openclaw_node.commands.dispatcher import _REGISTRY as DISPATCH
from openclaw_node.commands.ha_config_config_entries import (
    handle_ha_config_config_entries,
)
from openclaw_node.ha_client import HAClientError

pytestmark = pytest.mark.asyncio


def test_registered() -> None:
    assert DISPATCH["ha.config.config_entries"] is handle_ha_config_config_entries


async def test_missing_action() -> None:
    assert (await handle_ha_config_config_entries({}))["error"] == "INVALID_PARAM"


@pytest.mark.parametrize("bad_action", [42, [], {}, ["get"], {"action": "get"}])
async def test_action_wrong_type(bad_action: object) -> None:
    result = await handle_ha_config_config_entries({"action": bad_action, "entry_id": "e1"})
    assert result["error"] == "INVALID_PARAM"


async def test_unknown_action() -> None:
    result = await handle_ha_config_config_entries({"action": "bogus", "entry_id": "e1"})
    assert result["error"] == "INVALID_PARAM"


async def test_missing_entry_id() -> None:
    assert (await handle_ha_config_config_entries({"action": "get"}))["error"] == "MISSING_PARAM"


async def test_get_happy() -> None:
    mock = AsyncMock(return_value={"entry_id": "e1"})
    with patch("openclaw_node.commands.ha_config_config_entries.ha_ws_call", mock):
        result = await handle_ha_config_config_entries({"action": "get", "entry_id": "e1"})
    assert result["ok"] is True
    mock.assert_awaited_once_with("config_entries/get", {"entry_id": "e1"})


async def test_get_bad_response() -> None:
    mock = AsyncMock(return_value="not a dict")
    with patch("openclaw_node.commands.ha_config_config_entries.ha_ws_call", mock):
        assert (await handle_ha_config_config_entries({"action": "get", "entry_id": "e1"}))[
            "error"
        ] == "HA_BAD_RESPONSE"


async def test_get_ha_error() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_AUTH", "401"))
    with patch("openclaw_node.commands.ha_config_config_entries.ha_ws_call", mock):
        assert (await handle_ha_config_config_entries({"action": "get", "entry_id": "e1"}))[
            "error"
        ] == "HA_AUTH"


@pytest.mark.parametrize("action", ["disable", "enable", "options_flow"])
async def test_mutating_needs_proposal(action: str) -> None:
    result = await handle_ha_config_config_entries({"action": action, "entry_id": "e1"})
    assert result["error"] == "PROPOSAL_REQUIRED"


@pytest.mark.parametrize("action", ["disable", "enable", "options_flow"])
async def test_direct_proposal_refused(action: str) -> None:
    result = await handle_ha_config_config_entries(
        {"action": action, "entry_id": "e1", "proposal_id": "direct"}
    )
    assert result["error"] == "PROPOSAL_REQUIRED"


async def test_disable_happy() -> None:
    mock = AsyncMock(return_value={"disabled_by": "user"})
    with patch("openclaw_node.commands.ha_config_config_entries.ha_ws_call", mock):
        result = await handle_ha_config_config_entries(
            {"action": "disable", "entry_id": "e1", "proposal_id": "p"}
        )
    assert result["ok"] is True
    mock.assert_awaited_once_with(
        "config_entries/disable", {"entry_id": "e1", "disabled_by": "user"}
    )


async def test_enable_happy() -> None:
    mock = AsyncMock(return_value={"disabled_by": None})
    with patch("openclaw_node.commands.ha_config_config_entries.ha_ws_call", mock):
        result = await handle_ha_config_config_entries(
            {"action": "enable", "entry_id": "e1", "proposal_id": "p"}
        )
    assert result["ok"] is True
    mock.assert_awaited_once_with("config_entries/enable", {"entry_id": "e1", "disabled_by": None})


async def test_options_flow_happy() -> None:
    mock = AsyncMock(return_value={"flow_id": "f1", "type": "form"})
    with patch("openclaw_node.commands.ha_config_config_entries.ha_ws_call", mock):
        result = await handle_ha_config_config_entries(
            {"action": "options_flow", "entry_id": "e1", "proposal_id": "p"}
        )
    assert result["ok"] is True
    mock.assert_awaited_once_with("config_entries/options/flow/init", {"handler": "e1"})


async def test_options_flow_with_step() -> None:
    mock = AsyncMock(return_value={"flow_id": "f1"})
    with patch("openclaw_node.commands.ha_config_config_entries.ha_ws_call", mock):
        result = await handle_ha_config_config_entries(
            {
                "action": "options_flow",
                "entry_id": "e1",
                "proposal_id": "p",
                "step": {"foo": "bar"},
            }
        )
    assert result["ok"] is True
    mock.assert_awaited_once_with(
        "config_entries/options/flow/init",
        {"handler": "e1", "step": {"foo": "bar"}},
    )


async def test_options_flow_bad_step_type() -> None:
    result = await handle_ha_config_config_entries(
        {"action": "options_flow", "entry_id": "e1", "proposal_id": "p", "step": "no"}
    )
    assert result["error"] == "INVALID_PARAM"
