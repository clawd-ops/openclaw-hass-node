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


@pytest.mark.parametrize(
    "bad",
    [
        "#316",  # the old string form
        "banana",
        "",
        "316",
        "GH-316",
        "[x](x)",
        '<a href="https://evil.invalid">x</a>',
        316.0,
        True,  # bool is an int subclass; would render as issue #1
        0,
        -3,
        None,
    ],
)
def test_issue_citations_must_be_positive_integers(
    monkeypatch: pytest.MonkeyPatch, bad: object
) -> None:
    """A citation is a number, so nothing stringlike is representable.

    This replaces a regex over citation strings. The anchor is constructed from
    the integer rather than interpolated from authored text, so link syntax, raw
    HTML and whitespace are not rejected — they cannot be expressed at all.
    `True` is excluded explicitly because `bool` subclasses `int` and would
    otherwise render as issue #1.
    """
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    manual["commands"]["ha.get_config"]["issues"] = [bad]
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)

    with pytest.raises(generator.LedgerError, match=r"issues for .* must"):
        generator.build_ledger()


def test_issue_citation_anchor_is_constructed_not_interpolated() -> None:
    """The rendered anchor derives entirely from the integer and the repo URL."""
    generator = _load_generator()

    rendered = generator._citation_link(316)

    assert rendered.endswith(">#316</a>")
    assert "/issues/316" in rendered
    assert 'target="_blank"' in rendered
    assert 'rel="noopener noreferrer"' in rendered


@pytest.mark.parametrize("falsey", [None, 0, "", False, []])
def test_explicitly_null_caller_observations_is_rejected_not_defaulted(
    monkeypatch: pytest.MonkeyPatch, falsey: object
) -> None:
    """`or {}` accepted any falsey value as "no observations".

    That let a higher-precedence null suppress inherited observations instead of
    failing validation — the same absent-versus-null defect as `issues`, which is
    why presence is now resolved centrally rather than re-walked per field.
    """
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    manual["commands"]["ha.get_config"]["caller_observations"] = falsey
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)

    with pytest.raises(generator.LedgerError, match=r"caller_observations for"):
        generator.build_ledger()


def test_absent_caller_observations_defaults_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting the key is still the documented way to mean "none"."""
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    manual["commands"]["ha.get_config"].pop("caller_observations", None)
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)

    generator.build_ledger()


@pytest.mark.parametrize("winning_scope", [0, 1, 2, 3])
def test_presence_resolution_honours_scope_precedence(winning_scope: int) -> None:
    """The highest-priority *declaring* scope wins over every lower one.

    An earlier version of this test declared the key in only one scope at a
    time, so reversing the precedence order left all four cases passing — it
    asserted that resolution finds a value, not that it finds the right one.
    Every scope below the winner now carries a conflicting value, so a reorder
    changes the result and the test fails.
    """
    generator = _load_generator()
    scopes: list[dict[str, object]] = [{}, {}, {}, {}]
    for index in range(winning_scope, 4):
        scopes[index]["issues"] = [index]

    declared, value = generator._resolve_evidence_presence("issues", *scopes)

    assert declared is True
    assert value == [winning_scope], (
        "resolution must return the highest-priority declaring scope, "
        f"expected scope {winning_scope}"
    )


@pytest.mark.parametrize("winning_scope", [0, 1, 2])
def test_manual_presence_resolution_honours_scope_precedence(winning_scope: int) -> None:
    """Same competing-declaration coverage for the three-scope manual resolver."""
    generator = _load_generator()
    scopes: list[dict[str, object]] = [{}, {}, {}]
    for index in range(winning_scope, 3):
        scopes[index]["evidence_note"] = f"scope-{index}"

    declared, value = generator._resolve_manual_presence("evidence_note", *scopes)

    assert declared is True
    assert value == f"scope-{winning_scope}"


def test_presence_resolution_reports_absence() -> None:
    """An undeclared key reports absence rather than a None value."""
    generator = _load_generator()

    declared, value = generator._resolve_evidence_presence("issues", {}, {}, {}, {})

    assert declared is False
    assert value is None


@pytest.mark.parametrize("empty", ["", "   ", None, 0, []])
def test_authored_empty_evidence_note_is_rejected_not_defaulted(
    monkeypatch: pytest.MonkeyPatch, empty: object
) -> None:
    """`or <default>` replaced an authored empty note with the fallback.

    A row could then claim "Manual reality pass; behavior is not
    contract-enforced" that nobody wrote. Omitting the key is how you accept the
    default; an empty value is not.
    """
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    manual["commands"]["ha.get_config"]["evidence_note"] = empty
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)

    with pytest.raises(generator.LedgerError, match=r"evidence_note for .* must be"):
        generator.build_ledger()


def test_omitting_evidence_note_falls_through_to_the_global_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting it on a command defers to the global scope, not the hardcoded one.

    The first version of this test asserted the hardcoded fallback and failed,
    correctly: the manual ledger declares `evidence_note` in its global defaults,
    so removing it from one command leaves a lower scope still declaring it. That
    is precedence working, and the hardcoded fallback applies only when no scope
    declares the key at all.
    """
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    manual["commands"]["ha.get_config"].pop("evidence_note", None)
    global_default = manual["defaults"]["evidence_note"]
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)

    ledger = generator.build_ledger()

    row = next(r for r in ledger["rows"] if r["id"] == "ha.get_config")
    assert row["evidence_note"] == global_default


