#!/usr/bin/env python3
# ruff: noqa: TRY003
"""Generate and validate the command/action/caller coverage ledger.

Inventory and caller-path facts are read from the Python dispatcher, the node
connect-frame advertisement, and the Assist TypeScript wrappers.  Policy and
semantic notes that cannot be derived safely are kept in the adjacent manual
JSON file and are labelled as manual in the generated artifacts.
"""

from __future__ import annotations

import argparse
import ast
import copy
import datetime
import difflib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMMANDS_DIR = ROOT / "app/node/src/openclaw_node/commands"
DISPATCHER = COMMANDS_DIR / "dispatcher.py"
GATEWAY = ROOT / "app/node/src/openclaw_node/gateway_ws.py"
ASSIST_TOOLS = ROOT / "plugins/openclaw-hass-node-assist-tools/src/tools"
ASSIST_CONTRACT = ASSIST_TOOLS / "assist-command-contract.json"
PLUGIN_MANIFEST = ROOT / "plugins/openclaw-hass-node-assist-tools/openclaw.plugin.json"
MANUAL = ROOT / "contracts/command-coverage-manual.json"
JSON_OUTPUT = ROOT / "docs/reference/command-coverage.json"
MARKDOWN_OUTPUT = ROOT / "docs/reference/COMMAND-COVERAGE.md"

EVIDENCE_METHODS = [
    "UNVERIFIED",
    "CODE-PROVEN",
    "TEST-PROVEN",
    "DISPOSABLE-LIVE",
    "PRODUCTION-LIVE",
]

# ISO date pattern for observed_at provenance field.
_OBSERVED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_observed_at(value: object) -> bool:
    """True when *value* is a real calendar date in YYYY-MM-DD form.

    The shape check alone accepted impossible dates: `2026-02-31` matched the
    pattern and generated a ledger, so an evidence row could claim a day that
    never happened. Parsing rejects it.
    """
    if not isinstance(value, str) or not _OBSERVED_AT_RE.fullmatch(value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


OUTCOMES = ["pass", "fail", "refused-as-designed", "partial", "unverified"]
# Row callers that appear as columns in the generated ledger.
ROW_CALLERS = frozenset(
    {
        "node_advertisement",
        "direct_nodes_invoke",
        "assist_wrapper",
        "handler_dispatch",
    }
)
# Acceptance test callers must name a ROW_CALLER.
VALID_TEST_CALLERS = ROW_CALLERS

# Keep this exactly aligned with scripts/bump-version.py::_PEP440_RE.
_FIRST_SHIPPED_VERSION_RE = re.compile(r"^\d+(?:\.\d+){2}(?:(?:a|b|rc)\d+|\.dev\d+)?$")

# An issue citation is an integer, not a string. The field cannot express
# `banana`, `GH-316`, a trailing space, a Markdown link or a raw anchor, because
# none of them are representable — the anchor below is constructed from the
# number rather than interpolated from authored text. That closes the class by
# construction instead of by enumeration, and it removes the `#0` / `#007` /
# bare-`316` normalisation question, since the `#` is rendered here.
#
# This replaces a blocklist that validated citation strings, and a second one
# that scanned every manual string for live-rendering syntax. The second was
# defeated four times running — raw anchors, then Material attr-list, then
# escaped and nested link labels, then unenumerated HTML elements — and by the
# last round it had also started rejecting inert text inside backticks. A
# blocklist over a renderer's surface does not converge, so it is gone rather
# than extended again.
#
# The remaining prose fields are authored Markdown and are treated as such. That
# is the same trust level as README.md, INSTALL.md and every other document in
# this repository, none of which are escaped or scanned: they all change only
# through a reviewed pull request. Singling these four fields out bought nothing
# an author could not get by editing any other file, and cost the inline code and
# bold that 11 of them use deliberately.
#
# DO NOT REINTRODUCE A GUARD HERE without reading the next paragraph. Seeing
# authored strings interpolated unescaped into published Markdown looks like an
# injection hole, and the reflex is to add validation or escaping. Escaping was
# measured and rejected: it renders the inline code and bold those values use as
# literal backticks and asterisks. Validation was tried and deleted after leaking
# four times. The framing that resolves it is that this is not untrusted input
# crossing a boundary — there is no boundary here to defend.
#
# THE CONDITION THAT REVERSES THIS: the argument holds only while the manual
# ledger is written exclusively through reviewed pull requests. If evidence
# ingestion ever becomes automatic from a source no human reads — a live probe
# dump, an external artifact, any machine-written content landing in these fields
# without appearing in a reviewed diff — the trust level changes and so does the
# conclusion, and escaping or validation becomes correct at that point.
#
# This is not hypothetical. Systematic ingestion is being planned. It stays fine
# for as long as the intermediate lands in-repo and passes through review; it
# stops being fine the moment it does not. Whoever automates that ingestion owns
# revisiting this comment.


_MKDOCS_REPO_URL_RE = re.compile(r"^repo_url:\s*(\S+)\s*$", re.MULTILINE)


def _repo_url() -> str:
    """Return the canonical repository URL, from the one place it is declared.

    Derived from `mkdocs.yml` rather than written here so a fork or a rename
    updates one file instead of silently producing citation links that all point
    at the upstream repository.
    """
    text = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    match = _MKDOCS_REPO_URL_RE.search(text)
    if match is None:
        raise LedgerError("mkdocs.yml has no repo_url; cannot build issue citation links")
    return match.group(1).rstrip("/")


def _row_anchor(row_id: str) -> str:
    """Return the in-page anchor id for a coverage row.

    Both the link in the Coverage rows table and the `{#id}` on the Row details
    heading are built from this one call, so they cannot drift. The alternative,
    inferring the id that Python-Markdown's slugifier would produce, means
    reimplementing that slugifier and silently breaking every anchor on the page
    if it ever changes. Row ids contain backticks, dots and `#`, which is exactly
    the input where an inferred slug is least predictable.

    `attr_list` (enabled in mkdocs.yml) is what makes the explicit id possible.

    The whole row id is slugified, not just the command: action variants such as
    `ha.config.lovelace#get` share a command with other rows, so slugifying the
    command alone would collide.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", row_id.lower()).strip("-")
    return f"row-{slug}"


def _assert_unique_anchors(rows: list[dict[str, Any]]) -> None:
    """Fail generation when two rows would share an anchor.

    A duplicate id is not a rendering error: the page builds, both links point at
    the first occurrence, and the second row becomes unreachable with nothing to
    indicate it. Failing here is the only place it is visible.
    """
    seen: dict[str, str] = {}
    for row in rows:
        anchor = _row_anchor(row["id"])
        if anchor in seen:
            raise LedgerError(
                f"rows {seen[anchor]!r} and {row['id']!r} both slugify to "
                f"{anchor!r}; one would be unreachable"
            )
        seen[anchor] = row["id"]


def _assert_anchors_round_trip(document: str, rows: list[dict[str, Any]]) -> None:
    """Fail when a row has no detail section, or a section has no row.

    The two sides are generated from one list, so they agree by construction
    today. That is exactly why this is worth asserting: the check costs nothing
    now and catches the case where a future edit filters one loop and not the
    other. A row whose detail block is missing renders as a link into nothing,
    and a section nothing points at is unreachable; both build cleanly.
    """
    expected = {_row_anchor(row["id"]) for row in rows}
    linked = set(re.findall(r"\]\(#(row-[a-z0-9-]+)\)", document))
    targeted = set(re.findall(r"\{#(row-[a-z0-9-]+)\}", document))
    if linked != expected:
        missing = ", ".join(sorted(expected - linked)) or "none"
        extra = ", ".join(sorted(linked - expected)) or "none"
        raise LedgerError(f"table links do not match rows; missing: {missing}; unexpected: {extra}")
    if targeted != expected:
        missing = ", ".join(sorted(expected - targeted)) or "none"
        extra = ", ".join(sorted(targeted - expected)) or "none"
        raise LedgerError(
            f"detail anchors do not match rows; missing: {missing}; unexpected: {extra}"
        )


def _citation_link(citation: int) -> str:
    """Render `#123` as an anchor that opens outside the docs view.

    The docs are served through a token-gated portal proxy, so a same-tab
    navigation to GitHub either takes the operator out of the portal or is
    bounced by the relay auth boundary (#335 records the portal stripping launch
    tokens on sub-pages). `target="_blank"` keeps the ledger view intact, and
    `rel="noopener noreferrer"` is required with it to avoid handing the opened
    page a reference back to this one.
    """
    url = f"{_repo_url()}/issues/{citation}"
    return f'<a href="{url}" target="_blank" rel="noopener noreferrer">#{citation}</a>'


# The current release is read from all five tracked version sources. Generated
# artifacts must not depend on command history or ambient git tags, and version
# drift must fail generation instead of choosing one source arbitrarily.
_VERSION_SOURCES: tuple[tuple[Path, re.Pattern[str]], ...] = (
    (ROOT / "app/config.yaml", re.compile(r'^version: "([^"]+)"$', re.MULTILINE)),
    (
        ROOT / "app/build.yaml",
        re.compile(r'^  io\.hass\.version: "([^"]+)"$', re.MULTILINE),
    ),
    (
        ROOT / "app/node/pyproject.toml",
        re.compile(r'^version = "([^"]+)"$', re.MULTILINE),
    ),
    (
        ROOT / "app/node/src/openclaw_node/__init__.py",
        re.compile(r'^    __version__ = "([^"]+)"$', re.MULTILINE),
    ),
    (
        ROOT / "custom_components/openclaw_hass_node_assist/manifest.json",
        re.compile(r'^  "version": "([^"]+)"$', re.MULTILINE),
    ),
)


def _version_sort_key(version: str) -> tuple[int, int, int, int, int]:
    """Order supported release versions numerically, not lexicographically.

    Lexicographic ordering is wrong here: `2026.6.20b3` sorts before `2026.6.8a8`
    as text, because `2` precedes `8`. PEP 440 stage order is development,
    alpha, beta, release candidate, then final.
    """
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+)|\.dev(\d+))?", version)
    if not match:  # pragma: no cover - guarded by _validate_first_shipped_in
        raise ValueError(f"unsortable version: {version!r}")
    major, minor, patch, stage, serial, dev_serial = match.groups()
    stage_order = {"a": 1, "b": 2, "rc": 3, None: 4}
    if dev_serial is not None:
        stage_rank, stage_serial = 0, int(dev_serial)
    else:
        stage_rank = stage_order[stage]
        stage_serial = 0 if serial is None else int(serial)
    return (int(major), int(minor), int(patch), stage_rank, stage_serial)


def _tracked_release_version() -> str:
    """Return the synchronized release version from tracked project sources."""
    versions: dict[str, str] = {}
    for path, pattern in _VERSION_SOURCES:
        text = path.read_text(encoding="utf-8")
        match = pattern.search(text)
        if match is None:
            raise LedgerError(f"version pattern did not match in {path}")
        try:
            label = path.relative_to(ROOT).as_posix()
        except ValueError:
            label = str(path)
        versions[label] = match.group(1)

    distinct = set(versions.values())
    if len(distinct) != 1:
        raise LedgerError(f"version drift across tracked sources: {versions}")
    version = next(iter(distinct))
    if not _FIRST_SHIPPED_VERSION_RE.fullmatch(version):
        raise LedgerError(f"tracked release version {version!r} is not a supported release version")
    return version


def _commands_new_in_release(first_shipped_by_command: dict[str, str], release: str) -> list[str]:
    """Return commands introduced in *release*, including an empty release."""
    return sorted(cmd for cmd, version in first_shipped_by_command.items() if version == release)


def _validate_first_shipped_in(command: str, value: object) -> None:
    """Raise LedgerError when first_shipped_in is absent, empty, non-string, or malformed."""
    if not isinstance(value, str) or not value:
        raise LedgerError(
            f"command {command} first_shipped_in must be a non-empty string, got {value!r}"
        )
    if value != "unreleased" and not _FIRST_SHIPPED_VERSION_RE.fullmatch(value):
        raise LedgerError(
            f"command {command} first_shipped_in {value!r} is neither 'unreleased' nor a "
            "valid version string supported for releases (for example '2026.6.8a8', "
            "'2026.9.12rc1', "
            "or '2026.9.12')"
        )


class LedgerError(RuntimeError):
    """Raised when source inventory and manual coverage do not reconcile."""


def _literal_string_collection(node: ast.AST) -> list[str]:
    """Return string members from a literal list/set/frozenset expression."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "frozenset"
    ):
        if len(node.args) != 1:
            raise LedgerError("frozenset inventory must have exactly one positional argument")
        node = node.args[0]
    if not isinstance(node, ast.List | ast.Tuple | ast.Set):
        raise LedgerError(f"expected a literal string collection, got {ast.dump(node)}")
    values: list[str] = []
    for item in node.elts:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            raise LedgerError("inventory collections must contain only literal strings")
        values.append(item.value)
    return values


