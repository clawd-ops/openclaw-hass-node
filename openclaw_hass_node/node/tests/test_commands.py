"""Tests for the command dispatcher."""

from __future__ import annotations

import pytest
from openclaw_node.commands.dispatcher import UnknownCommandError, dispatch, register_handler


def test_ping_dispatch() -> None:
    """The ping command returns a pong payload."""
    result = dispatch("ping", {"message": "hello"})

    assert result["pong"] is True
    assert result["message"] == "hello"
    assert isinstance(result["ts"], int)


def test_unknown_command_raises() -> None:
    """Unknown commands fail explicitly."""
    with pytest.raises(UnknownCommandError) as exc:
        dispatch("nope", {})

    assert exc.value.command == "nope"


def test_register_handler() -> None:
    """Handlers can be registered for future command modules."""
    register_handler("test.echo", lambda params: {"echo": params["value"]})

    assert dispatch("test.echo", {"value": 42}) == {"echo": 42}