def test_hardcoded_evidence_note_applies_only_when_no_scope_declares_it() -> None:
    """The in-code fallback is the last resort, below the global defaults."""
    generator = _load_generator()

    assert generator._evidence_note("x", {}, {}, {}) == generator._DEFAULT_EVIDENCE_NOTE


def test_explicitly_null_issues_is_rejected_not_defaulted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`"issues": null` is authored intent, not absence, and must not default.

    `_resolved_evidence_field` returns None for both cases, so an explicit null
    was silently normalised to `[]` while the surrounding comment claimed only a
    genuinely absent key defaulted.
    """
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    manual["commands"]["ha.get_config"]["issues"] = None
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)

    with pytest.raises(generator.LedgerError, match=r"explicitly null"):
        generator.build_ledger()


@pytest.mark.parametrize("container", ["", 0, False, {}, 316])
def test_issue_citations_reject_malformed_containers(
    monkeypatch: pytest.MonkeyPatch, container: object
) -> None:
    """A malformed container must not be coerced into "no citations".

    `... or []` ran before the type check, so an explicit "", 0, false or {} was
    silently accepted as an empty citation list rather than rejected. Only a
    genuinely absent value should default.
    """
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    manual["commands"]["ha.get_config"]["issues"] = container
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)

    with pytest.raises(generator.LedgerError, match=r"issues for .* must be a list"):
        generator.build_ledger()


def test_absent_issues_defaults_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuinely absent value is still the documented way to say "none"."""
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    manual["commands"]["ha.get_config"].pop("issues", None)
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)

    ledger = generator.build_ledger()

    assert next(r for r in ledger["rows"] if r["id"] == "ha.get_config")["issues"] == []


