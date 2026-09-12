"""Fail-closed and transactional tests for the release command stamp helper."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts/mark-commands-shipped.py"


def _load_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mark_commands_shipped", _SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load mark-commands-shipped helper")
    module = importlib.util.module_from_spec(spec)
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

    with pytest.raises(helper.ShipError, match="all tracked files restored"):
        helper._transactional_replace(replacements, replace=fail_second)

    assert {path: path.read_bytes() for path in paths} == before


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


def test_stamp_version_must_match_synchronized_tracked_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = _load_helper()
    monkeypatch.setattr(helper, "_current_version", lambda: "2026.8.1b1")

    with pytest.raises(helper.ShipError, match="does not match synchronized tracked version"):
        helper._require_current_version("2026.8.1b2")