def _assigned_value(tree: ast.Module, name: str) -> ast.AST | None:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return node.value
    return None


def _registry() -> dict[str, tuple[str, str]]:
    tree = ast.parse(DISPATCHER.read_text(encoding="utf-8"), filename=str(DISPATCHER))
    imports: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if not node.module.startswith("openclaw_node.commands."):
            continue
        module = node.module.rsplit(".", 1)[-1]
        for alias in node.names:
            imports[alias.asname or alias.name] = module

    registry_node = _assigned_value(tree, "_REGISTRY")
    if not isinstance(registry_node, ast.Dict):
        raise LedgerError("dispatcher _REGISTRY must be a literal dict")
    result: dict[str, tuple[str, str]] = {}
    for key, value in zip(registry_node.keys, registry_node.values, strict=True):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise LedgerError("dispatcher keys must be literal strings")
        if not isinstance(value, ast.Name) or value.id not in imports:
            raise LedgerError(f"handler for {key.value!r} must be an imported name")
        result[key.value] = (imports[value.id], value.id)
    return result


def _advertised() -> list[str]:
    tree = ast.parse(GATEWAY.read_text(encoding="utf-8"), filename=str(GATEWAY))
    node = _assigned_value(tree, "_NODE_COMMANDS")
    if node is None:
        raise LedgerError("gateway _NODE_COMMANDS inventory not found")
    return _literal_string_collection(node)


