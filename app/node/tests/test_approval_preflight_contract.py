"""The plugin approval-preflight.json mirrors the node's pre-gate refusals."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from openclaw_node.caller import Caller
from openclaw_node.commands import fs_move_delete, fs_patch, fs_write, ha
from openclaw_node.commands.dispatcher import dispatch_async
from openclaw_node.config import DEFAULT_ADDON_LIFECYCLE_DENYLIST

_ROOT = Path(__file__).parents[3] / "contracts"
_PREFLIGHT = (
    _ROOT.parent / "plugins/openclaw-hass-node-assist-tools/src/shared/approval-preflight.json"
)
CONTRACT: dict[str, Any] = json.loads(_PREFLIGHT.read_text())
GATED: dict[str, list[str]] = json.loads((_ROOT / "approval-gated-commands.json").read_text())[
    "gated"
]

_FLAT = {
    "ha.reload_config": ha._RELOAD_CONFIG_KEYS,
    "ha.update_install": ha._UPDATE_INSTALL_KEYS,
    "ha.addon_start": ha._LIFECYCLE_KEYS,
    "ha.addon_stop": ha._LIFECYCLE_KEYS,
    "ha.addon_restart": ha._LIFECYCLE_KEYS,
    "ha.addon_update": ha._LIFECYCLE_KEYS,
    "fs.write": fs_write._WRITE_KEYS,
    "fs.restore": fs_write._RESTORE_KEYS,
    "fs.patch": fs_patch._PATCH_KEYS,
    "fs.move": fs_move_delete._MOVE_KEYS,
    "fs.delete": fs_move_delete._DELETE_KEYS,
}


def _node_keys(command: str, action: str) -> frozenset[str]:
    if command in _FLAT:
        return _FLAT[command]
    module = importlib.import_module(
        f"openclaw_node.commands.ha_config_{command.removeprefix('ha.config.')}"
    )
    keys: frozenset[str] = module._ACTION_KEYS[action]
    return keys


def test_allowed_keys_cover_every_gated_action_and_equal_node_tables() -> None:
    assert {c: sorted(a) for c, a in GATED.items()} == {
        c: sorted(a) for c, a in CONTRACT["allowed_keys"].items()
    }
    for command, actions in CONTRACT["allowed_keys"].items():
        for action, keys in actions.items():
            assert keys == sorted(_node_keys(command, action)), (command, action)


def test_addon_policy_equals_node_policy() -> None:
    addon = CONTRACT["addon"]
    assert addon == {
        "slug_pattern": ha._ADDON_SLUG_RE.pattern,
        "max_length": ha._ADDON_SLUG_MAX_LEN,
        "core_prefix": ha._CORE_ADDON_PREFIX,
        "denylist": sorted(DEFAULT_ADDON_LIFECYCLE_DENYLIST),
    }


def test_storage_rule_equals_node_rule() -> None:
    storage = CONTRACT["storage"]
    for path in (f"{storage['marker']}core", storage["suffix"], "/config/automations.yaml"):
        assert fs_write._is_storage(path) == (
            storage["marker"] in path or path.endswith(storage["suffix"])
        )


async def test_node_refuses_storage_with_contract_message() -> None:
    result = await dispatch_async(
        "fs.write", {"path": "/config/.storage/x", "content": "x"}, caller=Caller.operator("test")
    )
    assert result["error"] == "STORAGE_READONLY"
    assert result["message"] == CONTRACT["storage"]["message"]


async def test_helpers_accept_exactly_the_contracted_dynamic_id_key() -> None:
    rule = CONTRACT["dynamic_keys"]["ha.config.helpers"]
    assert rule == {
        "actions": ["update", "delete"],
        "type_key": "helper_type",
        "key_suffix": "_id",
    }
    operator = Caller.operator("test")
    base = {"action": "delete", "helper_type": "timer"}
    own = await dispatch_async("ha.config.helpers", {**base, "timer_id": "k"}, caller=operator)
    other = await dispatch_async("ha.config.helpers", {**base, "counter_id": "k"}, caller=operator)
    assert own["error"] == "PROPOSAL_REQUIRED"
    assert other["error"] == "INVALID_PARAM"


async def test_malformed_slug_message_matches_plugin_format() -> None:
    operator = Caller.operator("test")
    for slug, rendered in [
        ("Bad Slug", "'Bad Slug'"),
        ("it's", '"it\'s"'),
        ("a\\b", "'a\\\\b'"),
    ]:
        result = await dispatch_async("ha.addon_restart", {"slug": slug}, caller=operator)
        assert result["error"] == "INVALID_PARAM"
        assert result["message"] == f"invalid addon slug: {rendered}"
