"""Contract-ledger generation and completeness tests."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts/generate-command-coverage.py"
_LEDGER = _ROOT / "docs/reference/command-coverage.json"


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("command_coverage_generator", _SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load command coverage generator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_ledger_is_current() -> None:
    """Committed machine and human artifacts must match current source."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--check"],
        cwd=_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_generated_ledger_has_complete_unique_rows() -> None:
    """Every generated row carries the required reality and evidence fields."""
    ledger = json.loads(_LEDGER.read_text(encoding="utf-8"))
    rows = ledger["rows"]
    assert ledger["summary"] == {
        "action_variants": 31,
        "advertised_commands": 54,
        "advertised_not_registered": [],
        "assist_wrapped_commands": 30,
        "ledger_rows": 87,
        "registered_commands": 56,
        "registered_not_advertised": ["ha.addon_update", "ha.update_install"],
        "registered_without_assist_wrapper": [
            "fs.delete",
            "fs.diff",
            "fs.glob",
            "fs.history",
            "fs.list",
            "fs.move",
            "fs.patch",
            "fs.read",
            "fs.restore",
            "fs.stat",
            "fs.write",
            "ha.config.area_registry",
            "ha.config.automation",
            "ha.config.config_entries",
            "ha.config.device_registry",
            "ha.config.entity_registry",
            "ha.config.helpers",
            "ha.config.lovelace",
            "ha.config.scene",
            "ha.config.script",
            "ping",
            "system.execApprovals.get",
            "system.execApprovals.set",
            "system.run",
            "system.run.prepare",
            "system.which",
        ],
    }
    assert len({row["id"] for row in rows}) == len(rows)
    for row in rows:
        assert row["registered"] is True
        assert set(row["callers"]) == {
            "assist_wrapper",
            "direct_nodes_invoke",
            "handler_dispatch",
            "node_advertisement",
        }
        assert row["authorization_class"]
        assert row["capability_conditions"]
        assert row["semantic_result"]
        assert row["semantic_errors"]
        assert row["evidence_method"] in ledger["evidence_methods"]
        assert row["outcome"] in ledger["outcomes"]
        assert isinstance(row["acceptance_test_ids"], list)
        assert isinstance(row["source_mentions"], list)
        if row["evidence_method"] == "TEST-PROVEN":
            assert row["acceptance_test_ids"]
        for acceptance_test in row["acceptance_test_ids"]:
            assert acceptance_test["caller"] in row["callers"], (
                f"acceptance test caller {acceptance_test['caller']!r} "
                f"not in row callers for {row['id']}"
            )
            assert acceptance_test["outcome"] in ledger["outcomes"]
            assert any(
                evidence["method"] == "TEST-PROVEN"
                and evidence["outcome"] == acceptance_test["outcome"]
                for evidence in row["callers"][acceptance_test["caller"]]["evidence"]
            )
        for caller in row["callers"].values():
            assert caller["status"]
            assert caller["source"]
            assert caller["reason"]
            assert caller["evidence"]
            for observation in caller["evidence"]:
                assert observation["method"] in ledger["evidence_methods"]
                assert observation["outcome"] in ledger["outcomes"]
        for parameter in row["canonical_parameters"]:
            assert set(parameter) == {
                "aliases",
                "bounds",
                "defaults",
                "name",
                "provenance",
            }
            assert set(parameter["provenance"]) == {
                "aliases",
                "bounds",
                "defaults",
                "name",
            }

    rows_by_id = {row["id"]: row for row in rows}
    assert rows_by_id["system.run"]["callers"]["direct_nodes_invoke"]["status"] == "unavailable"
    for command in ("ha.addon_update", "ha.update_install"):
        assert rows_by_id[command]["callers"]["node_advertisement"]["status"] == "unavailable"
    assert "PRODUCTION-LIVE" in {
        item["method"]
        for item in rows_by_id["ha.addon_update"]["callers"]["direct_nodes_invoke"]["evidence"]
    }
    assert "PRODUCTION-LIVE" not in {
        item["method"]
        for item in rows_by_id["ha.update_install"]["callers"]["direct_nodes_invoke"]["evidence"]
    }
    assert rows_by_id["ha.reload_config"]["outcome"] == "fail"
    assert rows_by_id["ha.reload_config"]["callers"]["assist_wrapper"][
        "known_unaccepted_node_params"
    ] == {
        "domain": {
            "issue": "#263",
            "reason": (
                "Node ignores requested domain and reloads core config; tracked in the "
                "completion roadmap."
            ),
        }
    }
    assert rows_by_id["ha.list_states"]["callers"]["assist_wrapper"]["client_side_params"] == {
        "entity_filter": {
            "behavior": "glob_filter_result_by_entity_id",
            "description": (
                "Filter returned state objects by entity_id glob after the unfiltered node call."
            ),
        }
    }
    for command in (
        "ha.addon_start",
        "ha.addon_stop",
        "ha.addon_restart",
        "ha.addon_update",
    ):
        assist = rows_by_id[command]["callers"]["assist_wrapper"]
        assert assist["injected_node_params"] == {}
        assert assist["known_unaccepted_node_params"] == {}
        assert any(item["outcome"] == "pass" for item in assist["evidence"])


