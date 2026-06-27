"""Tests for openclaw_node.commands.ping and dispatcher integration."""

from __future__ import annotations

import time

import pytest
from openclaw_node.commands.dispatcher import (
    UnknownCommandError,
    dispatch,
    register_handler,
)
from openclaw_node.commands.ping import handle_ping


def test_handle_ping_basic() -> None:
    result = handle_ping({})
    assert result["pong"] is True
    assert result["message"] == ""
    assert isinstance(result["ts"], int)


def test_handle_ping_with_message() -> None:
    result = handle_ping({"message": "hello"})
    assert result["pong"] is True
    assert result["message"] == "hello"


def test_handle_ping_ts_is_recent() -> None:
    before = int(time.time() * 1000)
    result = handle_ping({})
    after = int(time.time() * 1000)
    assert before <= result["ts"] <= after


def test_handle_ping_non_string_message_coerced() -> None:
    result = handle_ping({"message": 42})
    assert result["message"] == "42"


def test_dispatch_ping() -> None:
    result = dispatch("ping", {"message": "world"})
    assert result["pong"] is True
    assert result["message"] == "world"


def test_dispatch_unknown_command_raises() -> None:
    with pytest.raises(UnknownCommandError) as exc_info:
        dispatch("does.not.exist", {})
    assert exc_info.value.command == "does.not.exist"
    assert "does.not.exist" in str(exc_info.value)


def test_register_handler_overrides_and_invokes() -> None:
    def custom_handler(params: dict[str, object]) -> dict[str, object]:
        return {"custom": True, "params": params}

    register_handler("test.custom", custom_handler)
    result = dispatch("test.custom", {"x": 1})
    assert result["custom"] is True


def test_unknown_command_error_attributes() -> None:
    err = UnknownCommandError("foo.bar")
    assert err.command == "foo.bar"
    assert "foo.bar" in str(err)