def test_issue_citations_reject_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same issue cited twice on one row is a typo, not a stronger claim."""
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    manual["commands"]["ha.get_config"]["issues"] = [316, 316]
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)

    with pytest.raises(generator.LedgerError, match=r"duplicate citation"):
        generator.build_ledger()


def test_issue_citations_accept_well_formed_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard must not reject legitimate citations."""
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    manual["commands"]["ha.get_config"]["issues"] = [316, 1, 3300]
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)

    ledger = generator.build_ledger()

    row = next(r for r in ledger["rows"] if r["id"] == "ha.get_config")
    assert row["issues"] == [316, 1, 3300]


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
    assert ledger["release_version_format"].endswith("or final")
    assert isinstance(ledger["commands_new_in_latest_release"], list)
    assert isinstance(ledger["commands_unreleased"], list)
    # What `commands_unreleased` actually contains is asserted independently in
    # test_commands_unreleased_reports_exactly_the_pending_set. It is not
    # pinned to a literal here: that is only correct *between* releases, since a
    # release sweep stamps every pending command and empties the list, which
    # made this assertion fail on the release commit itself.

    _version_re = re.compile(r"^\d+(?:\.\d+){2}(?:(?:a|b|rc)\d+|\.dev\d+)?$")

    assert len({row["id"] for row in rows}) == len(rows)
    for row in rows:
        assert row["registered"] is True
        # Every row must carry a valid first_shipped_in.
        fsi = row.get("first_shipped_in")
        assert isinstance(fsi, str), f"row {row['id']} first_shipped_in must be a string"
        assert fsi, f"row {row['id']} first_shipped_in must not be empty"
        fsi_valid = fsi == "unreleased" or bool(_version_re.fullmatch(fsi))
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


@pytest.mark.parametrize("pending_count", [0, 1, 3])
def test_commands_unreleased_reports_exactly_the_pending_set(
    monkeypatch: pytest.MonkeyPatch, pending_count: int
) -> None:
    """The generated pending list must equal the manual ledger's pending set.

    Comparing the generated list against the live manual ledger at assert time
    would be tautological: the generator derives one directly from the other,
    and `test_generated_ledger_is_current` already proves the committed artifact
    matches its source. So choose the pending set here and require the generator
    to reproduce exactly that.

    `pending_count=0` is the case that matters most. A release sweep stamps
    every pending command, so an empty list is the normal state at a release
    commit, and an assertion that silently assumed a non-empty set is what broke
    on the first cut after the ledger landed.
    """
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    names = sorted(manual["commands"])
    expected = names[:pending_count]
    for name in names:
        manual["commands"][name]["first_shipped_in"] = (
            "unreleased" if name in expected else "2026.1.1b1"
        )
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)

    ledger = generator.build_ledger()

    assert sorted(ledger["commands_unreleased"]) == expected


@pytest.mark.parametrize(
    "version",
    ["2026.6.8a8", "2026.7.23b1", "2026.9.12rc1", "2026.9.12", "2026.9.12.dev1"],
)
def test_release_metadata_accepts_every_canonical_release_form(version: str) -> None:
    generator = _load_generator()

    generator._validate_first_shipped_in("example", version)


def test_release_version_sorting_matches_pep440_stage_order() -> None:
    generator = _load_generator()
    versions = [
        "2026.9.12",
        "2026.9.12rc1",
        "2026.9.12b1",
        "2026.9.12a1",
        "2026.9.12.dev1",
    ]

    assert sorted(versions, key=generator._version_sort_key) == list(reversed(versions))


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


@pytest.mark.parametrize("value", ["2026.9.12\n", "2026.9.12suffix"])
def test_first_shipped_in_rejects_trailing_content(value: str) -> None:
    """A canonical prefix does not make a longer value valid."""
    generator = _load_generator()
    with pytest.raises(
        generator.LedgerError,
        match=r"first_shipped_in.*neither 'unreleased' nor a valid version string",
    ):
        generator._validate_first_shipped_in("test.cmd", value)


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


def test_tracked_release_version_uses_all_synchronized_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    generator = _load_generator()

    sources = []
    for index, (_, pattern) in enumerate(generator._VERSION_SOURCES):
        path = tmp_path / f"version-{index}"
        template = next(
            text
            for text in (
                'version: "2026.8.1b1"\n',
                '  io.hass.version: "2026.8.1b1"\n',
                'version = "2026.8.1b1"\n',
                '    __version__ = "2026.8.1b1"\n',
                '  "version": "2026.8.1b1"\n',
            )
            if pattern.search(text)
        )
        path.write_text(template, encoding="utf-8")
        sources.append((path, pattern))
    monkeypatch.setattr(generator, "_VERSION_SOURCES", tuple(sources))

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("generator must not shell out to git")

    monkeypatch.setattr(subprocess, "run", explode)
    assert generator._tracked_release_version() == "2026.8.1b1"


