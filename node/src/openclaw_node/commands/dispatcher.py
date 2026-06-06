"""Command dispatcher for gateway ``node.invoke.request`` events.

Maps command names to handler callables and invokes them, returning a
normalised result dict suitable for the ``node.invoke.result`` request body.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Final

from openclaw_node.commands.fs import (
    handle_fs_glob,
    handle_fs_list,
    handle_fs_read,
    handle_fs_stat,
)
from openclaw_node.commands.fs_move_delete import handle_fs_delete, handle_fs_move
from openclaw_node.commands.fs_patch import handle_fs_patch
from openclaw_node.commands.fs_write import (
    handle_fs_diff,
    handle_fs_history,
    handle_fs_restore,
    handle_fs_write,
)
from openclaw_node.commands.ping import handle_ping
from openclaw_node.commands.system import handle_system_which

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

# Type alias for a command handler function.
CommandHandler = Callable[[dict[str, Any]], dict[str, Any]]

# Registry of command name → handler.
_REGISTRY: dict[str, CommandHandler] = {
    "ping": handle_ping,
    "fs.read": handle_fs_read,
    "fs.list": handle_fs_list,
    "fs.stat": handle_fs_stat,
    "fs.glob": handle_fs_glob,
    "fs.write": handle_fs_write,
    "fs.restore": handle_fs_restore,
    "fs.history": handle_fs_history,
    "fs.diff": handle_fs_diff,
    "fs.move": handle_fs_move,
    "fs.delete": handle_fs_delete,
    "fs.patch": handle_fs_patch,
    "system.which": handle_system_which,
}


class UnknownCommandError(Exception):
    """Raised when the dispatcher receives a command name it has no handler for.

    Attributes:
        command: The unrecognised command name.
    """

    def __init__(self, command: str) -> None:
        """Initialise with the unknown command name.

        Args:
            command: The unrecognised command name string.
        """
        super().__init__(f"No handler registered for command: {command!r}")
        self.command = command


def dispatch(command: str, params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch *command* to its handler and return the result payload.

    Args:
        command: The command name from the ``node.invoke.request`` event.
        params: The params dict from the invoke event payload.

    Returns:
        The raw result dict produced by the command handler.  This becomes
        the ``result`` field in the ``node.invoke.result`` request.

    Raises:
        UnknownCommandError: If *command* has no registered handler.

    Example:
        >>> result = dispatch("ping", {"message": "hi"})
        >>> result["pong"]
        True
    """
    handler = _REGISTRY.get(command)
    if handler is None:
        _LOG.warning("Received unknown command: %r", command)
        raise UnknownCommandError(command)

    _LOG.debug("Dispatching command=%r params=%r", command, params)
    return handler(params)


def register_handler(command: str, handler: CommandHandler) -> None:
    """Register a new command handler, replacing any existing one.

    This is intended for use in tests or future phases to extend the command
    surface without modifying this module.

    Args:
        command: The command name string.
        handler: A callable accepting a ``params`` dict and returning a result
            dict.
    """
    _LOG.debug("Registering handler for command=%r", command)
    _REGISTRY[command] = handler
