"""Tests for openclaw_node.commands.dispatcher."""

from __future__ import annotations

from typing import Any

import pytest
from openclaw_node.commands.dispatcher import (
    AsyncHandlerError,
    UnknownCommandError,
    dispatch,
    dispatch_async,
    register_handler,
)


def test_dispatch_unknown_command() -> None:
    with pytest.raises(UnknownCommandError):
        dispatch("does.not.exist", {})


def test_dispatch_sync_handler_returns_dict() -> None:
    def _handler(params: dict[str, Any]) -> dict[str, Any]:
        return {"echo": params}

    register_handler("test.sync.echo", _handler)
    result = dispatch("test.sync.echo", {"x": 1})
    assert result == {"echo": {"x": 1}}


def test_dispatch_rejects_async_handler_with_helpful_error() -> None:
    async def _handler(params: dict[str, Any]) -> dict[str, Any]:
        return {"async": True}

    register_handler("test.async.bad", _handler)
    with pytest.raises(AsyncHandlerError):
        dispatch("test.async.bad", {})


async def test_dispatch_async_unknown_command() -> None:
    with pytest.raises(UnknownCommandError):
        await dispatch_async("does.not.exist", {})


async def test_dispatch_async_awaits_async_handler() -> None:
    async def _handler(params: dict[str, Any]) -> dict[str, Any]:
        return {"async": True, "got": params}

    register_handler("test.async.echo", _handler)
    result = await dispatch_async("test.async.echo", {"x": 1})
    assert result == {"async": True, "got": {"x": 1}}


async def test_dispatch_async_passes_through_sync_handler() -> None:
    def _handler(params: dict[str, Any]) -> dict[str, Any]:
        return {"sync": True}

    register_handler("test.async.from_sync", _handler)
    result = await dispatch_async("test.async.from_sync", {})
    assert result == {"sync": True}
