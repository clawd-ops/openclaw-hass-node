"""Every approval-gated (command, action) obeys the marker contract."""

from __future__ import annotations

import importlib
import json
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

import openclaw_node.commands.fs_write as fs_write_mod
import openclaw_node.commands.ha as ha_module
from openclaw_node import ha_client
from openclaw_node.caller import Caller
from openclaw_node.commands import config_mutation
from openclaw_node.commands.config_mutation import APPROVAL_PARAM, _canonical, approval_bind
from openclaw_node.commands.dispatcher import _REGISTRY, dispatch_async
from openclaw_node.commands.fs_write import _reset_store_for_testing

_CONTRACT = Path(__file__).parents[3] / "contracts" / "approval-gated-commands.json"
GATED: dict[str, list[str]] = json.loads(_CONTRACT.read_text())["gated"]
HA_CONFIG = {c: a for c, a in GATED.items() if c.startswith("ha.config.")}
FS = [c for c in GATED if c.startswith("fs.")]
_READS = {"get", "list", "dashboards_list", "resources_list"}
_OPERATOR = Caller.operator("test")

_TARGETS: dict[str, dict[str, Any]] = {
    "automation": {"id": "example"},
    "script": {"id": "example"},
    "scene": {"id": "example"},
    "area_registry": {"area_id": "example"},
    "device_registry": {"device_id": "example"},
    "entity_registry": {"entity_id": "input_boolean.example"},
    "config_entries": {"entry_id": "example"},
    "helpers": {"helper_type": "input_boolean", "input_boolean_id": "example"},
}
_PAYLOADS: dict[tuple[str, str], dict[str, Any]] = {
    ("automation", "save"): {"config": {"alias": "Example"}},
    ("script", "save"): {"config": {"alias": "Example"}},
    ("scene", "save"): {"config": {"name": "Example"}},
    ("lovelace", "save"): {"url_path": "example", "config": {"views": []}},
    ("lovelace", "resources_create"): {"url": "/local/example.js", "res_type": "module"},
    ("helpers", "create"): {"helper_type": "input_boolean", "attrs": {"name": "Example"}},
    ("helpers", "update"): {"attrs": {"name": "Updated"}},
    ("area_registry", "create"): {"name": "Example", "attrs": {"name": "Updated"}},
    ("area_registry", "update"): {"attrs": {"name": "Updated"}},
    ("device_registry", "update"): {"attrs": {"name": "Updated"}},
    ("entity_registry", "update"): {"attrs": {"category": "diagnostic"}},
}
_NO_TARGET = {("helpers", "create"), ("area_registry", "create")}
CASES = [(c, a) for c, actions in HA_CONFIG.items() for a in actions]


def _ha_params(command: str, action: str) -> dict[str, Any]:
    domain = command.removeprefix("ha.config.")
    params: dict[str, Any] = {"action": action}
    if (domain, action) not in _NO_TARGET:
        params.update(_TARGETS.get(domain, {}))
    params.update(_PAYLOADS.get((domain, action), {}))
    return params


def _marker(command: str, action: str, params: dict[str, Any], **override: Any) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "exp": int(time.time()) + 300,
        "bind": approval_bind(command, action, params),
        **override,
    }


@pytest.fixture
def ha_requests(monkeypatch: pytest.MonkeyPatch) -> list[AsyncMock]:
    mocks: list[AsyncMock] = []
    modules = [ha_client] + [
        importlib.import_module(f"openclaw_node.commands.ha_config_{c.removeprefix('ha.config.')}")
        for c in HA_CONFIG
    ]
    for module in modules:
        for name in ("ha_get", "ha_post", "ha_delete", "ha_ws_call"):
            if hasattr(module, name):
                mock = AsyncMock(return_value={})
                monkeypatch.setattr(module, name, mock)
                mocks.append(mock)
    return mocks


def _calls(mocks: list[AsyncMock]) -> list[Any]:
    return [call for mock in mocks for call in mock.await_args_list]


def test_contract_matches_node_handlers() -> None:
    assert {c for c in _REGISTRY if c.startswith("ha.config.")} == set(HA_CONFIG)
    for command, actions in HA_CONFIG.items():
        module = importlib.import_module(f"openclaw_node.commands.{command.replace('.', '_')}")
        assert set(actions) == module._ACTIONS - _READS, command
    assert set(FS) <= set(_REGISTRY)
    assert all(a == [""] for c, a in GATED.items() if c.startswith("fs."))