def _assist_callers() -> dict[str, dict[str, Any]]:
    value = json.loads(ASSIST_CONTRACT.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise LedgerError("Assist command contract schema_version must be 1")
    registrations = value.get("registrations")
    if not isinstance(registrations, list):
        raise LedgerError("Assist command contract registrations must be a list")
    found: dict[str, dict[str, Any]] = {}
    unique_fields: dict[str, set[str]] = {
        "tool_name": set(),
        "descriptor": set(),
        "factory": set(),
        "node_command": set(),
    }
    for registration in registrations:
        if not isinstance(registration, dict):
            raise LedgerError("Assist command registrations must be objects")
        for field, seen in unique_fields.items():
            item = registration.get(field)
            if not isinstance(item, str) or not item:
                raise LedgerError(f"Assist registration field {field} must be a nonempty string")
            if item in seen:
                raise LedgerError(f"duplicate Assist registration {field}: {item}")
            seen.add(item)
        command = registration["node_command"]
        accepted = registration.get("accepted_tool_params")
        emitted = registration.get("emitted_params")
        injected = registration.get("injected_node_params")
        known_unaccepted = registration.get("known_unaccepted_node_params", {})
        if not isinstance(accepted, list) or not all(isinstance(item, str) for item in accepted):
            raise LedgerError(f"Assist {command} accepted_tool_params must be a string list")
        if len(accepted) != len(set(accepted)) or "node" not in accepted:
            raise LedgerError(f"Assist {command} tool params must be unique and include node")
        if not isinstance(emitted, dict) or set(emitted) != set(accepted):
            raise LedgerError(f"Assist {command} emitted_params must map every accepted tool param")
        if not all(value is None or isinstance(value, str) for value in emitted.values()):
            raise LedgerError(f"Assist {command} emitted param targets must be strings or null")
        if not isinstance(injected, dict) or not all(
            isinstance(key, str) and isinstance(target, str) for key, target in injected.items()
        ):
            raise LedgerError(f"Assist {command} injected_node_params must be a string map")
        unsupported_injected = set(injected) - {"$policy.adminToken"}
        if unsupported_injected or any(not target for target in injected.values()):
            raise LedgerError(
                f"Assist {command} has unsupported or empty injected sources: "
                f"{sorted(unsupported_injected)}"
            )
        if not isinstance(known_unaccepted, dict) or not all(
            isinstance(key, str)
            and isinstance(mismatch, dict)
            and isinstance(mismatch.get("issue"), str)
            and bool(mismatch["issue"])
            and isinstance(mismatch.get("reason"), str)
            and bool(mismatch["reason"])
            for key, mismatch in known_unaccepted.items()
        ):
            raise LedgerError(
                f"Assist {command} known_unaccepted_node_params must map keys to issue/reason"
            )
        transforms = registration.get("value_transforms", {})
        if not isinstance(transforms, dict):
            raise LedgerError(f"Assist {command} value_transforms must be an object")
        allowed_transforms = {"wrap_array", "alias_merge"}
        emitted_node_keys = {v for v in emitted.values() if isinstance(v, str)}
        for node_key, entry in transforms.items():
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("source_tool_key"), str)
                or not entry["source_tool_key"]
                or not isinstance(entry.get("transform"), str)
                or not entry["transform"]
                or not isinstance(entry.get("description"), str)
                or not entry["description"]
            ):
                raise LedgerError(
                    f"Assist {command} value_transforms[{node_key}] must have "
                    f"source_tool_key, transform, and description strings"
                )
            xform = entry["transform"]
            if xform not in allowed_transforms:
                raise LedgerError(
                    f"Assist {command} value_transforms[{node_key}] transform "
                    f"{xform!r} not in {sorted(allowed_transforms)}"
                )
            if node_key not in emitted_node_keys:
                raise LedgerError(
                    f"Assist {command} value_transforms[{node_key}] target is "
                    f"not an emitted non-null node key"
                )
            source_keys = [k.strip() for k in entry["source_tool_key"].split("|") if k.strip()]
            if not source_keys:
                raise LedgerError(
                    f"Assist {command} value_transforms[{node_key}] source_tool_key is empty"
                )
            for sk in source_keys:
                if sk not in accepted:
                    raise LedgerError(
                        f"Assist {command} value_transforms[{node_key}] "
                        f"source_tool_key {sk!r} is not an accepted tool param"
                    )
            if xform == "wrap_array":
                if len(source_keys) != 1:
                    raise LedgerError(
                        f"Assist {command} value_transforms[{node_key}] "
                        f"wrap_array requires exactly one source key"
                    )
                if emitted.get(source_keys[0]) != node_key:
                    raise LedgerError(
                        f"Assist {command} value_transforms[{node_key}] "
                        f"wrap_array source must map to this target"
                    )
            elif xform == "alias_merge":
                if len(source_keys) < 2:
                    raise LedgerError(
                        f"Assist {command} value_transforms[{node_key}] "
                        f"alias_merge requires at least two source keys"
                    )
                for sk in source_keys:
                    if emitted.get(sk) != node_key:
                        raise LedgerError(
                            f"Assist {command} value_transforms[{node_key}] "
                            f"alias_merge source {sk!r} must map to this target"
                        )
        client_side = registration.get("client_side_params", {})
        if not isinstance(client_side, dict):
            raise LedgerError(f"Assist {command} client_side_params must be an object")
        allowed_client_behaviors = {"glob_filter_result_by_entity_id"}
        for tool_key, client_entry in client_side.items():
            if (
                tool_key not in accepted
                or emitted.get(tool_key) is not None
                or not isinstance(client_entry, dict)
                or client_entry.get("behavior") not in allowed_client_behaviors
                or not isinstance(client_entry.get("description"), str)
                or not client_entry["description"]
            ):
                raise LedgerError(
                    f"Assist {command} client_side_params[{tool_key}] is malformed, "
                    "unsupported, or not an accepted null-mapped param"
                )
        missing_client_side = {
            key
            for key, target in emitted.items()
            if key != "node" and target is None and key not in client_side
        }
        if missing_client_side:
            raise LedgerError(
                f"Assist {command} null-mapped params need client_side_params metadata: "
                f"{sorted(missing_client_side)}"
            )
        registration["client_side_params"] = client_side
        found[command] = registration
    return dict(sorted(found.items()))


def _named_string_collections(tree: ast.Module) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for name in ("_ACTIONS", "_MUTATING_ACTIONS"):
        node = _assigned_value(tree, name)
        if node is not None:
            result[name] = set(_literal_string_collection(node))
    return result


def _condition_for_action(
    node: ast.AST, action: str, named_collections: dict[str, set[str]]
) -> bool | None:
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return None
    if not isinstance(node.left, ast.Name) or node.left.id != "action":
        return None
    comparator = node.comparators[0]
    operator = node.ops[0]
    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
        if isinstance(operator, ast.Eq):
            return action == comparator.value
        if isinstance(operator, ast.NotEq):
            return action != comparator.value
    values: set[str] | None = None
    if isinstance(comparator, ast.Name):
        values = named_collections.get(comparator.id)
    elif isinstance(comparator, ast.Set | ast.Tuple | ast.List):
        values = set(_literal_string_collection(comparator))
    if values is not None:
        if isinstance(operator, ast.In):
            return action in values
        if isinstance(operator, ast.NotIn):
            return action not in values
    return None


def _action_parameters(
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    handler_name: str,
    action: str,
    named_collections: dict[str, set[str]],
) -> tuple[set[str], dict[str, set[str]]]:
    """Follow one action's handler path and return its exact accepted key set and defaults."""
    params: set[str] = set()
    action_defaults: dict[str, set[str]] = defaultdict(set)
    visited: set[tuple[str, str | None]] = set()

    def visit_function(name: str, selected_action: str | None) -> None:
        marker = (name, selected_action)
        if marker in visited or name not in functions:
            return
        visited.add(marker)
        function = functions[name]
        dynamic_keys: dict[str, str] = {}
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.JoinedStr)
            ):
                pieces: list[str] = []
                for value in node.value.values:
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        pieces.append(value.value)
                    elif isinstance(value, ast.FormattedValue) and isinstance(
                        value.value, ast.Name
                    ):
                        pieces.append(f"<{value.value.id}>")
                    else:
                        pieces = []
                        break
                if pieces:
                    dynamic_keys[node.targets[0].id] = "".join(pieces)

        def visit_node(node: ast.AST) -> None:
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "get"
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id == "params"
                    and child.args
                ):
                    key_node = child.args[0]
                    if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                        params.add(key_node.value)
                        default = ast.unparse(child.args[1]) if len(child.args) >= 2 else "null"
                        action_defaults[key_node.value].add(default)
                    elif isinstance(key_node, ast.Name) and key_node.id in dynamic_keys:
                        params.add(dynamic_keys[key_node.id])
                        default = ast.unparse(child.args[1]) if len(child.args) >= 2 else "null"
                        action_defaults[dynamic_keys[key_node.id]].add(default)
                elif (
                    isinstance(child, ast.Subscript)
                    and isinstance(child.value, ast.Name)
                    and child.value.id == "params"
                    and isinstance(child.slice, ast.Constant)
                    and isinstance(child.slice.value, str)
                ):
                    params.add(child.slice.value)
                    action_defaults[child.slice.value].add("required-or-validated-before-access")
                elif (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id in functions
                ):
                    visit_function(child.func.id, None)
            for loop in (child for child in ast.walk(node) if isinstance(child, ast.For)):
                if not isinstance(loop.iter, ast.Tuple | ast.List | ast.Set):
                    continue
                keys = [
                    item.value
                    for item in loop.iter.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                ]
                if len(keys) == len(loop.iter.elts) and any(
                    isinstance(child, ast.Name) and child.id == "params" for child in ast.walk(loop)
                ):
                    params.update(keys)

        def visit_block(statements: list[ast.stmt]) -> bool:
            for statement in statements:
                if isinstance(statement, ast.If):
                    visit_node(statement.test)
                    decision = (
                        _condition_for_action(statement.test, selected_action, named_collections)
                        if selected_action is not None
                        else None
                    )
                    if decision is not None:
                        chosen = statement.body if decision else statement.orelse
                        if visit_block(chosen):
                            return True
                        continue
                    body_returns = visit_block(statement.body)
                    else_returns = visit_block(statement.orelse) if statement.orelse else False
                    if body_returns and else_returns:
                        return True
                    continue
                visit_node(statement)
                if isinstance(statement, ast.Return):
                    return True
            return False

        visit_block(function.body)

    visit_function(handler_name, action)
    return params, dict(action_defaults)


