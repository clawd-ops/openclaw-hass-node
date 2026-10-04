"""Tests for openclaw_node.commands.dispatcher."""

from __future__ import annotations

from typing import Any

import pytest

from openclaw_node.authz import USER_ALLOWED_COMMANDS, USER_FORBIDDEN_COMMANDS, forbidden_for_role
from openclaw_node.caller import UNTRUSTED, Caller
from openclaw_node.commands.dispatcher import (
    _REGISTRY,
    AsyncHandlerError,
    UnknownCommandError,
    dispatch,
    dispatch_async,
)
from openclaw_node.config import IdentityConfig

_OP = Caller.operator("t")


def test_dispatch_unknown_command() -> None:
    with pytest.raises(UnknownCommandError):
        dispatch("does.not.exist", {})


def test_dispatch_sync_handler_returns_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    def _handler(params: dict[str, Any]) -> dict[str, Any]:
        return {"echo": params}

    monkeypatch.setitem(_REGISTRY, "test.sync.echo", _handler)
    result = dispatch("test.sync.echo", {"x": 1}, caller=_OP)
    assert result == {"echo": {"x": 1}}


def test_dispatch_rejects_async_handler_with_helpful_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _handler(params: dict[str, Any]) -> dict[str, Any]:
        return {"async": True}

    monkeypatch.setitem(_REGISTRY, "test.async.bad", _handler)
    with pytest.raises(AsyncHandlerError):
        dispatch("test.async.bad", {}, caller=_OP)


async def test_dispatch_async_unknown_command() -> None:
    with pytest.raises(UnknownCommandError):
        await dispatch_async("does.not.exist", {})


async def test_dispatch_async_awaits_async_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _handler(params: dict[str, Any]) -> dict[str, Any]:
        return {"async": True, "got": params}

    monkeypatch.setitem(_REGISTRY, "test.async.echo", _handler)
    result = await dispatch_async("test.async.echo", {"x": 1}, caller=_OP)
    assert result == {"async": True, "got": {"x": 1}}


async def test_dispatch_async_passes_through_sync_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _handler(params: dict[str, Any]) -> dict[str, Any]:
        return {"sync": True}

    monkeypatch.setitem(_REGISTRY, "test.async.from_sync", _handler)
    result = await dispatch_async("test.async.from_sync", {}, caller=_OP)
    assert result == {"sync": True}


def _counting_handler(calls: list[dict[str, Any]]) -> Any:
    def _handler(params: dict[str, Any]) -> dict[str, Any]:
        calls.append(params)
        return {"ran": True}

    return _handler


async def test_dispatch_async_default_caller_is_untrusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setitem(_REGISTRY, "system.run", _counting_handler(calls))
    result = await dispatch_async("system.run", {})
    assert result["ok"] is False
    assert result["error"] == "PERMISSION_DENIED"
    assert calls == []


_USER_FORBIDDEN_COMMANDS = sorted(
    c for c in forbidden_for_role(IdentityConfig(), "user") if ":" not in c
)


@pytest.mark.parametrize("command", _USER_FORBIDDEN_COMMANDS)
async def test_dispatch_async_refuses_every_user_forbidden_command(
    command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setitem(_REGISTRY, command, _counting_handler(calls))
    result = await dispatch_async(command, {}, caller=UNTRUSTED)
    assert result["error"] == "PERMISSION_DENIED"
    assert calls == []


async def test_dispatch_async_operator_reaches_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setitem(_REGISTRY, "system.run", _counting_handler(calls))
    result = await dispatch_async("system.run", {"a": 1}, caller=Caller.operator("t"))
    assert result == {"ran": True}
    assert calls == [{"a": 1}]


def test_dispatch_sync_refuses_untrusted_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setitem(_REGISTRY, "system.run", _counting_handler(calls))
    assert dispatch("system.run", {})["error"] == "PERMISSION_DENIED"
    assert dispatch("system.run", {}, caller=Caller.operator("t")) == {"ran": True}
    assert calls == [{}]


def test_every_registered_command_is_classified_for_user_exactly_once() -> None:
    registry = set(_REGISTRY)
    assert not USER_ALLOWED_COMMANDS & USER_FORBIDDEN_COMMANDS
    assert registry == USER_ALLOWED_COMMANDS | USER_FORBIDDEN_COMMANDS


async def test_dispatch_async_refuses_unclassified_command_for_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setitem(_REGISTRY, "ha.brand_new_mutation", _counting_handler(calls))
    result = await dispatch_async("ha.brand_new_mutation", {}, caller=UNTRUSTED)
    assert result["error"] == "PERMISSION_DENIED"
    assert calls == []
    operator = await dispatch_async("ha.brand_new_mutation", {}, caller=Caller.operator("t"))
    assert operator == {"ran": True}


@pytest.mark.parametrize(
    ("command", "params"),
    [
        ("system.execApprovals.set", {"file": {"version": 1, "agents": {}}}),
        ("ha.addon_update", {"slug": "core_ssh"}),
    ],
)
async def test_dispatch_async_user_cannot_reach_review_probe_mutations(
    command: str, params: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setitem(_REGISTRY, command, _counting_handler(calls))
    for caller in (UNTRUSTED, Caller(role="user", actor_id="u", forbidden=())):
        result = await dispatch_async(command, params, caller=caller)
        assert result["error"] == "PERMISSION_DENIED"
    assert calls == []
