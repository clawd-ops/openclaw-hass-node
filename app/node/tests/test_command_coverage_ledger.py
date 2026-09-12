"""Contract-ledger generation and completeness tests."""

from __future__ import annotations

import copy
import importlib.util
import json
import re
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
        "advertised_commands": 57,
        "advertised_not_registered": [],
        "assist_wrapped_commands": 31,
        "ledger_rows": 88,
        "registered_commands": 57,
        "registered_not_advertised": [],
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
    # New top-level release-tracking fields must be present.
    assert "latest_release" in ledger
    assert isinstance(ledger["commands_new_in_latest_release"], list)
    assert isinstance(ledger["commands_unreleased"], list)
    # The five genuinely unreleased commands as of origin/main.
    assert sorted(ledger["commands_unreleased"]) == [
        "ha.addon_update",
        "ha.update_install",
        "system.execApprovals.get",
        "system.execApprovals.set",
        "system.run.prepare",
    ]

    _version_re = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}[ab]\d+$")

    assert len({row["id"] for row in rows}) == len(rows)
    for row in rows:
        assert row["registered"] is True
        # Every row must carry a valid first_shipped_in.
        fsi = row.get("first_shipped_in")
        assert isinstance(fsi, str), f"row {row['id']} first_shipped_in must be a string"
        assert fsi, f"row {row['id']} first_shipped_in must not be empty"
        fsi_valid = fsi == "unreleased" or bool(_version_re.match(fsi))
        assert fsi_valid, f"row {row['id']} first_shipped_in {fsi!r} is not valid"
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
        assert rows_by_id[command]["callers"]["node_advertisement"]["status"] == "advertised"
    # `domain` used to be emitted by the Assist wrapper and ignored by the node,
    # which the ledger recorded as an acknowledged mismatch and a `fail` outcome.
    # The node now accepts `domain`, supports only `core`, and rejects anything
    # else before HA I/O, so the mismatch is retired. The row stays `partial`
    # rather than `pass`: per-domain reload is still unimplemented, the
    # admin-token gate is not the ratified authorization model, and the fix is
    # not in a released artifact.
    assert rows_by_id["ha.reload_config"]["outcome"] == "partial"
    assert (
        rows_by_id["ha.reload_config"]["callers"]["assist_wrapper"]["known_unaccepted_node_params"]
        == {}
    )
    assert "domain" in {
        parameter["name"] for parameter in rows_by_id["ha.reload_config"]["canonical_parameters"]
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


# --- first_shipped_in gate tests ---


def test_first_shipped_in_missing_fails() -> None:
    """A command without first_shipped_in is rejected at ledger build time."""
    generator = _load_generator()
    with pytest.raises(
        generator.LedgerError,
        match=r"first_shipped_in must be a non-empty string",
    ):
        generator._validate_first_shipped_in("test.cmd", None)


def test_first_shipped_in_empty_string_fails() -> None:
    """An empty first_shipped_in value is rejected."""
    generator = _load_generator()
    with pytest.raises(
        generator.LedgerError,
        match=r"first_shipped_in must be a non-empty string",
    ):
        generator._validate_first_shipped_in("test.cmd", "")


def test_first_shipped_in_non_string_fails() -> None:
    """A numeric first_shipped_in value is rejected."""
    generator = _load_generator()
    with pytest.raises(
        generator.LedgerError,
        match=r"first_shipped_in must be a non-empty string",
    ):
        generator._validate_first_shipped_in("test.cmd", 123)


def test_first_shipped_in_invalid_version_fails() -> None:
    """A value that is neither 'unreleased' nor a valid version string is rejected."""
    generator = _load_generator()
    with pytest.raises(
        generator.LedgerError,
        match=r"first_shipped_in.*neither 'unreleased' nor a valid version string",
    ):
        generator._validate_first_shipped_in("test.cmd", "not-a-version")


def test_first_shipped_in_valid_alpha_version_passes() -> None:
    """An alpha prerelease version like 2026.6.8a8 is accepted."""
    generator = _load_generator()
    generator._validate_first_shipped_in("test.cmd", "2026.6.8a8")


def test_first_shipped_in_valid_beta_version_passes() -> None:
    """A beta prerelease version like 2026.9.12b1 is accepted."""
    generator = _load_generator()
    generator._validate_first_shipped_in("test.cmd", "2026.9.12b1")


def test_first_shipped_in_unreleased_passes() -> None:
    """The sentinel value 'unreleased' is accepted."""
    generator = _load_generator()
    generator._validate_first_shipped_in("test.cmd", "unreleased")


def test_missing_first_shipped_in_in_manual_fails_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_ledger() must fail when first_shipped_in is absent from any command entry."""
    generator = _load_generator()
    original_load = generator._load_manual

    def without_first_shipped() -> Any:
        data = original_load()
        data["commands"]["ping"] = {
            k: v for k, v in data["commands"]["ping"].items() if k != "first_shipped_in"
        }
        return data

    monkeypatch.setattr(generator, "_load_manual", without_first_shipped)
    with pytest.raises(
        generator.LedgerError,
        match=r"ping first_shipped_in must be a non-empty string",
    ):
        generator.build_ledger()


def test_version_sort_key_orders_numerically_not_lexicographically() -> None:
    """`2026.6.8a8` precedes `2026.6.20b3` even though it sorts later as text."""
    generator = _load_generator()
    versions = ["2026.6.20b3", "2026.6.8a8", "2026.7.23b1", "2026.6.20b4"]
    assert sorted(versions, key=generator._version_sort_key) == [
        "2026.6.8a8",
        "2026.6.20b3",
        "2026.6.20b4",
        "2026.7.23b1",
    ]


def test_version_sort_key_orders_alpha_before_beta_same_date() -> None:
    generator = _load_generator()
    assert generator._version_sort_key("2026.6.8a8") < generator._version_sort_key("2026.6.8b1")


def test_latest_released_version_ignores_unreleased() -> None:
    generator = _load_generator()
    latest = generator._latest_released_version(
        {"a": "2026.6.8a8", "b": "2026.7.23b1", "c": "unreleased"}
    )
    assert latest == "2026.7.23b1"


def test_latest_released_version_is_none_when_all_unreleased() -> None:
    """Degrades explicitly rather than crashing when nothing has shipped yet."""
    generator = _load_generator()
    assert generator._latest_released_version({"a": "unreleased"}) is None


def test_latest_released_version_does_not_consult_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The value must come from ledger data, never from ambient git state.

    A tag-derived value differs between a local clone and CI's shallow tagless
    checkout, so the committed artifact could never match `--check`.
    """
    generator = _load_generator()

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("generator must not shell out to git")

    monkeypatch.setattr(subprocess, "run", explode)
    assert generator._latest_released_version({"a": "2026.7.23b1"}) == "2026.7.23b1"
