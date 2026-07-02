"""Command dispatcher for gateway ``node.invoke.request`` events.

Maps command names to handler callables and invokes them, returning a
normalised result dict suitable for the ``node.invoke.result`` request body.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
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
from openclaw_node.commands.ha import (
    handle_ha_addon_changelog,
    handle_ha_addon_documentation,
    handle_ha_addon_info,
    handle_ha_addon_logs,
    handle_ha_addon_restart,
    handle_ha_addon_start,
    handle_ha_addon_stats,
    handle_ha_addon_stop,
    handle_ha_addon_update,
    handle_ha_calendar_get_events,
    handle_ha_call_service,
    handle_ha_check_config,
    handle_ha_core_logs,
    handle_ha_get_config,
    handle_ha_get_state,
    handle_ha_history,
    handle_ha_light_turn_off,
    handle_ha_light_turn_on,
    handle_ha_list_addons,
    handle_ha_list_areas,
    handle_ha_list_automations,
    handle_ha_list_config_entries,
    handle_ha_list_devices,
    handle_ha_list_entity_registry,
    handle_ha_list_events,
    handle_ha_list_services,
    handle_ha_list_states,
    handle_ha_logbook,
    handle_ha_reload_config,
    handle_ha_update_install,
)
from openclaw_node.commands.ping import handle_ping
from openclaw_node.commands.system import handle_system_which
from openclaw_node.commands.system_run import handle_system_run

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

# Type alias for a command handler function.  Handlers may be sync or async;
# async handlers return a coroutine that :func:`dispatch_async` will await.
CommandHandler = Callable[
    [dict[str, Any]],
    dict[str, Any] | Awaitable[dict[str, Any]],
]

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
    "system.run": handle_system_run,
    "system.which": handle_system_which,
    "ha.list_states": handle_ha_list_states,
    "ha.get_state": handle_ha_get_state,
    "ha.call_service": handle_ha_call_service,
    "ha.list_areas": handle_ha_list_areas,
    "ha.list_devices": handle_ha_list_devices,
    "ha.list_services": handle_ha_list_services,
    "ha.get_config": handle_ha_get_config,
    "ha.list_events": handle_ha_list_events,
    "ha.list_config_entries": handle_ha_list_config_entries,
    "ha.core_logs": handle_ha_core_logs,
    "ha.calendar_get_events": handle_ha_calendar_get_events,
    "ha.list_entity_registry": handle_ha_list_entity_registry,
    "ha.logbook": handle_ha_logbook,
    "ha.history": handle_ha_history,
    "ha.reload_config": handle_ha_reload_config,
    "ha.light_turn_on": handle_ha_light_turn_on,
    "ha.light_turn_off": handle_ha_light_turn_off,
    "ha.list_automations": handle_ha_list_automations,
    "ha.check_config": handle_ha_check_config,
    "ha.addon_logs": handle_ha_addon_logs,
    "ha.list_addons": handle_ha_list_addons,
    "ha.addon_info": handle_ha_addon_info,
    "ha.addon_stats": handle_ha_addon_stats,
    "ha.addon_changelog": handle_ha_addon_changelog,
    "ha.addon_documentation": handle_ha_addon_documentation,
    "ha.addon_start": handle_ha_addon_start,
    "ha.addon_stop": handle_ha_addon_stop,
    "ha.addon_restart": handle_ha_addon_restart,
    "ha.addon_update": handle_ha_addon_update,
    "ha.update_install": handle_ha_update_install,
}


class AsyncHandlerError(RuntimeError):
    """Raised when an async handler is called via the sync :func:`dispatch`.

    Attributes:
        command: The async command name that triggered the error.
    """

    def __init__(self, command: str) -> None:
        """Initialise with the async command name.

        Args:
            command: The command name that returned a coroutine.
        """
        super().__init__(f"Command {command!r} is async; use dispatch_async() instead")
        self.command = command


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
    result = handler(params)
    if inspect.iscoroutine(result):
        result.close()
        raise AsyncHandlerError(command)
    return result  # type: ignore[return-value]


async def dispatch_async(command: str, params: dict[str, Any]) -> dict[str, Any]:
    """Async-aware dispatch: awaits handlers that return a coroutine.

    Args:
        command: The command name from the ``node.invoke.request`` event.
        params: The params dict from the invoke event payload.

    Returns:
        The raw result dict produced by the command handler.

    Raises:
        UnknownCommandError: If *command* has no registered handler.
    """
    handler = _REGISTRY.get(command)
    if handler is None:
        _LOG.warning("Received unknown command: %r", command)
        raise UnknownCommandError(command)

    _LOG.debug("Dispatching (async) command=%r params=%r", command, params)
    result = handler(params)
    if inspect.iscoroutine(result):
        awaited: dict[str, Any] = await result
        return awaited
    return result  # type: ignore[return-value]


_REGISTER_HANDLER_GUARD_MSG = (
    "register_handler is test/dev only. Set OPENCLAW_ALLOW_REGISTER_HANDLER=1 "
    "to override (do NOT enable in production)."
)


def register_handler(command: str, handler: CommandHandler) -> None:
    """Register or replace a command handler. Test/dev only.

    Guarded at runtime: refuses to mutate the registry unless ``pytest``
    is imported into the running process or
    ``OPENCLAW_ALLOW_REGISTER_HANDLER=1`` is set explicitly. Production
    code paths never load pytest and never set that env var, so a stray
    call site cannot grow the registry at runtime in a shipped addon.

    Args:
        command: The command name string.
        handler: A callable accepting a ``params`` dict and returning a
            result dict.

    Raises:
        RuntimeError: If neither pytest nor the explicit env-var override
            is present.
    """
    import os
    import sys

    if "pytest" not in sys.modules and os.environ.get("OPENCLAW_ALLOW_REGISTER_HANDLER") != "1":
        raise RuntimeError(_REGISTER_HANDLER_GUARD_MSG)
    _REGISTRY[command] = handler
