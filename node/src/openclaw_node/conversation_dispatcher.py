"""Pending-request correlation for the gateway conversation forwarder.

The node sends ``node.conversation.request`` frames over its single WS
connection and the gateway replies asynchronously with
``node.conversation.result`` frames carrying the same ``conversationId``.
This module owns the correlation table that matches replies to the
coroutine that initiated the request, so request senders can simply
``await dispatcher.forward(...)`` and get the gateway's response back.

The dispatcher is intentionally I/O-agnostic — the actual WS send is
injected as a callable at construction time so tests can run without a
real socket.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Final

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

SendCallback = Callable[[dict[str, Any]], Awaitable[None]]


class ConversationDispatcherError(Exception):
    """Raised when a conversation forward cannot complete."""

    def __init__(self, code: str, message: str) -> None:
        """Initialise with a stable error code and human message.

        Args:
            code: Stable wire-style error code.
            message: Human-readable detail (for logs and responses).
        """
        super().__init__(message)
        self.code = code
        self.message = message


class ConversationDispatcher:
    """Correlates ``node.conversation.request``/``result`` frames.

    Args:
        send: Async callback used to actually send a frame to the gateway.
        timeout_s: Per-request timeout, applied when callers await
            :meth:`forward`.

    Example:
        >>> async def send(req): pass
        >>> d = ConversationDispatcher(send=send)
        >>> # awaiting d.forward(...) blocks until handle_result fires.
    """

    def __init__(self, send: SendCallback, *, timeout_s: float = 30.0) -> None:
        """Initialise an empty dispatcher.

        Args:
            send: Async callable that pushes a request dict to the WS.
            timeout_s: Default per-request timeout.
        """
        self._send = send
        self._timeout_s = timeout_s
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    @property
    def pending_count(self) -> int:
        """Return the number of outstanding requests awaiting a reply."""
        return len(self._pending)

    async def forward(
        self,
        text: str,
        conversation_id: str | None,
        language: str | None,
    ) -> dict[str, Any]:
        """Send a conversation request and await the matching reply.

        Args:
            text: User-supplied Assist text.
            conversation_id: Optional caller-supplied conversation handle.
            language: Optional language code.

        Returns:
            The reply payload from the gateway (e.g. ``{"response": "..."}``).

        Raises:
            ConversationDispatcherError: On timeout, send failure, or cancel.
        """
        request_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        request = {
            "conversationId": request_id,
            "text": text,
            "conversationContextId": conversation_id,
            "language": language,
        }
        try:
            await self._send(request)
        except Exception as exc:
            self._pending.pop(request_id, None)
            raise ConversationDispatcherError("SEND_FAILED", f"send error: {exc}") from exc

        try:
            async with asyncio.timeout(self._timeout_s):
                return await future
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise ConversationDispatcherError(
                "TIMEOUT",
                f"Gateway did not respond within {int(self._timeout_s)}s",
            ) from exc

    def handle_result(self, payload: dict[str, Any]) -> None:
        """Complete the future for a ``node.conversation.result`` frame.

        Args:
            payload: The frame payload; must include ``conversationId``.
        """
        request_id = str(payload.get("conversationId", ""))
        future = self._pending.pop(request_id, None)
        if future is None:
            _LOG.warning("Ignoring conversation result for unknown id %r", request_id)
            return
        if future.done():
            return
        future.set_result(payload)

    def cancel_all(self, reason: str = "Connection lost") -> None:
        """Fail every outstanding request, e.g. on WS disconnect.

        Args:
            reason: Human-readable reason for the cancel; surfaced as the
                ``ConversationDispatcherError.message``.
        """
        pending = list(self._pending.items())
        self._pending.clear()
        for _, future in pending:
            if future.done():
                continue
            future.set_exception(ConversationDispatcherError("DISCONNECTED", reason))
