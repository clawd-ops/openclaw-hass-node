"""Pending-request correlation for gateway → node command invokes.

The gateway brain calls tools mid-turn. Each tool becomes a
``node.invoke.request`` event the gateway sends to the node, and the node
replies with a ``node.invoke.result`` carrying the same ``invokeId``.
This module owns the correlation table that matches replies to the
coroutine that initiated the invoke.

This is the gateway-side mirror of
:mod:`openclaw_node.conversation_dispatcher`; the protocol shape is the
same, only the field names differ.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Final

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

SendCallback = Callable[[dict[str, Any]], Awaitable[None]]


class InvokeDispatcherError(Exception):
    """Raised when a command invoke cannot complete."""

    def __init__(self, code: str, message: str) -> None:
        """Initialise with a stable code and human message.

        Args:
            code: Stable wire-style error code.
            message: Human-readable detail (for logs and tool_result content).
        """
        super().__init__(message)
        self.code = code
        self.message = message


class InvokeDispatcher:
    """Correlates ``node.invoke.request``/``result`` frames on the gateway.

    Args:
        send: Async callback used to actually send a frame to the node.
        timeout_s: Per-invoke timeout, applied when callers await
            :meth:`invoke`.
    """

    def __init__(self, send: SendCallback, *, timeout_s: float = 20.0) -> None:
        """Initialise an empty dispatcher.

        Args:
            send: Async callable that pushes a request dict to the WS.
            timeout_s: Default per-invoke timeout.
        """
        self._send = send
        self._timeout_s = timeout_s
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    @property
    def pending_count(self) -> int:
        """Return the number of outstanding invokes awaiting a reply."""
        return len(self._pending)

    async def invoke(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a command invoke and await the matching node reply.

        Args:
            command: The ``ha.*``/``fs.*``/``system.*`` command name.
            params: Parameter dict for the command handler.

        Returns:
            The wire result dict from the node handler.

        Raises:
            InvokeDispatcherError: On timeout, send failure, or cancel.
        """
        invoke_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[invoke_id] = future
        request = {"invokeId": invoke_id, "command": command, "params": params}
        try:
            await self._send(request)
        except Exception as exc:
            self._pending.pop(invoke_id, None)
            raise InvokeDispatcherError("SEND_FAILED", f"send error: {exc}") from exc

        try:
            async with asyncio.timeout(self._timeout_s):
                return await future
        except TimeoutError as exc:
            self._pending.pop(invoke_id, None)
            raise InvokeDispatcherError(
                "TIMEOUT",
                f"Node did not respond within {int(self._timeout_s)}s",
            ) from exc

    def handle_result(self, payload: dict[str, Any]) -> None:
        """Complete the future for a ``node.invoke.result`` frame.

        Args:
            payload: The frame payload; must include ``invokeId``.
        """
        invoke_id = str(payload.get("invokeId", ""))
        future = self._pending.pop(invoke_id, None)
        if future is None:
            _LOG.warning("Ignoring invoke result for unknown id %r", invoke_id)
            return
        if future.done():
            return
        # Strip transport-level invokeId before handing back to the caller.
        result_view = {k: v for k, v in payload.items() if k != "invokeId"}
        future.set_result(result_view)

    def cancel_all(self, reason: str = "Connection lost") -> None:
        """Fail every outstanding invoke on disconnect.

        Args:
            reason: Human-readable reason surfaced as ``message``.
        """
        pending = list(self._pending.items())
        self._pending.clear()
        for _, future in pending:
            if future.done():
                continue
            future.set_exception(InvokeDispatcherError("DISCONNECTED", reason))