def test_tracked_release_version_rejects_source_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    generator = _load_generator()
    sources = []
    for index, (_, pattern) in enumerate(generator._VERSION_SOURCES):
        version = "2026.8.1b2" if index == 4 else "2026.8.1b1"
        text_options = (
            f'version: "{version}"\n',
            f'  io.hass.version: "{version}"\n',
            f'version = "{version}"\n',
            f'    __version__ = "{version}"\n',
            f'  "version": "{version}"\n',
        )
        path = tmp_path / f"version-{index}"
        path.write_text(
            next(text for text in text_options if pattern.search(text)), encoding="utf-8"
        )
        sources.append((path, pattern))
    monkeypatch.setattr(generator, "_VERSION_SOURCES", tuple(sources))

    with pytest.raises(generator.LedgerError, match="version drift across tracked sources"):
        generator._tracked_release_version()


def test_release_with_zero_new_commands_has_current_release_heading() -> None:
    generator = _load_generator()
    first_shipped = {"ping": "2026.7.23b1", "ha.get_state": "2026.6.8a8"}

    assert generator._commands_new_in_release(first_shipped, "2026.8.1b1") == []


# ── Provenance field validation for PRODUCTION-LIVE observations ──


def test_production_live_observation_requires_observed_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRODUCTION-LIVE observations missing observed_at must fail ledger generation."""
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    manual["commands"]["ha.get_config"]["caller_observations"] = {
        "assist_wrapper": [
            {
                "method": "PRODUCTION-LIVE",
                "outcome": "pass",
                "source": "docs/evidence/sweep-2026-09-13.md",
                "observation": "Returned config dict.",
                # observed_at deliberately absent
                "node_version": "2026.9.13b1",
            }
        ]
    }
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)
    with pytest.raises(
        generator.LedgerError,
        match=r"PRODUCTION-LIVE observation.*observed_at",
    ):
        generator.build_ledger()


def test_production_live_observation_requires_node_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRODUCTION-LIVE observations missing node_version must fail ledger generation."""
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    manual["commands"]["ha.get_config"]["caller_observations"] = {
        "assist_wrapper": [
            {
                "method": "PRODUCTION-LIVE",
                "outcome": "pass",
                "source": "docs/evidence/sweep-2026-09-13.md",
                "observation": "Returned config dict.",
                "observed_at": "2026-09-13",
                # node_version deliberately absent
            }
        ]
    }
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)
    with pytest.raises(
        generator.LedgerError,
        match=r"PRODUCTION-LIVE observation.*node_version",
    ):
        generator.build_ledger()


def test_production_live_observation_requires_valid_iso_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """observed_at must be an ISO date string (YYYY-MM-DD)."""
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    manual["commands"]["ha.get_config"]["caller_observations"] = {
        "assist_wrapper": [
            {
                "method": "PRODUCTION-LIVE",
                "outcome": "pass",
                "source": "docs/evidence/sweep-2026-09-13.md",
                "observation": "Returned config dict.",
                "observed_at": "September 13 2026",  # not ISO format
                "node_version": "2026.9.13b1",
            }
        ]
    }
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)
    with pytest.raises(
        generator.LedgerError,
        match=r"PRODUCTION-LIVE observation.*observed_at",
    ):
        generator.build_ledger()


