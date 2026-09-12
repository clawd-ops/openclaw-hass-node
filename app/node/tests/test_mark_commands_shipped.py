"""Fail-closed and transactional tests for the release command stamp helper."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts/mark-commands-shipped.py"
_BUMP_SCRIPT = _ROOT / "scripts/bump-version.py"


def _load_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mark_commands_shipped", _SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load mark-commands-shipped helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_bump_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bump_version", _BUMP_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load bump-version helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _manual(entries: dict[str, object]) -> bytes:
    return (json.dumps({"commands": entries}, indent=2) + "\n").encode()


def _fake_generator(manual: Path, outputs: dict[Path, str]) -> SimpleNamespace:
    def validate(command: str, value: object) -> None:
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"invalid first_shipped_in for {command}")

    return SimpleNamespace(
        MANUAL=manual,
        _validate_first_shipped_in=validate,
        build_ledger=lambda: {},
        _serialized_outputs=lambda: outputs,
    )


def test_malformed_command_entry_fails_before_any_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = _load_helper()
    manual = tmp_path / "manual.json"
    json_output = tmp_path / "generated.json"
    markdown_output = tmp_path / "generated.md"
    manual.write_bytes(_manual({"good": {"first_shipped_in": "unreleased"}, "bad": []}))
    json_output.write_bytes(b"json-original\n")
    markdown_output.write_bytes(b"markdown-original\n")
    before = {path: path.read_bytes() for path in (manual, json_output, markdown_output)}
    generator = _fake_generator(
        manual, {json_output: "json-new\n", markdown_output: "markdown-new\n"}
    )
    monkeypatch.setattr(helper, "MANUAL_PATH", manual)
    monkeypatch.setattr(helper, "_load_generator", lambda: generator)

    with pytest.raises(helper.ShipError, match="entry for bad must be an object"):
        helper._candidate_outputs("2026.8.1b1")

    assert {path: path.read_bytes() for path in before} == before


def test_generator_failure_leaves_every_tracked_file_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = _load_helper()
    manual = tmp_path / "manual.json"
    json_output = tmp_path / "generated.json"
    markdown_output = tmp_path / "generated.md"
    manual.write_bytes(_manual({"ping": {"first_shipped_in": "unreleased"}}))
    json_output.write_bytes(b"json-original\n")
    markdown_output.write_bytes(b"markdown-original\n")
    before = {path: path.read_bytes() for path in (manual, json_output, markdown_output)}
    generator = _fake_generator(manual, {})

    def fail_generation() -> Any:
        raise RuntimeError("injected generator failure")

    generator._serialized_outputs = fail_generation
    monkeypatch.setattr(helper, "MANUAL_PATH", manual)
    monkeypatch.setattr(helper, "_load_generator", lambda: generator)

    with pytest.raises(RuntimeError, match="injected generator failure"):
        helper._candidate_outputs("2026.8.1b1")

    assert {path: path.read_bytes() for path in before} == before


def test_zero_new_commands_still_refreshes_generated_release_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = _load_helper()
    manual = tmp_path / "manual.json"
    json_output = tmp_path / "generated.json"
    markdown_output = tmp_path / "generated.md"
    manual.write_bytes(_manual({"ping": {"first_shipped_in": "2026.7.23b1"}}))
    json_output.write_bytes(b"old-json\n")
    markdown_output.write_bytes(b"old-markdown\n")
    generator = _fake_generator(
        manual, {json_output: "new-json\n", markdown_output: "new-markdown\n"}
    )
    monkeypatch.setattr(helper, "MANUAL_PATH", manual)
    monkeypatch.setattr(helper, "_load_generator", lambda: generator)

    replacements, stamped = helper._candidate_outputs("2026.8.1b1")

    assert stamped == []
    assert replacements == {json_output: b"new-json\n", markdown_output: b"new-markdown\n"}


def test_replacement_failure_rolls_back_every_file_byte_identical(tmp_path: Path) -> None:
    helper = _load_helper()
    paths = [tmp_path / "manual.json", tmp_path / "generated.json", tmp_path / "generated.md"]
    for index, path in enumerate(paths):
        path.write_bytes(f"original-{index}\n".encode())
    before = {path: path.read_bytes() for path in paths}
    replacements = {path: f"replacement-{index}\n".encode() for index, path in enumerate(paths)}
    calls = 0

    def fail_second(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected replacement failure")
        os.replace(source, target)

    with pytest.raises(helper.ShipError, match="all attempted targets restored"):
        helper._transactional_replace(replacements, replace=fail_second)

    assert {path: path.read_bytes() for path in paths} == before


def test_candidate_creation_failure_cleans_every_prepared_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = _load_helper()
    paths = [tmp_path / "manual.json", tmp_path / "generated.json"]
    for path in paths:
        path.write_bytes(b"original\n")
    original_write = helper._write_temporary_sibling
    calls = 0

    def fail_second(
        path: Path,
        content: bytes,
        *,
        mode: int | None = None,
        times_ns: tuple[int, int] | None = None,
    ) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected candidate failure")
        return cast(Path, original_write(path, content, mode=mode, times_ns=times_ns))

    monkeypatch.setattr(helper, "_write_temporary_sibling", fail_second)

    with pytest.raises(helper.ShipError, match="candidate preparation failed; no targets changed"):
        helper._transactional_replace({path: b"new\n" for path in paths})

    assert all(path.read_bytes() == b"original\n" for path in paths)
    assert list(tmp_path.glob(".*.json.*")) == []


def test_failure_inside_candidate_write_cleans_its_own_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = _load_helper()
    target = tmp_path / "manual.json"
    target.write_bytes(b"original\n")

    def fail_fsync(_fd: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(helper.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="injected fsync failure"):
        helper._write_temporary_sibling(target, b"new\n")

    assert list(tmp_path.glob(".manual.json.*")) == []


def test_keyboard_interrupt_rolls_back_bytes_modes_and_temps(tmp_path: Path) -> None:
    helper = _load_helper()
    paths = [tmp_path / "manual.json", tmp_path / "generated.json"]
    modes = [0o640, 0o604]
    mtimes = [1_600_000_000_000_000_000, 1_600_000_001_000_000_000]
    for path, mode, mtime in zip(paths, modes, mtimes, strict=True):
        path.write_bytes(b"original\n")
        path.chmod(mode)
        os.utime(path, ns=(mtime, mtime))
    calls = 0

    def interrupt_second(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        os.replace(source, target)

    with pytest.raises(KeyboardInterrupt):
        helper._transactional_replace(
            {path: f"new-{index}\n".encode() for index, path in enumerate(paths)},
            replace=interrupt_second,
        )

    assert all(path.read_bytes() == b"original\n" for path in paths)
    assert [stat.S_IMODE(path.stat().st_mode) for path in paths] == modes
    assert [path.stat().st_mtime_ns for path in paths] == mtimes
    assert list(tmp_path.glob(".*.json.*")) == []


def test_successful_replacement_preserves_existing_target_modes(tmp_path: Path) -> None:
    helper = _load_helper()
    paths = [tmp_path / "manual.json", tmp_path / "generated.json"]
    modes = [0o640, 0o604]
    for path, mode in zip(paths, modes, strict=True):
        path.write_bytes(b"original\n")
        path.chmod(mode)

    helper._transactional_replace({path: b"new\n" for path in paths})

    assert all(path.read_bytes() == b"new\n" for path in paths)
    assert [stat.S_IMODE(path.stat().st_mode) for path in paths] == modes
    assert list(tmp_path.glob(".*.json.*")) == []


def test_missing_target_is_removed_during_rollback(tmp_path: Path) -> None:
    helper = _load_helper()
    existing = tmp_path / "manual.json"
    missing = tmp_path / "generated.json"
    existing.write_bytes(b"original\n")
    calls = 0

    def fail_second(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected replacement failure")
        os.replace(source, target)

    with pytest.raises(helper.ShipError, match="all attempted targets restored"):
        helper._transactional_replace(
            {existing: b"new-existing\n", missing: b"new-missing\n"},
            replace=fail_second,
        )

    assert existing.read_bytes() == b"original\n"
    assert not missing.exists()
    assert list(tmp_path.glob(".*.json.*")) == []


def test_rollback_failure_reports_incomplete_recovery_and_cleans_temps(tmp_path: Path) -> None:
    helper = _load_helper()
    paths = [tmp_path / "manual.json", tmp_path / "generated.json"]
    for path in paths:
        path.write_bytes(b"original\n")
    calls = 0
    rollback_calls = 0

    def fail_second(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected replacement failure")
        os.replace(source, target)

    def fail_one_rollback(source: Path, target: Path) -> None:
        nonlocal rollback_calls
        rollback_calls += 1
        if rollback_calls == 1:
            raise OSError("injected rollback failure")
        os.replace(source, target)

    with pytest.raises(helper.ShipError, match="incomplete recovery: could not restore"):
        helper._transactional_replace(
            {path: b"new\n" for path in paths},
            replace=fail_second,
            rollback_replace=fail_one_rollback,
        )

    assert list(tmp_path.glob(".*.json.*")) == []


def test_release_bump_gate_rejects_unreleased_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = _load_helper()
    manual = tmp_path / "manual.json"
    manual.write_bytes(_manual({"ping": {"first_shipped_in": "unreleased"}}))
    generator = _fake_generator(manual, {})
    monkeypatch.setattr(helper, "MANUAL_PATH", manual)
    monkeypatch.setattr(helper, "_load_generator", lambda: generator)
    monkeypatch.setattr(helper, "_version_at_ref", lambda _ref: "2026.7.23b1")
    monkeypatch.setattr(helper, "_current_version", lambda: "2026.8.1b1")
    monkeypatch.setattr(
        helper,
        "_read_manual_at_ref",
        lambda _ref, _generator: {"ping": {"first_shipped_in": "unreleased"}},
    )

    assert helper._check_release_bump("base") == 1


def test_release_bump_gate_allows_unreleased_without_version_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = _load_helper()
    manual = tmp_path / "manual.json"
    manual.write_bytes(_manual({"ping": {"first_shipped_in": "unreleased"}}))
    generator = _fake_generator(manual, {})
    monkeypatch.setattr(helper, "MANUAL_PATH", manual)
    monkeypatch.setattr(helper, "_load_generator", lambda: generator)
    monkeypatch.setattr(helper, "_version_at_ref", lambda _ref: "2026.7.23b1")
    monkeypatch.setattr(helper, "_current_version", lambda: "2026.7.23b1")

    assert helper._check_release_bump("base") == 0


def _configure_transition(
    monkeypatch: pytest.MonkeyPatch,
    helper: ModuleType,
    tmp_path: Path,
    *,
    base: dict[str, dict[str, object]],
    head: dict[str, object],
) -> None:
    manual = tmp_path / "manual.json"
    manual.write_bytes(_manual(head))
    generator = _fake_generator(manual, {})
    monkeypatch.setattr(helper, "MANUAL_PATH", manual)
    monkeypatch.setattr(helper, "_load_generator", lambda: generator)
    monkeypatch.setattr(helper, "_version_at_ref", lambda _ref: "2026.7.23b1")
    monkeypatch.setattr(helper, "_current_version", lambda: "2026.9.12rc1")
    monkeypatch.setattr(helper, "_read_manual_at_ref", lambda _ref, _generator: base)


@pytest.mark.parametrize("wrong", ["2026.6.8a8", "2026.9.13rc1"])
def test_release_transition_rejects_wrong_stamp_for_base_unreleased_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, wrong: str
) -> None:
    helper = _load_helper()
    _configure_transition(
        monkeypatch,
        helper,
        tmp_path,
        base={"pending": {"first_shipped_in": "unreleased"}},
        head={"pending": {"first_shipped_in": wrong}},
    )

    assert helper._check_release_bump("base") == 1


def test_release_transition_rejects_historical_rewrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = _load_helper()
    _configure_transition(
        monkeypatch,
        helper,
        tmp_path,
        base={"old": {"first_shipped_in": "2026.6.8a8"}},
        head={"old": {"first_shipped_in": "2026.7.23b1"}},
    )

    assert helper._check_release_bump("base") == 1


@pytest.mark.parametrize("wrong", ["unreleased", "2026.6.8a8", "2026.9.13rc1"])
def test_release_transition_rejects_wrong_stamp_for_new_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, wrong: str
) -> None:
    helper = _load_helper()
    _configure_transition(
        monkeypatch,
        helper,
        tmp_path,
        base={"old": {"first_shipped_in": "2026.6.8a8"}},
        head={
            "old": {"first_shipped_in": "2026.6.8a8"},
            "new": {"first_shipped_in": wrong},
        },
    )

    assert helper._check_release_bump("base") == 1


@pytest.mark.parametrize(
    ("head", "missing"),
    [
        ({}, "old"),
        ({"renamed": {"first_shipped_in": "2026.9.12rc1"}}, "old"),
    ],
)
def test_release_transition_rejects_removal_or_rename(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    head: dict[str, object],
    missing: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    helper = _load_helper()
    _configure_transition(
        monkeypatch,
        helper,
        tmp_path,
        base={missing: {"first_shipped_in": "2026.6.8a8"}},
        head=head,
    )

    assert helper._check_release_bump("base") == 1
    assert "removed or renamed" in capsys.readouterr().err


def test_release_transition_accepts_exact_history_and_stamps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = _load_helper()
    _configure_transition(
        monkeypatch,
        helper,
        tmp_path,
        base={
            "old": {"first_shipped_in": "2026.6.8a8"},
            "pending": {"first_shipped_in": "unreleased"},
        },
        head={
            "old": {"first_shipped_in": "2026.6.8a8"},
            "pending": {"first_shipped_in": "2026.9.12rc1"},
            "new": {"first_shipped_in": "2026.9.12rc1"},
        },
    )

    assert helper._check_release_bump("base") == 0


def test_malformed_base_ref_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _load_helper()
    monkeypatch.setattr(helper, "ROOT", _ROOT)

    with pytest.raises(helper.ShipError, match="cannot resolve base ref"):
        helper._read_ref_file("--definitely-not-a-ref", helper.VERSION_SOURCE)


@pytest.mark.parametrize(
    "version",
    ["2026.6.8a8", "2026.7.23b1", "2026.9.12rc1", "2026.9.12", "2026.9.12.dev1"],
)
def test_stamp_helper_accepts_every_canonical_release_form(version: str) -> None:
    helper = _load_helper()

    assert helper.VERSION_RE.fullmatch(version)


def test_stamp_version_rule_exactly_matches_bump_version_rule() -> None:
    helper = _load_helper()
    bump_helper = _load_bump_helper()

    assert helper.VERSION_RE.pattern == bump_helper._PEP440_RE.pattern


def test_stamp_version_must_match_synchronized_tracked_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = _load_helper()
    monkeypatch.setattr(helper, "_current_version", lambda: "2026.8.1b1")

    with pytest.raises(helper.ShipError, match="does not match synchronized tracked version"):
        helper._require_current_version("2026.8.1b2")
