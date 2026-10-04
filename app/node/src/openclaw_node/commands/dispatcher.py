"""Command dispatcher for gateway ``node.invoke.request`` events.

Maps command names to handler callables and invokes them, returning a
normalised result dict suitable for the ``node.invoke.result`` request body.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final

from openclaw_node.authz import collect_codes, redact_code, scrub_codes
from openclaw_node.caller import UNTRUSTED, Caller
from openclaw_node.commands.exec_approvals import (
    handle_system_exec_approvals_get,
    handle_system_exec_approvals_set,
    handle_system_run_prepare,
)
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
    handle_ha_supervisor_info,
    handle_ha_update_install,
)
from openclaw_node.commands.ha_config_area_registry import (
    handle_ha_config_area_registry,
)
from openclaw_node.commands.ha_config_automation import handle_ha_config_automation
from openclaw_node.commands.ha_config_config_entries import (
    handle_ha_config_config_entries,
)
from openclaw_node.commands.ha_config_device_registry import (
    handle_ha_config_device_registry,
)
from openclaw_node.commands.ha_config_entity_registry import (
    handle_ha_config_entity_registry,
)
from openclaw_node.commands.ha_config_helpers import handle_ha_config_helpers
from openclaw_node.commands.ha_config_lovelace import handle_ha_config_lovelace
from openclaw_node.commands.ha_config_scene import handle_ha_config_scene
from openclaw_node.commands.ha_config_script import handle_ha_config_script
from openclaw_node.commands.ping import handle_ping
from openclaw_node.commands.system import handle_system_which
from openclaw_node.commands.system_run import handle_system_run
from openclaw_node.effect_policy import check

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
    "system.run.prepare": handle_system_run_prepare,
    "system.which": handle_system_which,
    "system.execApprovals.get": handle_system_exec_approvals_get,
    "system.execApprovals.set": handle_system_exec_approvals_set,
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
    "ha.supervisor_info": handle_ha_supervisor_info,
    "ha.addon_start": handle_ha_addon_start,
    "ha.addon_stop": handle_ha_addon_stop,
    "ha.addon_restart": handle_ha_addon_restart,
    "ha.addon_update": handle_ha_addon_update,
    "ha.update_install": handle_ha_update_install,
    "ha.config.lovelace": handle_ha_config_lovelace,
    "ha.config.automation": handle_ha_config_automation,
    "ha.config.script": handle_ha_config_script,
    "ha.config.scene": handle_ha_config_scene,
    "ha.config.helpers": handle_ha_config_helpers,
    "ha.config.area_registry": handle_ha_config_area_registry,
    "ha.config.device_registry": handle_ha_config_device_registry,
    "ha.config.entity_registry": handle_ha_config_entity_registry,
    "ha.config.config_entries": handle_ha_config_config_entries,
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


class HandlerFailedError(Exception):
    """A handler raised while a caller-supplied code was in play; detail withheld."""


def _masked_failure(
    command: str, exc: Exception, codes: list[str | int | float]
) -> HandlerFailedError:
    """Log only the exception type and return a generic error carrying no handler text."""
    name = scrub_codes(command, codes)
    _LOG.error("Handler failed command=%r: %s", name, type(exc).__name__)
    return HandlerFailedError(f"Handler failed for command: {name!r}")


def _scrubbed(result: dict[str, Any], codes: list[str | int | float]) -> dict[str, Any]:
    """The single exit: mask every supplied code in a value leaving the dispatcher."""
    scrubbed: dict[str, Any] = scrub_codes(result, codes)
    return scrubbed


def dispatch(command: str, params: dict[str, Any], *, caller: Caller = UNTRUSTED) -> dict[str, Any]:
    """Dispatch *command* to its handler and return the result payload.

    Args:
        command: The command name from the ``node.invoke.request`` event.
        params: The params dict from the invoke event payload.
        caller: Principal making the call. Defaults to the untrusted household
            user, so a call site that forgets it is restricted.

    Returns:
        The raw result dict produced by the command handler, or the structured
        refusal when the policy gate refuses the call (the handler never runs).
        This becomes the ``payload`` field in the ``node.invoke.result`` request.

    Raises:
        UnknownCommandError: If *command* has no registered handler.

    Example:
        >>> result = dispatch("ping", {"message": "hi"})
        >>> result["pong"]
        True
    """
    codes = collect_codes(params)
    handler = _REGISTRY.get(command)
    if handler is None:
        _LOG.warning("Received unknown command: %r", scrub_codes(command, codes))
        raise UnknownCommandError(scrub_codes(command, codes))

    refusal = check(caller, command, params)
    if refusal is not None:
        _LOG.warning(
            "%s",
            scrub_codes(
                f"Refused command={command!r} caller={caller.actor_id}: {refusal['error']}", codes
            ),
        )
        return _scrubbed(refusal, codes)

    _LOG.debug("Dispatching command=%r params=%r", command, redact_code(params))
    try:
        result = handler(params)
    except Exception as exc:
        if not codes:
            raise
        raise _masked_failure(command, exc, codes) from None
    if inspect.iscoroutine(result):
        result.close()
        raise AsyncHandlerError(command)
    return _scrubbed(result, codes)  # type: ignore[arg-type]


async def dispatch_async(
    command: str, params: dict[str, Any], *, caller: Caller = UNTRUSTED
) -> dict[str, Any]:
    """Async-aware dispatch: awaits handlers that return a coroutine.

    Args:
        command: The command name from the ``node.invoke.request`` event.
        params: The params dict from the invoke event payload.
        caller: Principal making the call. Defaults to the untrusted household
            user, so a call site that forgets it is restricted.

    Returns:
        The raw result dict produced by the command handler, or the structured
        refusal when the policy gate refuses the call (the handler never runs).

    Raises:
        UnknownCommandError: If *command* has no registered handler.
    """
    codes = collect_codes(params)
    handler = _REGISTRY.get(command)
    if handler is None:
        _LOG.warning("Received unknown command: %r", scrub_codes(command, codes))
        raise UnknownCommandError(scrub_codes(command, codes))

    refusal = check(caller, command, params)
    if refusal is not None:
        _LOG.warning(
            "%s",
            scrub_codes(
                f"Refused command={command!r} caller={caller.actor_id}: {refusal['error']}", codes
            ),
        )
        return _scrubbed(refusal, codes)

    _LOG.debug("Dispatching (async) command=%r params=%r", command, redact_code(params))
    try:
        result = handler(params)
        if inspect.iscoroutine(result):
            result = await result
    except Exception as exc:
        if not codes:
            raise
        raise _masked_failure(command, exc, codes) from None
    return _scrubbed(result, codes)  # type: ignore[arg-type]
