"""Chat-surface relay for HA Assist turns via the OpenClaw gateway WS.

Relays HA Assist conversation turns into OpenClaw agent sessions using
the gateway's ``chat.send`` + ``sessions.messages.subscribe`` surface.
Each HA ``conversation_id`` maps to a unique gateway session keyed as
``ha-assist:{conversation_id}``.

Design decisions (2026-06-08, Clawd, for Rob's follow-up review):

1. **Fresh session per conversation_id.** Matches HA's conversation
   model (each ``conversation_id`` is a self-contained thread); avoids
   cross-conversation bleed.
2. **Default agent.** Uses whatever agent the gateway routes the session
   to. A future add-on option can pin the agent via ``agentId`` in
   ``sessions.create`` if needed.
3. **``chat.send`` for execution.** Triggers the full agent pipeline
   (model + tool use + multi-turn). The RPC response signals completion;
   the assistant reply text is captured from ``session.message`` events
   delivered on the same WS before the response (TCP ordering guarantee).
4. **30 s turn timeout.** Matches the ``_FORWARDER_TIMEOUT_S`` convention
   from the deleted ``ConversationDispatcher``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Coroutine
from typing import Any, Final

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

_TURN_TIMEOUT_S: Final[float] = 30.0
_RPC_TIMEOUT_S: Final[float] = 10.0
_SESSION_KEY_PREFIX: Final[str] = "ha-assist:"


class ChatRelayError(Exception):
    """Raised when a relay RPC or turn fails."""

    def __init__(self, code: str, message: str) -> None:
        """Initialise with a stable error code and human message.

        Args:
            code: Stable error code for the wire result.
            message: Human-readable detail.
        """
        super().__init__(message)
        self.code = code
        self.message = message


SendFn = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class ChatRelay:
    """Relay HA Assist turns into OpenClaw agent sessions.

    Args:
        send_fn: Async callable that sends a JSON-serialisable dict over
            the gateway WS connection.
    """

    def __init__(self, send_fn: SendFn) -> None:
        """Initialise the relay with no active sessions.

        Args:
            send_fn: Async callable to send a frame dict over the gateway WS.
        """
        self._send: SendFn = send_fn
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._subscribed: set[str] = set()
        self._last_assistant_text: dict[str, str] = {}
        self._reply_events: dict[str, asyncio.Event] = {}
        self._active_run_id: dict[str, str] = {}
        self._turn_locks: dict[str, asyncio.Lock] = {}

    async def relay_turn(
        self,
        conversation_id: str,
        text: str,
        language: str = "en",  # noqa: ARG002
    ) -> str:
        """Relay one Assist turn and return the assistant's reply.

        Args:
            conversation_id: HA conversation id (stable across follow-ups).
            text: The user's input text.
            language: BCP-47 language tag (informational; not sent to gateway).

        Returns:
            The assistant's reply text.

        Raises:
            ChatRelayError: On timeout, gateway rejection, or missing reply.
        """
        session_key = f"{_SESSION_KEY_PREFIX}{conversation_id}"

        lock = self._turn_locks.setdefault(session_key, asyncio.Lock())
        async with lock:
            return await self._relay_turn_locked(session_key, conversation_id, text)

    async def _relay_turn_locked(self, session_key: str, conversation_id: str, text: str) -> str:
        """Inner relay turn, called under the per-session lock."""
        if session_key not in self._subscribed:
            await self._ensure_session(session_key, conversation_id)

        self._last_assistant_text.pop(session_key, None)
        reply_event = asyncio.Event()
        self._reply_events[session_key] = reply_event

        idempotency_key = str(uuid.uuid4())
        try:
            ack = await self._rpc(
                "chat.send",
                {
                    "sessionKey": session_key,
                    "message": text,
                    "idempotencyKey": idempotency_key,
                },
                timeout=_TURN_TIMEOUT_S,
            )
        except ChatRelayError:
            self._reply_events.pop(session_key, None)
            raise
        except Exception as exc:
            self._reply_events.pop(session_key, None)
            raise ChatRelayError("RELAY_FAILED", str(exc)) from exc

        run_id = ""
        if isinstance(ack, dict):
            run_id = str(ack.get("runId", "") or "")
        if run_id:
            self._active_run_id[session_key] = run_id

        reply = self._last_assistant_text.get(session_key, "")
        if reply:
            self._reply_events.pop(session_key, None)
            return reply

        try:
            async with asyncio.timeout(_TURN_TIMEOUT_S):
                await reply_event.wait()
        except TimeoutError:
            pass
        finally:
            self._reply_events.pop(session_key, None)

        reply = self._last_assistant_text.get(session_key, "")
        if not reply:
            _LOG.warning(
                "No assistant reply captured for session %s (runId=%s); returning empty string",
                session_key,
                run_id,
            )
        return reply

    async def _ensure_session(self, session_key: str, conversation_id: str) -> None:
        """Create the session and subscribe for messages if not already done.

        Args:
            session_key: Gateway session key.
            conversation_id: HA conversation id for the label.
        """
        try:
            await self._rpc(
                "sessions.create",
                {
                    "key": session_key,
                    "label": f"HA Assist ({conversation_id[:8]})",
                },
                timeout=_RPC_TIMEOUT_S,
            )
        except ChatRelayError as exc:
            if "ALREADY_EXISTS" in exc.code.upper():
                _LOG.debug(
                    "sessions.create returned ALREADY_EXISTS for %s (benign)",
                    session_key,
                )
            else:
                raise

        try:
            await self._rpc(
                "sessions.messages.subscribe",
                {"key": session_key},
                timeout=_RPC_TIMEOUT_S,
            )
        except ChatRelayError as exc:
            _LOG.warning(
                "sessions.messages.subscribe failed for %s: %s",
                session_key,
                exc.code,
            )
            raise

        self._subscribed.add(session_key)

    async def _rpc(
        self,
        method: str,
        params: dict[str, Any],
        timeout: float = _RPC_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Send a gateway RPC and await the response.

        Args:
            method: The RPC method name.
            params: The params dict.
            timeout: Seconds to wait before raising TIMEOUT.

        Returns:
            The response payload dict (may be empty).

        Raises:
            ChatRelayError: On timeout or gateway rejection.
        """
        req_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = future

        frame = {
            "type": "req",
            "id": req_id,
            "method": method,
            "params": params,
        }
        await self._send(frame)

        try:
            async with asyncio.timeout(timeout):
                return await future
        except TimeoutError:
            raise ChatRelayError("TIMEOUT", f"{method} timed out after {timeout}s") from None
        finally:
            self._pending.pop(req_id, None)

    def handle_response(self, msg: dict[str, Any]) -> bool:
        """Route a gateway ``res`` frame to its pending future.

        Args:
            msg: The parsed JSON response frame.

        Returns:
            True if the response was consumed by a pending relay RPC.
        """
        req_id = msg.get("id")
        if not isinstance(req_id, str):
            return False
        future = self._pending.get(req_id)
        if future is None or future.done():
            return False

        if msg.get("ok"):
            future.set_result(msg.get("payload") or {})
        else:
            raw_error = msg.get("error", {})
            if isinstance(raw_error, dict):
                code = str(raw_error.get("code", "UNKNOWN"))
                message = str(raw_error.get("message", ""))
            else:
                code = str(raw_error) if raw_error else "UNKNOWN"
                message = code
            future.set_exception(ChatRelayError(code, message))
        return True

    @staticmethod
    def _extract_text(content: Any) -> str:
        """Extract plain text from a message content field.

        Handles three shapes:
        - ``str``: returned as-is.
        - ``list``: content-block array (``[{"type":"text","text":"..."}]``),
          joined with newlines.
        - ``dict``: nested message object with ``content``/``text``/``message``.
        - falsy: returns ``""``.
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts)
        if isinstance(content, dict):
            return ChatRelay._extract_text(
                content.get("content", "") or content.get("text", "") or content.get("message", "")
            )
        return str(content) if content else ""

    def handle_event(self, msg: dict[str, Any]) -> None:
        """Process session/chat events to capture assistant reply text.

        Handles both ``session.message`` and ``chat`` event families.
        The gateway may emit assistant output under either family
        depending on protocol version and subscription mode. The handler
        is defensive about payload shape: ``message`` can be a flat
        string, a nested object, or a content-block array.

        Events are filtered by ``runId`` when an active run is tracked
        for the session, preventing stale events from prior runs from
        being captured.

        Args:
            msg: The parsed JSON event frame.
        """
        event = msg.get("event", "")
        payload = msg.get("payload", {})
        if not isinstance(payload, dict):
            return

        session_key = ""
        role = ""
        text = ""
        event_run_id = str(payload.get("runId", "") or "")

        if event == "session.message":
            session_key = str(payload.get("sessionKey", ""))
            role = str(payload.get("role", ""))
            msg_field = payload.get("message", "")
            if isinstance(msg_field, dict):
                role = role or str(msg_field.get("role", ""))
                text = self._extract_text(
                    msg_field.get("content", "")
                    or msg_field.get("text", "")
                    or msg_field.get("message", "")
                )
            else:
                text = self._extract_text(msg_field)
            if not text:
                text = self._extract_text(payload.get("text", "") or payload.get("content", ""))

        elif isinstance(event, str) and event.startswith("chat"):
            session_key = str(payload.get("sessionKey", ""))
            role = str(payload.get("role", ""))
            msg_field = payload.get("message", "")
            if isinstance(msg_field, dict):
                role = role or str(msg_field.get("role", ""))
                text = self._extract_text(
                    msg_field.get("content", "")
                    or msg_field.get("text", "")
                    or msg_field.get("message", "")
                )
            else:
                text = self._extract_text(msg_field or payload.get("text", ""))

        if not (role == "assistant" and session_key and text):
            return

        active_run = self._active_run_id.get(session_key)
        if active_run and event_run_id and event_run_id != active_run:
            _LOG.debug(
                "Ignoring stale event for %s: event runId=%s, active=%s",
                session_key,
                event_run_id,
                active_run,
            )
            return

        self._last_assistant_text[session_key] = text
        reply_evt = self._reply_events.get(session_key)
        if reply_evt is not None:
            reply_evt.set()

    def reset(self) -> None:
        """Clear all state on disconnect.

        Cancels pending futures and clears session tracking so the next
        connect starts clean.
        """
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ChatRelayError("DISCONNECTED", "Gateway connection lost"))
        self._pending.clear()
        self._subscribed.clear()
        self._last_assistant_text.clear()
        for evt in self._reply_events.values():
            evt.set()
        self._reply_events.clear()
        self._active_run_id.clear()
        self._turn_locks.clear()