@pytest.mark.parametrize("impossible", ["2026-02-31", "2026-13-01", "2026-00-10"])
def test_production_live_observation_rejects_an_impossible_calendar_date(
    monkeypatch: pytest.MonkeyPatch, impossible: str
) -> None:
    """A well-shaped date that never happened must not become evidence.

    The check was a bare `\\d{4}-\\d{2}-\\d{2}` shape match, so `2026-02-31`
    passed and generated a ledger. An evidence row could then claim it was
    observed on a day that does not exist.
    """
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    manual["commands"]["ha.get_config"]["caller_observations"] = {
        "assist_wrapper": [
            {
                "method": "PRODUCTION-LIVE",
                "outcome": "pass",
                "source": "docs/evidence/sweep-2026-09-13.md",
                "observation": "Returned config dict.",
                "observed_at": impossible,
                "node_version": "2026.9.13b1",
            }
        ]
    }
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)
    with pytest.raises(
        generator.LedgerError,
        match=r"PRODUCTION-LIVE observation.*observed_at",
    ):
        generator.build_ledger()


@pytest.mark.parametrize(
    ("observed_at", "node_version"),
    [
        ("2026-02-31", "2026.9.13b1"),
        ("not-a-date", "2026.9.13b1"),
        (None, "2026.9.13b1"),
        ("2026-09-13", None),
        ("2026-09-13", "not-a-version"),
    ],
)
def test_evidence_constructor_rejects_unprovenanced_live_records(
    observed_at: str | None, node_version: str | None
) -> None:
    """The live-evidence invariant belongs to the record type, not one caller.

    Validating only where manual `caller_observations` are ingested constrained a
    single path: the design-derived `system.run*` rows reached the ledger by
    skipping that path entirely. `_evidence` is the one constructor every live
    record passes through, so the check lives there.
    """
    generator = _load_generator()

    with pytest.raises(generator.LedgerError, match=r"PRODUCTION-LIVE evidence observation"):
        generator._evidence(
            "PRODUCTION-LIVE",
            "pass",
            "docs/evidence/sweep-2026-09-13.md",
            "Probe returned a result.",
            observed_at=observed_at,
            node_version=node_version,
        )


def test_evidence_constructor_accepts_a_provenanced_live_record() -> None:
    """The guard must not reject legitimate live evidence."""
    generator = _load_generator()

    item = generator._evidence(
        "PRODUCTION-LIVE",
        "pass",
        "docs/evidence/sweep-2026-09-13.md",
        "Probe returned a result.",
        observed_at="2026-09-13",
        node_version="2026.9.13b1",
    )

    assert item["observed_at"] == "2026-09-13"
    assert item["node_version"] == "2026.9.13b1"


def test_design_derived_direct_rows_are_not_claimed_as_production_live() -> None:
    """A design document is not an observation.

    The generator's `system.run*` direct-path branch emitted PRODUCTION-LIVE with
    `docs/design/AUTHORIZATION-MODEL.md` as its source. Because that branch builds
    the caller directly, it also bypassed the provenance validation applied to
    manual observations, producing live-evidence rows with no `observed_at`, no
    `node_version` and no staleness.

    `system.run` separately carries a genuine Sept 11 observation from the
    verification record, which is legitimate and must survive; `system.run.prepare`
    was never probed, so it must carry no live evidence at all.
    """
    ledger = json.loads(_LEDGER.read_text(encoding="utf-8"))
    rows_by_id = {row["id"]: row for row in ledger["rows"]}

    for command in ("system.run", "system.run.prepare"):
        evidence = rows_by_id[command]["callers"]["direct_nodes_invoke"]["evidence"]
        assert evidence, f"{command} direct path must carry evidence"
        design_sourced = [
            observation
            for observation in evidence
            if "AUTHORIZATION-MODEL.md" in observation["source"]
        ]
        assert design_sourced, f"{command} should retain its design-derived row"
        for observation in design_sourced:
            assert observation["method"] == "CODE-PROVEN", (
                f"{command}: a design document cannot be production evidence"
            )

    prepare_evidence = rows_by_id["system.run.prepare"]["callers"]["direct_nodes_invoke"][
        "evidence"
    ]
    assert not [o for o in prepare_evidence if o["method"] == "PRODUCTION-LIVE"], (
        "system.run.prepare was never probed, so it must claim no live evidence"
    )

    # No generated row anywhere may claim live evidence without provenance.
    unprovenanced = [
        f"{row['id']}/{caller_name}"
        for row in ledger["rows"]
        for caller_name, caller in row["callers"].items()
        for observation in caller.get("evidence", [])
        if observation["method"] == "PRODUCTION-LIVE"
        and not (observation.get("observed_at") and observation.get("node_version"))
    ]
    assert not unprovenanced, f"live evidence lacking provenance: {unprovenanced}"