def test_missing_registered_command_coverage_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new dispatcher command cannot land without explicit manual coverage."""
    generator = _load_generator()
    original = generator._registry()
    monkeypatch.setattr(
        generator,
        "_registry",
        lambda: {**original, "test.uncovered": ("ping", "handle_ping")},
    )
    with pytest.raises(generator.LedgerError, match=r"missing=.*test.uncovered"):
        generator.build_ledger()


def test_missing_action_variant_coverage_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A newly declared handler action cannot disappear inside a command row."""
    generator = _load_generator()
    original = generator._module_analysis

    def with_uncovered_action(
        module: str, handler: str
    ) -> tuple[list[str], dict[str, list[str]], list[str], dict[str, Any]]:
        params, defaults, actions, action_analysis = original(module, handler)
        if handler == "handle_ping":
            actions = [*actions, "new_action"]
            action_analysis = {
                **action_analysis,
                "new_action": generator.ActionAnalysis(params=[], defaults={}),
            }
        return params, defaults, actions, action_analysis

    monkeypatch.setattr(generator, "_module_analysis", with_uncovered_action)
    with pytest.raises(generator.LedgerError, match=r"action variants for ping.*new_action"):
        generator.build_ledger()


def test_new_action_parameter_coverage_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A newly reachable action parameter cannot hide behind command-wide keys."""
    generator = _load_generator()
    original = generator._module_analysis

    def with_new_save_parameter(
        module: str, handler: str
    ) -> tuple[list[str], dict[str, list[str]], list[str], dict[str, Any]]:
        params, defaults, actions, action_analysis = original(module, handler)
        if handler == "handle_ha_config_automation":
            params = [*params, "new_save_param"]
            action_analysis = copy.deepcopy(action_analysis)
            save = action_analysis["save"]
            action_analysis["save"] = generator.ActionAnalysis(
                params=[*save.params, "new_save_param"],
                defaults=save.defaults,
            )
        return params, defaults, actions, action_analysis

    monkeypatch.setattr(generator, "_module_analysis", with_new_save_parameter)
    with pytest.raises(
        generator.LedgerError,
        match=r"action parameter coverage mismatch for ha.config.automation/save; "
        r"missing=\['new_save_param'\]",
    ):
        generator.build_ledger()


def test_orphaned_action_parameter_coverage_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A removed action parameter cannot survive as stale manual coverage."""
    generator = _load_generator()
    original = generator._module_analysis

    def without_save_parameter(
        module: str, handler: str
    ) -> tuple[list[str], dict[str, list[str]], list[str], dict[str, Any]]:
        params, defaults, actions, action_analysis = original(module, handler)
        if handler == "handle_ha_config_automation":
            action_analysis = copy.deepcopy(action_analysis)
            save = action_analysis["save"]
            action_analysis["save"] = generator.ActionAnalysis(
                params=[name for name in save.params if name != "config"],
                defaults=save.defaults,
            )
        return params, defaults, actions, action_analysis

    monkeypatch.setattr(generator, "_module_analysis", without_save_parameter)
    with pytest.raises(
        generator.LedgerError,
        match=r"action parameter coverage mismatch for ha.config.automation/save;.*"
        r"orphaned=\['config'\]",
    ):
        generator.build_ledger()


