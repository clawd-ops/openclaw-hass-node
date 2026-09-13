"""Tests for the command dispatcher."""

from __future__ import annotations

import pytest

from openclaw_node.commands.dispatcher import UnknownCommandError, dispatch


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