def test_production_live_observation_requires_valid_version_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """node_version must be a valid canonical release version string."""
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    manual["commands"]["ha.get_config"]["caller_observations"] = {
        "assist_wrapper": [
            {
                "method": "PRODUCTION-LIVE",
                "outcome": "pass",
                "source": "docs/evidence/sweep-2026-09-13.md",
                "observation": "Returned config dict.",
                "observed_at": "2026-09-13",
                "node_version": "not-a-version",
            }
        ]
    }
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)
    with pytest.raises(
        generator.LedgerError,
        match=r"PRODUCTION-LIVE observation.*node_version",
    ):
        generator.build_ledger()


def test_stale_observation_flagged_when_version_mismatches_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An observation probed against an older node_version must carry stale=True."""
    generator = _load_generator()
    manual = copy.deepcopy(generator._load_manual())
    # Inject an observation with an older node_version.
    manual["commands"]["ha.get_config"]["caller_observations"] = {
        "assist_wrapper": [
            {
                "method": "PRODUCTION-LIVE",
                "outcome": "pass",
                "source": "docs/evidence/sweep-2026-09-13.md",
                "observation": "Returned config dict.",
                "observed_at": "2026-07-23",
                "node_version": "2026.7.23b1",  # older than current
            }
        ]
    }
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)

    ledger = generator.build_ledger()

    rows_by_id = {row["id"]: row for row in ledger["rows"]}
    assist_evidence = rows_by_id["ha.get_config"]["callers"]["assist_wrapper"]["evidence"]
    live_items = [e for e in assist_evidence if e["method"] == "PRODUCTION-LIVE"]
    assert live_items, "expected at least one PRODUCTION-LIVE evidence item"
    assert any(item.get("stale") is True for item in live_items), (
        "older node_version should produce stale=True on the evidence item"
    )


def test_current_observation_not_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An observation probed against the current release must carry stale=False."""
    generator = _load_generator()
    current_version = generator._tracked_release_version()
    manual = copy.deepcopy(generator._load_manual())
    manual["commands"]["ha.get_config"]["caller_observations"] = {
        "assist_wrapper": [
            {
                "method": "PRODUCTION-LIVE",
                "outcome": "pass",
                "source": "docs/evidence/sweep-2026-09-13.md",
                "observation": "Returned config dict.",
                "observed_at": "2026-09-13",
                "node_version": current_version,
            }
        ]
    }
    monkeypatch.setattr(generator, "_load_manual", lambda: manual)

    ledger = generator.build_ledger()

    rows_by_id = {row["id"]: row for row in ledger["rows"]}
    assist_evidence = rows_by_id["ha.get_config"]["callers"]["assist_wrapper"]["evidence"]
    live_items = [e for e in assist_evidence if e["method"] == "PRODUCTION-LIVE"]
    assert live_items, "expected at least one PRODUCTION-LIVE evidence item"
    assert all(item.get("stale") is False for item in live_items), (
        "current node_version should produce stale=False on the evidence item"
    )


