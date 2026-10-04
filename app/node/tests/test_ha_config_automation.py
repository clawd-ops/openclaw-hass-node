"""Tests for openclaw_node.commands.ha_config_automation."""

from __future__ import annotations

import inspect
import json
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from openclaw_node.commands.config_mutation import _USED_IDS, APPROVAL_PARAM, approval_bind
from openclaw_node.commands.dispatcher import _REGISTRY
from openclaw_node.commands.ha_config_automation import handle_ha_config_automation
from openclaw_node.ha_client import HAClientError

_FIXTURE = Path(__file__).parents[3] / "contracts" / "approval-bind-fixture.json"

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
# no action=list — HA does not expose /api/config/automation/config as a
# collection route. Enumeration is via the existing ha.list_automations
# command (which reads automation.* entities from state).
# ---------------------------------------------------------------------------


async def test_list_action_rejected() -> None:
    """`action=list` must be rejected; enumeration goes through ha.list_automations."""
    result = await handle_ha_config_automation({"action": "list"})
    assert result["ok"] is False
    assert result["error"] == "INVALID_PARAM"


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
    result = await handle_ha_config_automation({"action": "get", "id": bad_id})
    assert result["error"] == "INVALID_PARAM"


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


def _approved(params: dict[str, Any], **marker: Any) -> dict[str, Any]:
    """Params carrying a marker bound to them (fields overridable via ``marker``)."""
    body = {
        "id": str(uuid.uuid4()),
        "exp": int(time.time()) + 300,
        "bind": approval_bind("ha.config.automation", "save", params),
    }
    return {**params, APPROVAL_PARAM: {**body, **marker}}


_SAVE = {"action": "save", "id": "42", "config": {"alias": "morning", "trigger": []}}


async def _save_refused(params: dict[str, Any], code: str) -> None:
    mock = AsyncMock()
    with patch("openclaw_node.commands.ha_config_automation.ha_post", mock):
        result = await handle_ha_config_automation(params)
    assert result["ok"] is False
    assert result["error"] == code
    mock.assert_not_called()


async def test_save_missing_id() -> None:
    await _save_refused(_approved({"action": "save", "config": {"alias": "x"}}), "MISSING_PARAM")


async def test_save_invalid_id_type() -> None:
    await _save_refused(
        _approved({"action": "save", "id": 42, "config": {"alias": "x"}}), "MISSING_PARAM"
    )


async def test_save_missing_config() -> None:
    await _save_refused(_approved({"action": "save", "id": "1"}), "MISSING_PARAM")


async def test_save_config_wrong_type() -> None:
    await _save_refused(_approved({"action": "save", "id": "1", "config": "yaml"}), "MISSING_PARAM")


async def test_save_valid_marker_executes_once_without_marker() -> None:
    mock = AsyncMock(return_value={"result": "ok"})
    with patch("openclaw_node.commands.ha_config_automation.ha_post", mock):
        result = await handle_ha_config_automation(_approved(_SAVE))
    assert result == {"ok": True, "id": "42"}
    mock.assert_awaited_once_with("/api/config/automation/config/42", _SAVE["config"])


async def test_save_without_marker_is_proposal_required() -> None:
    await _save_refused(dict(_SAVE), "PROPOSAL_REQUIRED")


async def test_save_replayed_marker_refused() -> None:
    params = _approved(_SAVE)
    replay = {**params, APPROVAL_PARAM: dict(params[APPROVAL_PARAM])}
    mock = AsyncMock(return_value={"result": "ok"})
    with patch("openclaw_node.commands.ha_config_automation.ha_post", mock):
        assert (await handle_ha_config_automation(params))["ok"] is True
    await _save_refused(replay, "APPROVAL_INVALID")
    mock.assert_awaited_once()


async def test_save_tampered_params_refused() -> None:
    params = _approved(_SAVE)
    params["config"] = {"alias": "evil", "trigger": []}
    await _save_refused(params, "APPROVAL_INVALID")


async def test_save_expired_marker_refused() -> None:
    await _save_refused(_approved(_SAVE, exp=int(time.time()) - 1), "APPROVAL_INVALID")


async def test_save_marker_for_other_action_refused() -> None:
    # Marker minted for delete of the same id cannot authorize save.
    wrong = approval_bind("ha.config.automation", "delete", _SAVE)
    await _save_refused(_approved(_SAVE, bind=wrong), "APPROVAL_INVALID")


async def test_save_marker_for_other_command_refused() -> None:
    wrong = approval_bind("ha.config.scene", "save", _SAVE)
    await _save_refused(_approved(_SAVE, bind=wrong), "APPROVAL_INVALID")


@pytest.mark.parametrize(
    "marker",
    [
        "approved",
        [],
        {"id": "x", "exp": int(time.time()) + 60},
        {"id": "", "exp": int(time.time()) + 60, "bind": "b"},
        {"id": "x", "exp": True, "bind": "b"},
        {"id": "x", "exp": "9999999999", "bind": "b"},
        {"id": "x", "exp": int(time.time()) + 60, "bind": "b", "extra": 1},
    ],
)
async def test_save_malformed_marker_refused(marker: object) -> None:
    await _save_refused({**_SAVE, APPROVAL_PARAM: marker}, "APPROVAL_INVALID")


async def test_save_ha_error_propagates() -> None:
    mock = AsyncMock(side_effect=HAClientError("HA_HTTP_ERROR", "boom"))
    with patch("openclaw_node.commands.ha_config_automation.ha_post", mock):
        result = await handle_ha_config_automation(_approved(_SAVE))
    assert result["error"] == "HA_HTTP_ERROR"


async def test_expired_ids_are_evicted_from_replay_cache() -> None:
    _USED_IDS["stale"] = int(time.time()) - 1
    mock = AsyncMock(return_value={})
    with patch("openclaw_node.commands.ha_config_automation.ha_post", mock):
        await handle_ha_config_automation(_approved(_SAVE))
    assert "stale" not in _USED_IDS


def test_approval_bind_matches_cross_language_fixture() -> None:
    fixture = json.loads(_FIXTURE.read_text())
    for case in fixture["cases"]:
        assert approval_bind(case["command"], case["action"], case["params"]) == case["bind"]


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


@pytest.mark.usefixtures("trusted_config_approval_adapter_stub")
async def test_delete_missing_id() -> None:
    result = await handle_ha_config_automation({"action": "delete", "proposal_id": "p1"})
    assert result["error"] == "MISSING_PARAM"


@pytest.mark.usefixtures("trusted_config_approval_adapter_stub")
async def test_delete_invalid_id_type() -> None:
    result = await handle_ha_config_automation({"action": "delete", "id": 42, "proposal_id": "p1"})
    assert result["error"] == "MISSING_PARAM"


@pytest.mark.usefixtures("trusted_config_approval_adapter_stub")
async def test_delete_happy_path() -> None:
    mock = AsyncMock(return_value={"result": "ok"})
    with patch("openclaw_node.commands.ha_config_automation.ha_delete", mock):
        result = await handle_ha_config_automation(
            {"action": "delete", "id": "42", "proposal_id": "p1"}
        )
    assert result == {"ok": True, "id": "42", "proposal_id": "p1"}
    mock.assert_awaited_once_with("/api/config/automation/config/42")


@pytest.mark.usefixtures("trusted_config_approval_adapter_stub")
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
