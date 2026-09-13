"""Tests for openclaw_node.commands.dispatcher."""

from __future__ import annotations

from typing import Any

import pytest

from openclaw_node.commands.dispatcher import (
    _REGISTRY,
    AsyncHandlerError,
    UnknownCommandError,
    dispatch,
    dispatch_async,
)


def test_dispatch_unknown_command() -> None:
    with pytest.raises(UnknownCommandError):
        dispatch("does.not.exist", {})


def test_dispatch_sync_handler_returns_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    def _handler(params: dict[str, Any]) -> dict[str, Any]:
        return {"echo": params}

    monkeypatch.setitem(_REGISTRY, "test.sync.echo", _handler)
    result = dispatch("test.sync.echo", {"x": 1})
    assert result == {"echo": {"x": 1}}


def test_dispatch_rejects_async_handler_with_helpful_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _handler(params: dict[str, Any]) -> dict[str, Any]:
        return {"async": True}

    monkeypatch.setitem(_REGISTRY, "test.async.bad", _handler)
    with pytest.raises(AsyncHandlerError):
        dispatch("test.async.bad", {})


async def test_dispatch_async_unknown_command() -> None:
    with pytest.raises(UnknownCommandError):
        await dispatch_async("does.not.exist", {})


async def test_dispatch_async_awaits_async_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _handler(params: dict[str, Any]) -> dict[str, Any]:
        return {"async": True, "got": params}

    monkeypatch.setitem(_REGISTRY, "test.async.echo", _handler)
    result = await dispatch_async("test.async.echo", {"x": 1})
    assert result == {"async": True, "got": {"x": 1}}


async def test_dispatch_async_passes_through_sync_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _handler(params: dict[str, Any]) -> dict[str, Any]:
        return {"sync": True}

    monkeypatch.setitem(_REGISTRY, "test.async.from_sync", _handler)
    result = await dispatch_async("test.async.from_sync", {})
    assert result == {"sync": True}