def test_sept13_sweep_commands_have_production_live_evidence() -> None:
    """Commands probed in the Sept 13 sweep must carry PRODUCTION-LIVE evidence in the ledger."""
    ledger = json.loads(_LEDGER.read_text(encoding="utf-8"))
    rows_by_id = {row["id"]: row for row in ledger["rows"]}

    # These commands were clean passes in the Sept 13 sweep.
    expected_live_pass = [
        "ha.get_config",
        "ha.list_areas",
        "ha.list_events",
        "ha.get_state",
        "ha.check_config",
        "ha.core_logs",
        "ha.calendar_get_events",
        "ha.addon_info",
        "ha.addon_logs",
        "ha.addon_stats",
        "ha.addon_changelog",
        "ha.addon_documentation",
        "ha.list_addons",
    ]
    for command in expected_live_pass:
        row = rows_by_id[command]
        assert row["evidence_method"] == "PRODUCTION-LIVE", (
            f"{command} expected PRODUCTION-LIVE evidence_method"
        )
        assert row["outcome"] == "pass", f"{command} expected pass outcome"
        # At least one caller must have a PRODUCTION-LIVE evidence item.
        all_evidence = [ev for caller in row["callers"].values() for ev in caller["evidence"]]
        assert any(e["method"] == "PRODUCTION-LIVE" for e in all_evidence), (
            f"{command} has no PRODUCTION-LIVE evidence item in any caller"
        )

    # ha.history: direct-path alias mismatch and silent-empty fixed by #344/#345 — now pass.
    history_row = rows_by_id["ha.history"]
    assert history_row["evidence_method"] == "PRODUCTION-LIVE"
    assert history_row["outcome"] == "pass"

    # ha.logbook: direct-path key mismatch fixed by #344 — now pass.
    logbook_row = rows_by_id["ha.logbook"]
    assert logbook_row["evidence_method"] == "PRODUCTION-LIVE"
    assert logbook_row["outcome"] == "pass"

    # Oversized-pass commands: correctness confirmed but response sizes expose ergonomics gap.
    for oversized_cmd in (
        "ha.list_config_entries",
        "ha.list_devices",
        "ha.list_services",
        "ha.list_automations",
    ):
        row = rows_by_id[oversized_cmd]
        assert row["evidence_method"] == "PRODUCTION-LIVE", (
            f"{oversized_cmd} expected PRODUCTION-LIVE evidence_method"
        )
        # ha.list_automations is pass (correctness confirmed); others are partial.
        assert row["outcome"] in ("pass", "partial"), (
            f"{oversized_cmd} expected pass or partial outcome, got {row['outcome']!r}"
        )
        all_evidence = [ev for caller in row["callers"].values() for ev in caller["evidence"]]
        assert any(e["method"] == "PRODUCTION-LIVE" for e in all_evidence), (
            f"{oversized_cmd} has no PRODUCTION-LIVE evidence item in any caller"
        )

    assert rows_by_id["ha.list_entity_registry"]["evidence_method"] == "PRODUCTION-LIVE"
    assert rows_by_id["ha.list_entity_registry"]["outcome"] == "fail"