class ActionAnalysis:
    """Per-action parameter keys and defaults."""

    __slots__ = ("defaults", "params")

    def __init__(self, params: list[str], defaults: dict[str, list[str]]) -> None:
        """Store parameter names and their source-derived defaults."""
        self.params = params
        self.defaults = defaults


def _module_analysis(
    module: str, handler_name: str
) -> tuple[list[str], dict[str, list[str]], list[str], dict[str, ActionAnalysis]]:
    """Return accepted param names/default expressions and declared actions.

    Parameter discovery follows same-module calls reachable from the registered
    handler.  For action-based HA config modules, all helper functions are
    included because action dispatch deliberately fans out to private adapters.
    This is a source inventory, not a runtime schema claim.
    """
    path = COMMANDS_DIR / f"{module}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    actions_node = _assigned_value(tree, "_ACTIONS")
    actions = sorted(_literal_string_collection(actions_node)) if actions_node is not None else []
    named_collections = _named_string_collections(tree)

    reachable: set[str] = set()
    pending = [handler_name]
    while pending:
        name = pending.pop()
        if name in reachable or name not in functions:
            continue
        reachable.add(name)
        for node in ast.walk(functions[name]):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in functions
                and node.func.id not in reachable
            ):
                pending.append(node.func.id)
    if actions:
        reachable.update(functions)

    params: set[str] = set()
    defaults: dict[str, set[str]] = defaultdict(set)
    for name in sorted(reachable):
        function = functions[name]
        for loop in (node for node in ast.walk(function) if isinstance(node, ast.For)):
            if not isinstance(loop.target, ast.Name) or not isinstance(
                loop.iter, ast.Tuple | ast.List | ast.Set
            ):
                continue
            loop_keys = [
                item.value
                for item in loop.iter.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]
            references_params = any(
                isinstance(child, ast.Name) and child.id == "params"
                for statement in loop.body
                for child in ast.walk(statement)
            )
            if references_params and len(loop_keys) == len(loop.iter.elts):
                params.update(loop_keys)
                for key in loop_keys:
                    defaults[key].add("null")
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "params"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                key = node.args[0].value
                params.add(key)
                default = ast.unparse(node.args[1]) if len(node.args) >= 2 else "null"
                defaults[key].add(default)
            elif (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "params"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
            ):
                params.add(node.slice.value)
                defaults[node.slice.value].add("required-or-validated-before-access")
    action_analysis: dict[str, ActionAnalysis] = {}
    for action in actions:
        action_keys, action_defs = _action_parameters(
            functions, handler_name, action, named_collections
        )
        action_analysis[action] = ActionAnalysis(
            params=sorted(action_keys),
            defaults={k: sorted(v) for k, v in sorted(action_defs.items())},
        )
    return (
        sorted(params),
        {key: sorted(value) for key, value in sorted(defaults.items())},
        actions,
        action_analysis,
    )


def _test_references(command: str) -> list[str]:
    needle = f'"{command}"'
    refs: list[str] = []
    for path in sorted((ROOT / "app/node/tests").glob("test_*.py")):
        if path.name == "test_command_coverage_ledger.py":
            continue
        if needle in path.read_text(encoding="utf-8"):
            refs.append(path.relative_to(ROOT).as_posix())
    for path in sorted(ASSIST_TOOLS.glob("*.test.ts")):
        if needle in path.read_text(encoding="utf-8"):
            refs.append(path.relative_to(ROOT).as_posix())
    return refs


def _load_manual() -> dict[str, Any]:
    value = json.loads(MANUAL.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("commands"), dict):
        raise LedgerError("manual coverage file must contain a commands object")
    return value


def _validate_exact(label: str, actual: set[str], declared: set[str]) -> None:
    missing = sorted(actual - declared)
    stale = sorted(declared - actual)
    if missing or stale:
        parts = [f"{label} coverage mismatch"]
        if missing:
            parts.append(f"missing={missing}")
        if stale:
            parts.append(f"stale={stale}")
        raise LedgerError("; ".join(parts))


def _evidence(
    method: str,
    outcome: str,
    source: str,
    observation: str,
    *,
    observed_at: str | None = None,
    node_version: str | None = None,
    stale: bool | None = None,
) -> dict[str, Any]:
    if method not in EVIDENCE_METHODS:
        raise LedgerError(f"invalid evidence method: {method}")
    if outcome not in OUTCOMES:
        raise LedgerError(f"invalid evidence outcome: {outcome}")
    # Live evidence must say when it was observed and against which build, and
    # the invariant is enforced here rather than only where manual observations
    # are ingested. The design-derived `system.run*` rows reached the ledger by
    # skipping that ingestion path entirely, so a check that lives only there
    # constrains one caller instead of the record type. Every live record is
    # built through this constructor.
    if method == "PRODUCTION-LIVE":
        if not _is_observed_at(observed_at):
            raise LedgerError(
                "PRODUCTION-LIVE evidence observation needs a real calendar "
                f"observed_at date (got {observed_at!r})"
            )
        if not isinstance(node_version, str) or not _FIRST_SHIPPED_VERSION_RE.fullmatch(
            node_version
        ):
            raise LedgerError(
                "PRODUCTION-LIVE evidence observation needs a valid "
                f"node_version (got {node_version!r})"
            )
    item: dict[str, Any] = {
        "method": method,
        "outcome": outcome,
        "source": source,
        "observation": observation,
    }
    if observed_at is not None:
        item["observed_at"] = observed_at
    if node_version is not None:
        item["node_version"] = node_version
    if stale is not None:
        item["stale"] = stale
    return item


def _caller(
    status: str,
    source: str,
    reason: str,
    *,
    method: str,
    outcome: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "source": source,
        "reason": reason,
        "evidence": [_evidence(method, outcome, source, reason)],
    }


def _resolve_manual_presence(
    key: str,
    variant: dict[str, Any],
    entry: dict[str, Any],
    defaults: dict[str, Any],
) -> tuple[bool, Any]:
    """Resolve *key* through manual precedence, reporting whether it was declared."""
    for scope in (variant, entry, defaults):
        if key in scope:
            return True, scope[key]
    return False, None


def _resolved_manual(
    key: str,
    variant: dict[str, Any],
    entry: dict[str, Any],
    defaults: dict[str, Any],
) -> Any:
    """Resolve action, command, then global manual metadata precedence."""
    _, value = _resolve_manual_presence(key, variant, entry, defaults)
    return value


_DEFAULT_EVIDENCE_NOTE = "Manual reality pass; behavior is not contract-enforced."


def _evidence_note(
    row_id: str,
    variant: dict[str, Any],
    entry: dict[str, Any],
    defaults: dict[str, Any],
) -> str:
    """Return the authored evidence note, defaulting only when none was written.

    `or <default>` replaced an authored empty string with the fallback, so a row
    could silently claim a manual reality pass its author never wrote. An empty
    note is not a way to say "use the default" — omitting the key is.
    """
    declared, note = _resolve_manual_presence("evidence_note", variant, entry, defaults)
    if not declared:
        return _DEFAULT_EVIDENCE_NOTE
    if not isinstance(note, str) or not note.strip():
        raise LedgerError(
            f"evidence_note for {row_id} must be a non-empty string; omit the key "
            "entirely to accept the default"
        )
    return str(note)


def _resolve_evidence_presence(
    key: str,
    variant: dict[str, Any],
    entry: dict[str, Any],
    authorization_defaults: dict[str, Any],
    defaults: dict[str, Any],
) -> tuple[bool, Any]:
    """Resolve *key*, reporting whether any scope declared it.

    Presence and value are resolved together, in one place, because they must
    agree on precedence. An earlier fix walked these scopes a second time at the
    call site to tell an absent key from an explicit null; that duplicated the
    order here and would have drifted the moment either changed. Returning both
    facts removes the duplicate.
    """
    for scope in (variant, entry, authorization_defaults, defaults):
        if key in scope:
            return True, scope[key]
    return False, None