@pytest.mark.parametrize(("command", "action"), CASES)
async def test_valid_marker_executes_once_and_is_not_forwarded(
    command: str, action: str, ha_requests: list[AsyncMock]
) -> None:
    params = _ha_params(command, action)
    result = await dispatch_async(
        command, {**params, APPROVAL_PARAM: _marker(command, action, params)}, caller=_OPERATOR
    )
    assert result["ok"] is True, result
    calls = _calls(ha_requests)
    assert len(calls) == 1
    assert APPROVAL_PARAM not in repr(calls[0])


@pytest.mark.parametrize(("command", "action"), CASES)
async def test_absent_marker_is_proposal_required(
    command: str, action: str, ha_requests: list[AsyncMock]
) -> None:
    result = await dispatch_async(command, _ha_params(command, action), caller=_OPERATOR)
    assert result["error"] == "PROPOSAL_REQUIRED"
    assert _calls(ha_requests) == []


@pytest.mark.parametrize(("command", "action"), CASES)
async def test_mismatched_marker_is_approval_invalid(
    command: str, action: str, ha_requests: list[AsyncMock]
) -> None:
    params = _ha_params(command, action)
    marker = _marker(command, action, {**params, "extra": 1})
    result = await dispatch_async(command, {**params, APPROVAL_PARAM: marker}, caller=_OPERATOR)
    assert result["error"] == "APPROVAL_INVALID"
    assert _calls(ha_requests) == []


@pytest.mark.parametrize(("command", "action"), CASES)
async def test_marker_for_another_action_cannot_authorize(
    command: str, action: str, ha_requests: list[AsyncMock]
) -> None:
    params = _ha_params(command, action)
    marker = _marker(command, "other", params)
    result = await dispatch_async(command, {**params, APPROVAL_PARAM: marker}, caller=_OPERATOR)
    assert result["error"] == "APPROVAL_INVALID"
    assert _calls(ha_requests) == []


@pytest.mark.parametrize(
    ("command", "params"),
    [
        ("ha.config.automation", {"action": "get", "id": "example"}),
        ("ha.config.script", {"action": "get", "id": "example"}),
        ("ha.config.scene", {"action": "get", "id": "example"}),
        ("ha.config.helpers", {"action": "list"}),
        ("ha.config.area_registry", {"action": "list"}),
        ("ha.config.device_registry", {"action": "list"}),
        ("ha.config.entity_registry", {"action": "list"}),
        ("ha.config.entity_registry", {"action": "get", "entity_id": "input_boolean.example"}),
        ("ha.config.config_entries", {"action": "get", "entry_id": "example"}),
        ("ha.config.lovelace", {"action": "get"}),
        ("ha.config.lovelace", {"action": "dashboards_list"}),
        ("ha.config.lovelace", {"action": "resources_list"}),
    ],
)
async def test_reads_need_no_marker(
    command: str, params: dict[str, Any], ha_requests: list[AsyncMock]
) -> None:
    assert params["action"] not in HA_CONFIG[command]
    result = await dispatch_async(command, params, caller=_OPERATOR)
    assert result.get("error") != "PROPOSAL_REQUIRED"
    assert len(_calls(ha_requests)) <= 1


@pytest.mark.parametrize("helper_type", ["input_boolean", "input_number", "timer"])
async def test_helpers_marker_binds_the_helper_type(
    helper_type: str, ha_requests: list[AsyncMock]
) -> None:
    params = {"action": "delete", "helper_type": helper_type, f"{helper_type}_id": "example"}
    marker = _marker("ha.config.helpers", "delete", params)
    other = {**params, "helper_type": "counter", "counter_id": "example"}
    other.pop(f"{helper_type}_id")
    result = await dispatch_async(
        "ha.config.helpers", {**other, APPROVAL_PARAM: marker}, caller=_OPERATOR
    )
    assert result["error"] == "APPROVAL_INVALID"
    assert _calls(ha_requests) == []


def test_canonical_json_sorts_keys_by_utf16_code_unit() -> None:
    # U+1F600 encodes as D83D DE00 and sorts before U+FF5E; code-point order puts it after.
    assert _canonical({"\uff5e": 1, "\U0001f600": 2}) == '{"\U0001f600":2,"\uff5e":1}'