def test_sept13_second_pass_commands_have_production_live_evidence() -> None:
    """Gap-closing read-only probes must be represented on their exact command/action rows."""
    ledger = json.loads(_LEDGER.read_text(encoding="utf-8"))
    rows_by_id = {row["id"]: row for row in ledger["rows"]}

    for row_id in (
        "ping",
        "ha.supervisor_info",
        "system.which",
        "fs.list",
        "fs.stat",
        "fs.read",
        "fs.glob",
        "fs.history",
        "ha.config.area_registry#list",
        "ha.config.helpers#list",
        "ha.config.automation#get",
        "ha.config.script#get",
        "ha.config.scene#get",
        "ha.config.entity_registry#get",
        "ha.config.config_entries#get",
        "ha.config.lovelace#dashboards_list",
        "ha.config.lovelace#resources_list",
    ):
        row = rows_by_id[row_id]
        assert row["evidence_method"] == "PRODUCTION-LIVE"
        assert row["outcome"] == "pass"
        direct_evidence = row["callers"]["direct_nodes_invoke"]["evidence"]
        current_live = [
            item
            for item in direct_evidence
            if item.get("method") == "PRODUCTION-LIVE" and item.get("node_version") == "2026.9.13b1"
        ]
        assert current_live, f"{row_id} lacks current direct-node production evidence"
        assert all(item.get("stale") is False for item in current_live)

    fs_diff = rows_by_id["fs.diff"]
    assert fs_diff["evidence_method"] == "PRODUCTION-LIVE"
    assert fs_diff["outcome"] == "partial"
    diff_evidence = [
        item
        for item in fs_diff["callers"]["direct_nodes_invoke"]["evidence"]
        if item.get("method") == "PRODUCTION-LIVE"
    ]
    assert diff_evidence
    assert all(item["outcome"] == "partial" for item in diff_evidence)
    assert all(item.get("stale") is False for item in diff_evidence)

    for row_id, expected_outcome in (
        ("ha.config.device_registry#list", "partial"),
        ("ha.config.entity_registry#list", "fail"),
        ("ha.config.lovelace#get", "partial"),
    ):
        row = rows_by_id[row_id]
        assert row["evidence_method"] == "PRODUCTION-LIVE"
        assert row["outcome"] == expected_outcome
        evidence = row["callers"]["direct_nodes_invoke"]["evidence"]
        current_live = [item for item in evidence if item.get("method") == "PRODUCTION-LIVE"]
        assert current_live
        assert all(item.get("stale") is False for item in current_live)

    states = rows_by_id["ha.list_states"]
    assert states["outcome"] == "pass"
    state_evidence = states["callers"]["direct_nodes_invoke"]["evidence"]
    current_state_live = [
        item for item in state_evidence if item.get("method") == "PRODUCTION-LIVE"
    ]
    assert any(item["outcome"] == "fail" for item in current_state_live)
    assert all(item.get("stale") is False for item in current_state_live)

    approvals = rows_by_id["system.execApprovals.get"]
    assert approvals["evidence_method"] == "PRODUCTION-LIVE"
    assert approvals["outcome"] == "partial"
    refused = [
        item
        for item in approvals["callers"]["direct_nodes_invoke"]["evidence"]
        if item.get("method") == "PRODUCTION-LIVE"
    ]
    assert refused
    assert all(item["outcome"] == "refused-as-designed" for item in refused)
    assert all(item.get("stale") is False for item in refused)


def test_sept11_observations_carry_stale_provenance() -> None:
    """Sept 11 observations (node 2026.7.23b1) must be marked stale in the output ledger."""
    ledger = json.loads(_LEDGER.read_text(encoding="utf-8"))
    rows_by_id = {row["id"]: row for row in ledger["rows"]}

    # system.run direct_nodes_invoke observation was probed against 2026.7.23b1.
    system_run_evidence = rows_by_id["system.run"]["callers"]["direct_nodes_invoke"]["evidence"]
    json_live = [
        e
        for e in system_run_evidence
        if e.get("method") == "PRODUCTION-LIVE" and e.get("node_version") is not None
    ]
    assert json_live, "system.run direct_nodes_invoke must have a provenanced PRODUCTION-LIVE item"
    assert all(e.get("stale") is True for e in json_live), (
        "system.run Sept 11 observations must be stale (node_version 2026.7.23b1 != current)"
    )

    # ha.logbook assist_wrapper observation was probed against 2026.7.23b1 (Sept 11).
    # The staleness rule is the core invariant: version mismatch must produce stale=True.
    logbook_evidence = rows_by_id["ha.logbook"]["callers"]["assist_wrapper"]["evidence"]
    logbook_sept11 = [
        e
        for e in logbook_evidence
        if e.get("method") == "PRODUCTION-LIVE" and e.get("node_version") == "2026.7.23b1"
    ]
    assert logbook_sept11, (
        "ha.logbook assist_wrapper must have the Sept 11 PRODUCTION-LIVE observation"
        " (node 2026.7.23b1)"
    )
    assert all(e.get("stale") is True for e in logbook_sept11), (
        "ha.logbook Sept 11 observations must be stale (node_version 2026.7.23b1 != current)"
    )