def test_unknown_assist_caller_target_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """An Assist wrapper cannot target a command absent from the dispatcher."""
    generator = _load_generator()
    original = generator._assist_callers()
    monkeypatch.setattr(
        generator,
        "_assist_callers",
        lambda: {**original, "ha.unregistered": {}},
    )
    with pytest.raises(generator.LedgerError, match="Assist wrappers target unregistered"):
        generator.build_ledger()


def test_removed_assist_registration_fails_manifest_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing a registration fails manifest parity before the unavailable-reason check."""
    generator = _load_generator()
    callers = generator._assist_callers()
    callers.pop("ha.call_service")
    monkeypatch.setattr(generator, "_assist_callers", lambda: callers)
    with pytest.raises(
        generator.LedgerError,
        match=r"manifest/contract tool parity mismatch.*manifest-only=\['ha_call_service'\]",
    ):
        generator.build_ledger()


def test_wrong_assist_emitted_key_needs_acknowledged_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrapper cannot silently begin emitting a node key the handler ignores."""
    generator = _load_generator()
    callers = copy.deepcopy(generator._assist_callers())
    callers["ha.get_state"]["emitted_params"]["entity_id"] = "wrong_key"
    monkeypatch.setattr(generator, "_assist_callers", lambda: callers)
    with pytest.raises(
        generator.LedgerError,
        match=r"Assist ha.get_state node-key mismatch coverage differs; "
        r"unacknowledged=\['wrong_key'\]",
    ):
        generator.build_ledger()


def test_orphaned_assist_mismatch_acknowledgement_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resolved wrapper/node mismatch cannot remain listed as current."""
    generator = _load_generator()
    callers = copy.deepcopy(generator._assist_callers())
    callers["ha.get_state"]["known_unaccepted_node_params"] = {
        "resolved_key": {"issue": "#synthetic", "reason": "stale acknowledgement"}
    }
    monkeypatch.setattr(generator, "_assist_callers", lambda: callers)
    with pytest.raises(
        generator.LedgerError,
        match=r"Assist ha.get_state node-key mismatch coverage differs;.*"
        r"orphaned=\['resolved_key'\]",
    ):
        generator.build_ledger()


def test_assist_contract_rejects_unsupported_injected_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Policy-derived injected values must come from an implemented trusted source."""
    generator = _load_generator()
    contract = json.loads(generator.ASSIST_CONTRACT.read_text(encoding="utf-8"))
    registration = next(
        item for item in contract["registrations"] if item["tool_name"] == "ha_reload_config"
    )
    registration["injected_node_params"] = {"$policy.synthetic": "admin_token"}
    mutated = tmp_path / "assist-command-contract.json"
    mutated.write_text(json.dumps(contract), encoding="utf-8")
    monkeypatch.setattr(generator, "ASSIST_CONTRACT", mutated)
    with pytest.raises(generator.LedgerError, match="unsupported or empty injected sources"):
        generator._assist_callers()


def test_assist_contract_requires_client_side_semantics_for_null_mapping(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-node null mapping cannot silently discard a caller parameter."""
    generator = _load_generator()
    contract = json.loads(generator.ASSIST_CONTRACT.read_text(encoding="utf-8"))
    registration = next(
        item for item in contract["registrations"] if item["tool_name"] == "ha_list_states"
    )
    registration["client_side_params"] = {}
    mutated = tmp_path / "assist-command-contract.json"
    mutated.write_text(json.dumps(contract), encoding="utf-8")
    monkeypatch.setattr(generator, "ASSIST_CONTRACT", mutated)
    with pytest.raises(generator.LedgerError, match="null-mapped params need"):
        generator._assist_callers()