# --- fs.* writes: protected paths need the marker, unprotected paths ignore it ---


@pytest.fixture
def fs_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "fs"
    (root / "prot").mkdir(parents=True)
    _reset_store_for_testing(tmp_path / "store")
    monkeypatch.setenv("OPENCLAW_ALLOWED_ROOTS", str(root))
    monkeypatch.setenv("OPENCLAW_BACKUP_ROOT", str(tmp_path / "store"))
    monkeypatch.setenv("OPENCLAW_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.setattr(fs_write_mod, "_PROTECTED_ROOTS", frozenset({str(root / "prot")}))
    return root


_PATCH = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new\n"


def _fs_case(command: str, root: Path, area: str) -> tuple[dict[str, Any], Path, str, str]:
    """Return ``(params, file to inspect, content before, content after approval)``."""
    target = root / area / "f.txt"
    target.write_text("old\n")
    if command == "fs.write":
        return {"path": str(target), "content": "new\n"}, target, "old\n", "new\n"
    if command == "fs.patch":
        return {"path": str(target), "patch": _PATCH}, target, "old\n", "new\n"
    if command == "fs.restore":
        # Capture "old" as a prior version, then restore it over "newer".
        fs_write_mod._get_store().capture(
            str(target), b"old\n", proposal_id="seed", op="write", actor="test"
        )
        target.write_text("newer\n")
        return {"path": str(target), "version": 1}, target, "newer\n", "old\n"
    if command == "fs.move":
        dst = root / area / "moved.txt"
        return {"src": str(target), "dst": str(dst)}, dst, "", "old\n"
    return {"path": str(target)}, target, "old\n", ""


def _read(path: Path) -> str:
    return path.read_text() if path.exists() else ""


@pytest.mark.parametrize("command", FS)
async def test_fs_protected_valid_marker_writes_once(command: str, fs_env: Path) -> None:
    params, inspected, _before, after = _fs_case(command, fs_env, "prot")
    marker = _marker(command, "", params)
    result = await dispatch_async(command, {**params, APPROVAL_PARAM: marker}, caller=_OPERATOR)
    assert result["ok"] is True, result
    assert _read(inspected) == after
    replay = await dispatch_async(command, {**params, APPROVAL_PARAM: marker}, caller=_OPERATOR)
    assert replay["error"] in {"APPROVAL_INVALID", "NOT_FOUND"}


@pytest.mark.parametrize("command", FS)
async def test_fs_protected_absent_marker_is_proposal_required(command: str, fs_env: Path) -> None:
    params, inspected, before, _after = _fs_case(command, fs_env, "prot")
    result = await dispatch_async(command, params, caller=_OPERATOR)
    assert result["error"] == "PROPOSAL_REQUIRED"
    assert _read(inspected) == before


@pytest.mark.parametrize("command", FS)
async def test_fs_protected_invalid_marker_is_approval_invalid(command: str, fs_env: Path) -> None:
    params, inspected, before, _after = _fs_case(command, fs_env, "prot")
    marker = _marker(command, "", {**params, "extra": 1})
    result = await dispatch_async(command, {**params, APPROVAL_PARAM: marker}, caller=_OPERATOR)
    assert result["error"] == "APPROVAL_INVALID"
    assert _read(inspected) == before


@pytest.mark.parametrize("command", FS)
async def test_fs_unprotected_write_ignores_marker(command: str, fs_env: Path) -> None:
    for marker in ({"id": "x"}, "junk"):
        fresh, inspected, _before, after = _fs_case(command, fs_env, "")
        result = await dispatch_async(command, {**fresh, APPROVAL_PARAM: marker}, caller=_OPERATOR)
        assert result["ok"] is True, result
        assert _read(inspected) == after


async def test_fs_move_consumes_one_marker_for_two_protected_paths(fs_env: Path) -> None:
    params, dst, _before, after = _fs_case("fs.move", fs_env, "prot")
    marker = _marker("fs.move", "", params)
    result = await dispatch_async("fs.move", {**params, APPROVAL_PARAM: marker}, caller=_OPERATOR)
    assert result["ok"] is True
    assert _read(dst) == after


# --- Tier B admin commands: policy first, then the marker, then the effect ---

_SLUG = "my_addon"
ADMIN: dict[str, dict[str, Any]] = {
    "ha.reload_config": {"domain": "core"},
    "ha.update_install": {"entity_id": "update.example", "backup": True},
    "ha.addon_start": {"slug": _SLUG},
    "ha.addon_stop": {"slug": _SLUG},
    "ha.addon_restart": {"slug": _SLUG},
    "ha.addon_update": {"slug": _SLUG},
}


@pytest.fixture
def admin_requests(monkeypatch: pytest.MonkeyPatch) -> list[AsyncMock]:
    monkeypatch.setenv("OPENCLAW_ADDON_LIFECYCLE_ALLOWLIST", json.dumps([_SLUG]))
    mocks: list[AsyncMock] = []
    for name in ("ha_get", "ha_post", "supervisor_get_json", "supervisor_post_json"):
        mock = AsyncMock(name=name, return_value={"data": {"state": "stopped"}})
        monkeypatch.setattr(ha_module, name, mock)
        mocks.append(mock)
    return mocks


def _effects(mocks: list[AsyncMock]) -> int:
    """Count awaited mutating requests (ha_post and supervisor_post_json)."""
    return len(mocks[1].await_args_list) + len(mocks[3].await_args_list)


def test_admin_contract_matches_node_handlers() -> None:
    contract = {c: a for c, a in GATED.items() if c.startswith("ha.") and c not in HA_CONFIG}
    assert set(contract) == set(ADMIN)
    assert all(a == [""] for a in contract.values())
    assert set(ADMIN) <= set(_REGISTRY)


@pytest.mark.parametrize("command", sorted(ADMIN))
async def test_admin_valid_marker_executes_once_and_is_not_forwarded(
    command: str, admin_requests: list[AsyncMock]
) -> None:
    params = dict(ADMIN[command])
    state = "stopped" if command == "ha.addon_start" else "started"
    admin_requests[2].return_value = {"data": {"state": state}}
    marker = _marker(command, "", params)
    result = await dispatch_async(command, {**params, APPROVAL_PARAM: marker}, caller=_OPERATOR)
    assert result["ok"] is True, result
    assert _effects(admin_requests) == 1
    assert APPROVAL_PARAM not in repr(_calls(admin_requests))
    replay = await dispatch_async(command, {**params, APPROVAL_PARAM: marker}, caller=_OPERATOR)
    assert replay["error"] == "APPROVAL_INVALID"
    assert _effects(admin_requests) == 1


@pytest.mark.parametrize("command", sorted(ADMIN))
async def test_admin_absent_marker_is_proposal_required(
    command: str, admin_requests: list[AsyncMock]
) -> None:
    result = await dispatch_async(command, dict(ADMIN[command]), caller=_OPERATOR)
    assert result["error"] == "PROPOSAL_REQUIRED"
    assert _calls(admin_requests) == []


@pytest.mark.parametrize("command", sorted(ADMIN))
@pytest.mark.parametrize("flaw", ["mismatch", "other_command", "expired", "malformed"])
async def test_admin_invalid_marker_is_approval_invalid(
    command: str, flaw: str, admin_requests: list[AsyncMock]
) -> None:
    params = dict(ADMIN[command])
    markers: dict[str, Any] = {
        "mismatch": _marker(command, "", {**params, "extra": 1}),
        "other_command": _marker("ha.config.scene", "", params),
        "expired": _marker(command, "", params, exp=int(time.time()) - 1),
        "malformed": "junk",
    }
    result = await dispatch_async(
        command, {**params, APPROVAL_PARAM: markers[flaw]}, caller=_OPERATOR
    )
    assert result["error"] == "APPROVAL_INVALID"
    assert _calls(admin_requests) == []


@pytest.mark.parametrize("command", [c for c in sorted(ADMIN) if c.startswith("ha.addon_")])
async def test_admin_allowlist_refusal_precedes_approval(
    command: str, admin_requests: list[AsyncMock]
) -> None:
    params = {"slug": "other_addon"}
    marker = _marker(command, "", params)
    result = await dispatch_async(command, {**params, APPROVAL_PARAM: marker}, caller=_OPERATOR)
    assert result["error"] == "PERMISSION_DENIED"
    assert marker["id"] not in config_mutation._USED_IDS
    assert _calls(admin_requests) == []