def _resolved_evidence_field(
    key: str,
    variant: dict[str, Any],
    entry: dict[str, Any],
    authorization_defaults: dict[str, Any],
    defaults: dict[str, Any],
) -> Any:
    """Resolve explicit row metadata before authorization-class and global defaults."""
    _, value = _resolve_evidence_presence(key, variant, entry, authorization_defaults, defaults)
    return value


def _validate_acceptance_test_id(test_id: str) -> None:
    """Require each curated acceptance ID to name an extant behavioral test."""
    path_text, separator, test_name = test_id.partition("::")
    if not separator or not path_text or not test_name:
        raise LedgerError(f"acceptance test ID must be path::test-name: {test_id!r}")
    path = ROOT / path_text
    if not path.is_file() or not path.resolve().is_relative_to(ROOT.resolve()):
        raise LedgerError(f"acceptance test ID names missing repository file: {test_id}")
    source = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        expected = f"def {test_name}("
    elif path.suffix in {".ts", ".tsx"}:
        expected = json.dumps(test_name)
    else:
        raise LedgerError(f"acceptance test ID must name a Python or TypeScript test: {test_id}")
    if expected not in source:
        raise LedgerError(f"acceptance test ID does not match a declared test: {test_id}")


def build_ledger() -> dict[str, Any]:
    """Build the reconciled source/manual ledger dictionary."""
    registry = _registry()
    advertised_list = _advertised()
    if len(advertised_list) != len(set(advertised_list)):
        raise LedgerError("gateway _NODE_COMMANDS contains duplicate entries")
    advertised = set(advertised_list)
    assist = _assist_callers()
    manual = _load_manual()
    manual_defaults = manual.get("defaults", {})
    if not isinstance(manual_defaults, dict):
        raise LedgerError("manual coverage defaults must be an object")
    authorization_evidence = manual.get("authorization_evidence", {})
    if not isinstance(authorization_evidence, dict):
        raise LedgerError("manual authorization_evidence must be an object")
    declared = set(manual["commands"])
    _validate_exact("registered command", set(registry), declared)

    unknown_advertised = advertised - set(registry)
    if unknown_advertised:
        raise LedgerError(
            f"advertised commands have no dispatcher handler: {sorted(unknown_advertised)}"
        )
    unknown_assist = set(assist) - set(registry)
    if unknown_assist:
        raise LedgerError(f"Assist wrappers target unregistered commands: {sorted(unknown_assist)}")

    # Enforce parity between openclaw.plugin.json contracts.tools and the
    # executable Assist contract registrations.
    manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    manifest_tools = manifest.get("contracts", {}).get("tools", [])
    if not isinstance(manifest_tools, list):
        raise LedgerError("openclaw.plugin.json contracts.tools must be a list")
    assist_tool_names = {reg["tool_name"] for reg in assist.values()}
    manifest_set = set(manifest_tools)
    manifest_only = sorted(manifest_set - assist_tool_names)
    contract_only = sorted(assist_tool_names - manifest_set)
    if manifest_only or contract_only:
        parts = ["manifest/contract tool parity mismatch"]
        if manifest_only:
            parts.append(f"manifest-only={manifest_only}")
        if contract_only:
            parts.append(f"contract-only={contract_only}")
        raise LedgerError("; ".join(parts))

    current_release = _tracked_release_version()
    rows: list[dict[str, Any]] = []
    for command in sorted(registry):
        module, handler = registry[command]
        source_params, defaults, actions, action_analysis = _module_analysis(module, handler)
        entry = manual["commands"][command]
        if not isinstance(entry, dict):
            raise LedgerError(f"manual command entry for {command} must be an object")
        declared_actions = entry.get("actions", {})
        if not isinstance(declared_actions, dict):
            raise LedgerError(f"manual actions for {command} must be an object")
        _validate_exact(f"action variants for {command}", set(actions), set(declared_actions))

        first_shipped_in = entry.get("first_shipped_in")
        _validate_first_shipped_in(command, first_shipped_in)

        aliases = entry.get("aliases", {})
        if not isinstance(aliases, dict):
            raise LedgerError(f"aliases for {command} must be an object")
        alias_names = {alias for values in aliases.values() for alias in values}
        if not alias_names.issubset(source_params):
            raise LedgerError(
                f"aliases for {command} are not accepted by its source handler: "
                f"{sorted(alias_names - set(source_params))}"
            )
        canonical_params = sorted(set(source_params) - alias_names)
        bounds = entry.get("bounds", {})
        if not isinstance(bounds, dict):
            raise LedgerError(f"bounds for {command} must be an object")
        unknown_bounds = set(bounds) - set(source_params)
        if unknown_bounds:
            raise LedgerError(f"bounds for {command} name unknown params: {sorted(unknown_bounds)}")

        assist_registration = assist.get(command)
        if assist_registration is not None:
            emitted_targets = {
                target
                for target in assist_registration["emitted_params"].values()
                if target is not None
            }
            emitted_targets.update(assist_registration["injected_node_params"].values())
            unknown_emitted = emitted_targets - set(source_params)
            known_unaccepted = set(assist_registration.get("known_unaccepted_node_params", {}))
            if unknown_emitted != known_unaccepted:
                raise LedgerError(
                    f"Assist {command} node-key mismatch coverage differs; "
                    f"unacknowledged={sorted(unknown_emitted - known_unaccepted)}; "
                    f"orphaned={sorted(known_unaccepted - unknown_emitted)}"
                )

        if command in advertised:
            advertised_path = _caller(
                "advertised",
                "app/node/src/openclaw_node/gateway_ws.py::_NODE_COMMANDS",
                "Present in the node connect frame; gateway allowlisting and runtime "
                "availability are separate.",
                method="CODE-PROVEN",
                outcome="pass",
            )
        else:
            reason = entry.get("unadvertised_reason")
            if not isinstance(reason, str) or not reason:
                raise LedgerError(f"unadvertised command {command} needs unadvertised_reason")
            advertised_path = _caller(
                "unavailable",
                "app/node/src/openclaw_node/gateway_ws.py::_NODE_COMMANDS",
                reason,
                method="CODE-PROVEN",
                outcome="fail",
            )

        if command in {"system.run", "system.run.prepare"}:
            # CODE-PROVEN, not PRODUCTION-LIVE. The source here is a design
            # document, and neither command appears in the September 13 sweep,
            # so there is no observation behind this row. Labelling it
            # PRODUCTION-LIVE also bypassed provenance validation, which is only
            # applied to manual observations, and emitted a live-evidence row
            # carrying no observed_at, node_version, or staleness.
            direct_path = _caller(
                "unavailable",
                "docs/design/AUTHORIZATION-MODEL.md#class-3-home-assistant-shell",
                (
                    "Direct nodes.invoke is refused by the Gateway by design; "
                    "reach this command through the OpenClaw exec tool with host=node, "
                    "which prepares the canonical systemRunPlan and forwards it after "
                    "operator approval."
                ),
                method="CODE-PROVEN",
                outcome="refused-as-designed",
            )
        elif command not in advertised:
            direct_path = _caller(
                "unavailable",
                "node advertisement reconciliation",
                "The command is registered but not advertised, so direct node invocation "
                "cannot reach it.",
                method="CODE-PROVEN",
                outcome="fail",
            )
        else:
            direct_path = _caller(
                "advertised-unverified",
                "dispatcher + node connect frame",
                "A dispatcher and advertised path exist; end-to-end availability is not implied.",
                method="CODE-PROVEN",
                outcome="unverified",
            )

        if assist_registration is not None:
            assist_path: dict[str, Any] = _caller(
                "wrapper-exposed",
                "plugins/openclaw-hass-node-assist-tools/src/tools/assist-command-contract.json",
                "The executable Assist registration contract maps this tool to the node command.",
                method="CODE-PROVEN",
                outcome="pass",
            )
            assist_path.update(
                {
                    "tool_name": assist_registration["tool_name"],
                    "descriptor": assist_registration["descriptor"],
                    "factory": assist_registration["factory"],
                    "accepted_tool_params": assist_registration["accepted_tool_params"],
                    "emitted_params": assist_registration["emitted_params"],
                    "injected_node_params": assist_registration["injected_node_params"],
                    "known_unaccepted_node_params": assist_registration.get(
                        "known_unaccepted_node_params", {}
                    ),
                    "value_transforms": assist_registration.get("value_transforms", {}),
                    "client_side_params": assist_registration.get("client_side_params", {}),
                }
            )
            for key, mismatch in assist_path["known_unaccepted_node_params"].items():
                assist_path["evidence"].append(
                    _evidence(
                        "CODE-PROVEN",
                        "fail",
                        "plugins/openclaw-hass-node-assist-tools/src/tools/"
                        "assist-command-contract.json",
                        f"Emitted node parameter {key!r} is not accepted "
                        f"({mismatch['issue']}): {mismatch['reason']}",
                    )
                )
        else:
            reason = entry.get("assist_unavailable_reason")
            if not isinstance(reason, str) or not reason:
                raise LedgerError(f"unwrapped command {command} needs assist_unavailable_reason")
            assist_path = _caller(
                "unavailable",
                "contracts/command-coverage-manual.json",
                reason,
                method="CODE-PROVEN",
                outcome="refused-as-designed",
            )

        variants: list[tuple[str | None, dict[str, Any]]] = [(None, entry)]
        if actions:
            variants = [
                (
                    None,
                    {
                        "authorization_class": "action_dependent",
                        "capability_conditions": (
                            "Registered command entry point; parameters, policy, and runtime "
                            "capability vary by the selected action."
                        ),
                        "evidence_method": "CODE-PROVEN",
                        "evidence_note": (
                            "Base command inventory row; use the action rows for behavior claims."
                        ),
                    },
                ),
                *((action, declared_actions[action]) for action in actions),
            ]
        for action, variant in variants:
            authorization_class = _resolved_manual(
                "authorization_class", variant, entry, manual_defaults
            )
            authorization_defaults = authorization_evidence.get(authorization_class, {})
            if not isinstance(authorization_defaults, dict):
                raise LedgerError(
                    f"authorization evidence for {authorization_class!r} must be an object"
                )
            for required in (
                "authorization_class",
                "capability_conditions",
                "semantic_result",
                "semantic_errors",
            ):
                if _resolved_manual(required, variant, entry, manual_defaults) is None:
                    raise LedgerError(
                        f"{command}/{action or '-'} is missing manual field {required}"
                    )
            evidence = _resolved_evidence_field(
                "evidence_method",
                variant,
                entry,
                authorization_defaults,
                manual_defaults,
            )
            if evidence not in EVIDENCE_METHODS:
                raise LedgerError(
                    f"invalid evidence method for {command}/{action or '-'}: {evidence!r}"
                )
            outcome = _resolved_evidence_field(
                "outcome", variant, entry, authorization_defaults, manual_defaults
            )
            if outcome not in OUTCOMES:
                raise LedgerError(f"invalid outcome for {command}/{action or '-'}: {outcome!r}")
            acceptance_tests = _resolved_evidence_field(
                "acceptance_test_ids",
                variant,
                entry,
                authorization_defaults,
                manual_defaults,
            )
            if not isinstance(acceptance_tests, list) or not all(
                isinstance(test, dict)
                and isinstance(test.get("id"), str)
                and test["id"]
                and test.get("caller") in VALID_TEST_CALLERS
                and test.get("outcome") in OUTCOMES
                for test in acceptance_tests
            ):
                raise LedgerError(
                    f"acceptance_test_ids for {command}/{action or '-'} must be curated objects "
                    f"with caller in {sorted(VALID_TEST_CALLERS)}"
                )
            for acceptance_test in acceptance_tests:
                _validate_acceptance_test_id(acceptance_test["id"])
                # Validate that mapped callers resolve to a real row caller.
                if acceptance_test["caller"] not in ROW_CALLERS:
                    raise LedgerError(
                        f"acceptance test caller {acceptance_test['caller']!r} for "
                        f"{command}/{action or '-'} is not a recognized row caller"
                    )
            if evidence == "TEST-PROVEN" and not acceptance_tests:
                raise LedgerError(
                    f"TEST-PROVEN row {command}/{action or '-'} needs curated behavioral test IDs"
                )
            selected_params = variant.get("parameters", canonical_params)
            if not isinstance(selected_params, list) or not all(
                isinstance(name, str) for name in selected_params
            ):
                raise LedgerError(f"parameters for {command}/{action or '-'} must be a string list")
            unknown_selected = {
                name
                for name in selected_params
                if name not in canonical_params and not (name.startswith("<") and ">" in name)
            }
            if unknown_selected:
                raise LedgerError(
                    f"action params for {command}/{action or '-'} are not accepted by source: "
                    f"{sorted(unknown_selected)}"
                )
            if action is not None:
                derived_action_params = set(action_analysis[action].params) - alias_names
                selected_action_params = set(selected_params)
                if selected_action_params != derived_action_params:
                    missing = sorted(derived_action_params - selected_action_params)
                    orphaned = sorted(selected_action_params - derived_action_params)
                    raise LedgerError(
                        f"action parameter coverage mismatch for {command}/{action}; "
                        f"missing={missing}; orphaned={orphaned}"
                    )
            # Resolve defaults: action-specific when available, else module-wide.
            active_defaults = action_analysis[action].defaults if action is not None else defaults
            parameters = [
                {
                    "name": name,
                    "aliases": aliases.get(name, []),
                    "defaults": active_defaults.get(
                        name,
                        defaults.get(name, ["generator-fallback: no source default observed"]),
                    ),
                    "bounds": bounds.get(name, "unverified; no normalized contract yet"),
                    "provenance": {
                        "name": (
                            "source-derived AST accepted key"
                            if name in canonical_params
                            else "source-derived dynamic key pattern"
                        ),
                        "aliases": "manual declaration validated against source accepted keys",
                        "defaults": (
                            "action-specific source-derived AST expression"
                            if action is not None and name in active_defaults
                            else (
                                "source-derived AST expression"
                                if name in defaults
                                else (
                                    "generator-fallback: param accepted but no default found in AST"
                                )
                            )
                        ),
                        "bounds": (
                            "manual normalized note"
                            if name in bounds
                            else "manual UNVERIFIED placeholder"
                        ),
                    },
                }
                for name in selected_params
            ]
            row_id = command if action is None else f"{command}#{action}"
            # handler_dispatch: evidence from handler/dispatch_async tests only,
            # distinct from end-to-end Gateway nodes.invoke (direct_nodes_invoke).
            # Status is path-present-unverified: a registered handler exists but
            # behavioral evidence comes only from curated caller_observations.
            handler_dispatch_path = _caller(
                "path-present-unverified",
                "handler and dispatch_async test matrix",
                "A registered handler exists. Behavioral evidence comes from "
                "curated handler/dispatch_async tests, not live Gateway nodes.invoke.",
                method="UNVERIFIED",
                outcome="unverified",
            )
            row_callers = copy.deepcopy(
                {
                    "node_advertisement": advertised_path,
                    "direct_nodes_invoke": direct_path,
                    "assist_wrapper": assist_path,
                    "handler_dispatch": handler_dispatch_path,
                }
            )
            # Same absent-versus-null distinction as `issues`. `or {}` accepted an
            # explicit null as "no observations", so a higher-precedence null
            # could suppress inherited observations instead of failing.
            observations_declared, caller_observations = _resolve_evidence_presence(
                "caller_observations", variant, entry, authorization_defaults, manual_defaults
            )
            if not observations_declared:
                caller_observations = {}
            elif caller_observations is None:
                raise LedgerError(
                    f"caller_observations for {row_id} is explicitly null; omit the "
                    "key entirely to mean 'no observations'"
                )
            if not isinstance(caller_observations, dict):
                raise LedgerError(f"caller_observations for {row_id} must be an object")
            for caller_name, observations in caller_observations.items():
                if caller_name not in row_callers or not isinstance(observations, list):
                    raise LedgerError(f"invalid caller observations for {row_id}/{caller_name}")
                for observation in observations:
                    if not isinstance(observation, dict):
                        raise LedgerError(f"caller observation for {row_id} must be an object")
                    for required_field in ("method", "outcome", "source", "observation"):
                        if not isinstance(observation.get(required_field), str):
                            raise LedgerError(
                                f"caller observation for {row_id}/{caller_name} "
                                f"missing required string field: {required_field}"
                            )
                    obs_observed_at: str | None = None
                    obs_node_version: str | None = None
                    obs_stale: bool | None = None
                    if observation["method"] == "PRODUCTION-LIVE":
                        obs_observed_at = observation.get("observed_at")
                        obs_node_version = observation.get("node_version")
                        if not _is_observed_at(obs_observed_at):
                            raise LedgerError(
                                f"PRODUCTION-LIVE observation for {row_id}/{caller_name} "
                                f"missing required ISO date field: observed_at "
                                f"(got {obs_observed_at!r})"
                            )
                        ver_re = _FIRST_SHIPPED_VERSION_RE
                        if not isinstance(obs_node_version, str) or not ver_re.fullmatch(
                            obs_node_version
                        ):
                            raise LedgerError(
                                f"PRODUCTION-LIVE observation for {row_id}/{caller_name} "
                                f"missing required version field: node_version "
                                f"(got {obs_node_version!r})"
                            )
                        obs_stale = obs_node_version != current_release
                    row_callers[caller_name]["evidence"].append(
                        _evidence(
                            observation["method"],
                            observation["outcome"],
                            observation["source"],
                            observation["observation"],
                            observed_at=obs_observed_at,
                            node_version=obs_node_version,
                            stale=obs_stale,
                        )
                    )
            # Only a genuinely absent key defaults. `or []` used to run before the
            # type check, so an explicit "", 0, false or {} was silently accepted
            # as "no citations" instead of being rejected as malformed, and an
            # explicit null was indistinguishable from absence.
            issues_declared, issues = _resolve_evidence_presence(
                "issues", variant, entry, authorization_defaults, manual_defaults
            )
            if not issues_declared:
                issues = []
            elif issues is None:
                raise LedgerError(
                    f"issues for {row_id} is explicitly null; omit the key "
                    "entirely to mean 'no citations'"
                )
            if not isinstance(issues, list):
                raise LedgerError(f"issues for {row_id} must be a list (got {issues!r})")
            for citation in issues:
                # bool is an int subclass, so it must be excluded explicitly or
                # `true` would be accepted and render as issue #1.
                if type(citation) is not int or citation < 1:
                    raise LedgerError(
                        f"issues for {row_id} must each be a positive issue number "
                        f"(got {citation!r})"
                    )
            if len(set(issues)) != len(issues):
                raise LedgerError(f"issues for {row_id} contains a duplicate citation: {issues}")
            rows.append(
                {
                    "id": row_id,
                    "command": command,
                    "action": action,
                    "first_shipped_in": first_shipped_in,
                    "handler": f"openclaw_node.commands.{module}:{handler}",
                    "registered": True,
                    "callers": row_callers,
                    "canonical_parameters": parameters,
                    "authorization_class": authorization_class,
                    "issues": issues,
                    "capability_conditions": _resolved_manual(
                        "capability_conditions", variant, entry, manual_defaults
                    ),
                    "semantic_result": _resolved_manual(
                        "semantic_result", variant, entry, manual_defaults
                    ),
                    "semantic_errors": _resolved_manual(
                        "semantic_errors", variant, entry, manual_defaults
                    ),
                    "acceptance_test_ids": acceptance_tests,
                    "source_mentions": _test_references(command),
                    "evidence_method": evidence,
                    "outcome": outcome,
                    "evidence_note": _evidence_note(row_id, variant, entry, manual_defaults),
                    "metadata_provenance": {
                        "inventory": "source-derived",
                        "authorization_class": "manual reality pass",
                        "capability_conditions": "manual reality pass",
                        "semantic_result": "manual reality pass",
                        "semantic_errors": "manual reality pass",
                        "evidence_method": "manual evidence classification",
                        "outcome": "manual outcome classification",
                        "acceptance_test_ids": "manually curated behavioral IDs",
                        "source_mentions": "source-derived textual references; not proof",
                    },
                }
            )

    first_shipped_by_command = {
        cmd: manual["commands"][cmd]["first_shipped_in"] for cmd in sorted(registry)
    }
    latest_release = current_release
    commands_new_in_latest_release = _commands_new_in_release(
        first_shipped_by_command, latest_release
    )
    commands_unreleased = sorted(
        cmd for cmd, v in first_shipped_by_command.items() if v == "unreleased"
    )

    return {
        "schema_version": 1,
        "title": "OpenClaw Home Assistant node command/action/caller coverage ledger",
        "generated": True,
        "generation_note": (
            "Deterministic source inventory plus explicitly labelled manual reality metadata; "
            "no runtime command is enabled by this ledger."
        ),
        "release_version_format": (
            "PEP 440 three-part release: alpha (aN), beta (bN), release candidate (rcN), "
            "development (.devN), or final"
        ),
        "evidence_methods": {
            "UNVERIFIED": "Present in the ledger but not behaviorally proven.",
            "CODE-PROVEN": "Established from source review; not a live result.",
            "TEST-PROVEN": "Covered by an automated test; not a live result.",
            "DISPOSABLE-LIVE": "Probed against a disposable non-production environment.",
            "PRODUCTION-LIVE": (
                "Observed on the installed production node, including observed failures."
            ),
        },
        "outcomes": {
            "pass": "Observed behavior matched the scoped claim.",
            "fail": "Observed behavior contradicted the scoped claim.",
            "refused-as-designed": "The operation was intentionally unavailable and refused.",
            "partial": "Some caller or contract behavior passed while material gaps remain.",
            "unverified": "No behavioral outcome is claimed.",
        },
        "summary": {
            "registered_commands": len(registry),
            "advertised_commands": len(advertised),
            "assist_wrapped_commands": len(assist),
            "action_variants": sum(1 for row in rows if row["action"] is not None),
            "ledger_rows": len(rows),
            "registered_not_advertised": sorted(set(registry) - advertised),
            "advertised_not_registered": sorted(advertised - set(registry)),
            "registered_without_assist_wrapper": sorted(set(registry) - set(assist)),
        },
        "latest_release": latest_release,
        "commands_new_in_latest_release": commands_new_in_latest_release,
        "commands_unreleased": commands_unreleased,
        "rows": rows,
    }


def _compact_params(row: dict[str, Any]) -> str:
    values: list[str] = []
    for item in row["canonical_parameters"]:
        text = item["name"]
        if item["aliases"]:
            text += " (alias: " + ", ".join(item["aliases"]) + ")"
        values.append(text)
    return ", ".join(values) if values else "none observed"


def _compact_caller(caller: dict[str, Any]) -> str:
    outcomes = list(
        dict.fromkeys(f"{item['method']}:{item['outcome']}" for item in caller["evidence"])
    )
    return f"{caller['status']}<br>{'<br>'.join(outcomes)}"


def render_markdown(ledger: dict[str, Any]) -> str:
    """Render the human-readable ledger from the machine representation."""
    summary = ledger["summary"]
    lines = [
        "# Command Coverage Ledger",
        "",
        "<!-- Generated by scripts/generate-command-coverage.py. Do not edit by hand. -->",
        "",
        "This is a generated **coverage inventory**, not a claim that every row works.",
        "Source-derived registry, advertisement, caller, action, and accepted-key facts are",
        "combined with explicitly manual policy/semantic notes. `UNVERIFIED` and failure",
        "rows are intentionally retained. Regenerate after editing source or",
        "`contracts/command-coverage-manual.json`.",
        "Shipment versions use the same canonical forms as `scripts/bump-version.py`:",
        "`aN`, `bN`, `rcN`, `.devN`, or a final three-part release.",
        "",
        "## Summary",
        "",
        f"- Registered commands: **{summary['registered_commands']}**",
        f"- Advertised commands: **{summary['advertised_commands']}**",
        f"- Assist-wrapped commands: **{summary['assist_wrapped_commands']}**",
        f"- Action variants: **{summary['action_variants']}**",
        f"- Ledger rows: **{summary['ledger_rows']}**",
        "- Registered but unadvertised: "
        f"`{', '.join(summary['registered_not_advertised']) or 'none'}`",
        "- Advertised but unregistered: "
        f"`{', '.join(summary['advertised_not_registered']) or 'none'}`",
        "",
    ]
    # New in this release
    latest_release = ledger.get("latest_release")
    new_commands = ledger.get("commands_new_in_latest_release", [])
    if latest_release:
        lines.extend(
            [
                f"## New in this release ({latest_release})",
                "",
                "Commands whose `first_shipped_in` matches the synchronized version in "
                "the five tracked project sources. These are new since the previous release.",
                "",
            ]
        )
        if new_commands:
            for cmd in new_commands:
                lines.append(f"- `{cmd}`")
        else:
            lines.append("_(no commands are new in this release)_")
        lines.append("")
    else:
        lines.extend(
            [
                "## New in this release",
                "",
                "_(no released version is recorded in the ledger yet; "
                "every command is still unreleased)_",
                "",
            ]
        )
    # Unreleased command additions
    unreleased_commands = ledger.get("commands_unreleased", [])
    lines.extend(
        [
            "## Unreleased command additions",
            "",
            "Commands with `first_shipped_in: unreleased`. "
            "These will ship if a release is cut now.",
            "",
        ]
    )
    if unreleased_commands:
        for cmd in unreleased_commands:
            lines.append(f"- `{cmd}`")
    else:
        lines.append("_(no unreleased command additions)_")
    lines.extend(
        [
            "",
            "## Evidence methods",
            "",
        ]
    )
    for level, meaning in ledger["evidence_methods"].items():
        lines.append(f"- **{level}:** {meaning}")
    lines.extend(["", "## Outcomes", ""])
    for outcome, meaning in ledger["outcomes"].items():
        lines.append(f"- **{outcome}:** {meaning}")
    _assert_unique_anchors(ledger["rows"])
    lines.extend(
        [
            "",
            "## Coverage rows",
            "",
            "| Command / action | Advertised | Direct caller | Handler/dispatch | Assist wrapper | "
            "Authorization | Method | **Outcome** |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in ledger["rows"]:
        callers = row["callers"]
        outcome_cell = f"**`{row['outcome']}`**"
        if row.get("issues"):
            outcome_cell += " (" + ", ".join(_citation_link(c) for c in row["issues"]) + ")"
        lines.append(
            f"| [`{row['id']}`](#{_row_anchor(row['id'])}) | "
            f"{_compact_caller(callers['node_advertisement'])} | "
            f"{_compact_caller(callers['direct_nodes_invoke'])} | "
            f"{_compact_caller(callers['handler_dispatch'])} | "
            f"{_compact_caller(callers['assist_wrapper'])} | "
            f"`{row['authorization_class']}` | `{row['evidence_method']}` | "
            f"{outcome_cell} |"
        )
    lines.extend(["", "## Row details", ""])
    for row in ledger["rows"]:
        lines.extend(
            [
                f"### `{row['id']}` {{#{_row_anchor(row['id'])}}}",
                "",
                f"- Handler: `{row['handler']}`",
                f"- Canonical parameters: {_compact_params(row)}",
                f"- Authorization: `{row['authorization_class']}`",
                f"- Capability conditions: {row['capability_conditions']}",
                f"- Semantic result: {row['semantic_result']}",
                f"- Semantic errors: {row['semantic_errors']}",
                f"- Evidence method: `{row['evidence_method']}`",
                f"- **Outcome: `{row['outcome']}`**",
                *(
                    [f"- Issues: {', '.join(_citation_link(c) for c in row['issues'])}"]
                    if row.get("issues")
                    else []
                ),
                f"- Evidence note: {row['evidence_note']}",
                f"- Advertisement: {row['callers']['node_advertisement']['reason']}",
                f"- Direct caller: {row['callers']['direct_nodes_invoke']['reason']}",
                f"- Handler/dispatch: {row['callers']['handler_dispatch']['reason']}",
                f"- Assist caller: {row['callers']['assist_wrapper']['reason']}",
            ]
        )
        assist_caller = row["callers"]["assist_wrapper"]
        if assist_caller["status"] == "wrapper-exposed":
            lines.extend(
                [
                    f"  - Assist tool: `{assist_caller['tool_name']}`",
                    f"  - Descriptor/factory: `{assist_caller['descriptor']}` / "
                    f"`{assist_caller['factory']}`",
                    "  - Tool parameters: "
                    f"`{json.dumps(assist_caller['accepted_tool_params'], sort_keys=True)}`",
                    "  - Emitted node mapping: "
                    f"`{json.dumps(assist_caller['emitted_params'], sort_keys=True)}`",
                    "  - Injected node mapping: "
                    f"`{json.dumps(assist_caller['injected_node_params'], sort_keys=True)}`",
                    "  - Known unaccepted node parameters: `"
                    + json.dumps(assist_caller["known_unaccepted_node_params"], sort_keys=True)
                    + "`",
                    "  - Value transforms: `"
                    + json.dumps(assist_caller["value_transforms"], sort_keys=True)
                    + "`",
                    "  - Client-side parameters: `"
                    + json.dumps(assist_caller["client_side_params"], sort_keys=True)
                    + "`",
                ]
            )
        lines.append("- Parameter details:")
        if row["canonical_parameters"]:
            for parameter in row["canonical_parameters"]:
                lines.extend(
                    [
                        f"  - `{parameter['name']}`",
                        f"    - aliases: `{json.dumps(parameter['aliases'])}`",
                        f"    - defaults: `{json.dumps(parameter['defaults'])}`",
                        f"    - bounds: {parameter['bounds']}",
                        "    - provenance: "
                        f"`{json.dumps(parameter['provenance'], sort_keys=True)}`",
                    ]
                )
        else:
            lines.append("  - none observed")
        lines.append("- Caller evidence:")
        for caller_name, caller in row["callers"].items():
            for observation in caller["evidence"]:
                provenance = ""
                if observation.get("node_version") or observation.get("observed_at"):
                    prov_parts = []
                    if observation.get("node_version"):
                        prov_parts.append(f"node_version={observation['node_version']}")
                    if observation.get("observed_at"):
                        prov_parts.append(f"observed_at={observation['observed_at']}")
                    if observation.get("stale"):
                        prov_parts.append("**STALE**")
                    provenance = " [" + "; ".join(prov_parts) + "]"
                lines.append(
                    f"  - `{caller_name}` / `{observation['method']}` / "
                    f"**`{observation['outcome']}`**: {observation['observation']} "
                    f"(source: `{observation['source']}`){provenance}"
                )
        lines.append("- Curated acceptance-test IDs:")
        if row["acceptance_test_ids"]:
            for test in row["acceptance_test_ids"]:
                lines.append(f"  - `{test['id']}` / `{test['caller']}` / `{test['outcome']}`")
        else:
            lines.append("  - none; do not treat source mentions as behavioral proof")
        lines.append("- Source mentions (not acceptance evidence):")
        if row["source_mentions"]:
            lines.extend(f"  - `{source}`" for source in row["source_mentions"])
        else:
            lines.append("  - none")
        lines.append("")
    document = "\n".join(lines).rstrip() + "\n"
    _assert_anchors_round_trip(document, ledger["rows"])
    return document


def _serialized_outputs() -> dict[Path, str]:
    ledger = build_ledger()
    return {
        JSON_OUTPUT: json.dumps(ledger, indent=2, sort_keys=True) + "\n",
        MARKDOWN_OUTPUT: render_markdown(ledger),
    }


def _check(outputs: dict[Path, str]) -> int:
    stale = False
    for path, expected in outputs.items():
        actual = path.read_text(encoding="utf-8") if path.exists() else ""
        if actual == expected:
            continue
        stale = True
        print(f"stale generated artifact: {path.relative_to(ROOT)}", file=sys.stderr)
        diff = difflib.unified_diff(
            actual.splitlines(),
            expected.splitlines(),
            fromfile=str(path.relative_to(ROOT)),
            tofile=f"generated:{path.relative_to(ROOT)}",
            lineterm="",
        )
        for line in list(diff)[:80]:
            print(line, file=sys.stderr)
    return 1 if stale else 0


def main() -> int:
    """Run generation or stale-artifact check mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail if generated artifacts are stale"
    )
    args = parser.parse_args()
    try:
        outputs = _serialized_outputs()
    except (LedgerError, json.JSONDecodeError, OSError) as exc:
        print(f"command coverage generation failed: {exc}", file=sys.stderr)
        return 2
    if args.check:
        return _check(outputs)
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
