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
from collections.abc import AsyncIterator, Callable, Coroutine
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
        # Per #118: gateway emits session-message events under a canonical
        # form of the key (e.g. ``agent:clawd:ha-assist:01kvh...`` lowercased)
        # which differs from the raw ``ha-assist:01KVH...`` form the addon
        # sends on subscribe/chat.send. The subscribe response carries the
        # canonicalKey; we capture it and use it for ALL internal state so
        # the in-event lookup matches.
        self._canonical_by_raw: dict[str, str] = {}
        self._subscribed: set[str] = set()  # canonical keys
        # Running buffer — overwritten by every assistant event (deltas
        # included). Used as the BEST-EFFORT fallback when the turn times
        # out without ever seeing a terminal event.
        self._last_assistant_text: dict[str, str] = {}  # canonical keys
        # Only populated by TERMINAL events (chat state='final' / no state,
        # or session.message). This is what the fast-path post-ack return
        # checks so a delta arriving before chat.send acks doesn't truncate
        # the reply.
        self._terminal_assistant_text: dict[str, str] = {}  # canonical keys
        self._reply_events: dict[str, asyncio.Event] = {}  # canonical keys
        # Per-session async queue used by stream_turn(). Each delta event
        # pushes a non-empty str; the terminal event pushes None as a
        # clean-close sentinel; a disconnect pushes a ChatRelayError so
        # the consumer can distinguish a successful end-of-stream from a
        # mid-turn gateway disconnect (Codex review #126 catch — without
        # this an in-flight stream would silently emit `{"done":true}`
        # to HA instead of an error frame).
        self._delta_queues: dict[
            str, asyncio.Queue[str | None | ChatRelayError]
        ] = {}  # canonical keys
        # Per-session count of characters already yielded to the active
        # stream consumer. Used at the terminal event to compute any tail
        # that wasn't covered by deltas (or to yield the full message text
        # for session.message events that arrive without deltas).
        self._stream_yielded_chars: dict[str, int] = {}  # canonical keys
        self._active_run_id: dict[str, str] = {}  # canonical keys
        self._turn_locks: dict[str, asyncio.Lock] = {}  # raw keys

    async def stream_turn(
        self,
        conversation_id: str,
        text: str,
        language: str = "en",  # noqa: ARG002
    ) -> AsyncIterator[str]:
        """Relay one Assist turn and yield assistant text as it streams.

        Implements the streaming half of the HA conversation API: each
        gateway ``chat`` event with ``state='delta'`` yields its
        ``deltaText`` (the NEW chunk, not the cumulative text). The
        ``state='final'`` (or ``session.message``) event closes the
        iterator after yielding any tail that wasn't covered by deltas
        (typically empty when deltas have been flowing, or the full
        message text when a session.message arrives without deltas).

        Args:
            conversation_id: HA conversation id.
            text: User's input text.
            language: BCP-47 language tag (informational).

        Yields:
            Each delta chunk in the order received.

        Raises:
            ChatRelayError: On subscribe / chat.send failure. Errors during
                streaming close the iterator after yielding any text already
                received.
        """
        session_key = f"{_SESSION_KEY_PREFIX}{conversation_id}"
        lock = self._turn_locks.setdefault(session_key, asyncio.Lock())
        async with lock:
            async for chunk in self._stream_turn_locked(session_key, conversation_id, text):
                yield chunk

    async def _stream_turn_locked(
        self, session_key: str, conversation_id: str, text: str
    ) -> AsyncIterator[str]:
        """Inner streaming turn under the per-session lock.

        Turn-boundary ordering (issue #128 fix): chat.send runs FIRST so the
        new runId is known before any per-turn state is installed. This
        guarantees stale trailer events from the prior turn cannot leak
        into this turn's queue: by the time the queue exists, the prior
        turn has long since cleaned up and the new runId is in place, so
        runId mismatch filters any straggler. Previously the queue was
        installed BEFORE chat.send awaited, opening a window where late
        turn-N events landed in turn-(N+1)'s stream as a single dumped
        chunk and the actual turn-(N+1) reply never streamed deltas.
        """
        deadline = asyncio.get_event_loop().time() + _TURN_TIMEOUT_S

        canonical_key = self._canonical_by_raw.get(session_key)
        if canonical_key is None or canonical_key not in self._subscribed:
            canonical_key = await self._ensure_session(session_key, conversation_id)

        idempotency_key = str(uuid.uuid4())
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise ChatRelayError("TIMEOUT", "Turn deadline expired before chat.send")
        try:
            ack = await self._rpc(
                "chat.send",
                {
                    "sessionKey": session_key,
                    "message": text,
                    "idempotencyKey": idempotency_key,
                },
                timeout=remaining,
            )
        except ChatRelayError:
            raise
        except Exception as exc:
            raise ChatRelayError("RELAY_FAILED", str(exc)) from exc

        run_id = ""
        if isinstance(ack, dict):
            run_id = str(ack.get("runId", "") or "")

        # Atomic state install: between this point and the next await, no
        # other coroutine runs, so handle_event cannot route events into
        # the queue with mismatched runId / missing state.
        self._last_assistant_text.pop(canonical_key, None)
        self._terminal_assistant_text.pop(canonical_key, None)
        if run_id:
            self._active_run_id[canonical_key] = run_id
        else:
            # No runId in ack: clear any stale prior runId so this turn's
            # events (which will also lack a runId) are not filtered out.
            self._active_run_id.pop(canonical_key, None)
        self._stream_yielded_chars[canonical_key] = 0
        queue: asyncio.Queue[str | None | ChatRelayError] = asyncio.Queue()
        self._delta_queues[canonical_key] = queue

        try:
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    # Best-effort: drain whatever is queued without further
                    # waiting, then close.
                    while not queue.empty():
                        item = queue.get_nowait()
                        if item is None:
                            return
                        if isinstance(item, ChatRelayError):
                            raise item
                        yield item
                    return
                try:
                    async with asyncio.timeout(remaining):
                        item = await queue.get()
                except TimeoutError:
                    return
                if item is None:
                    return  # terminal sentinel
                if isinstance(item, ChatRelayError):
                    raise item  # disconnect / fatal-stream sentinel
                yield item
        finally:
            self._delta_queues.pop(canonical_key, None)
            self._stream_yielded_chars.pop(canonical_key, None)

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
        """Inner relay turn, called under the per-session lock.

        Uses a single monotonic deadline for the entire turn (RPC + deferred
        wait) so the total wall time stays within ``_TURN_TIMEOUT_S``.
        """
        deadline = asyncio.get_event_loop().time() + _TURN_TIMEOUT_S

        canonical_key = self._canonical_by_raw.get(session_key)
        if canonical_key is None or canonical_key not in self._subscribed:
            canonical_key = await self._ensure_session(session_key, conversation_id)

        # Turn-boundary ordering (issue #128 fix): chat.send first, then
        # install per-turn state once runId is known. Mirrors the
        # _stream_turn_locked ordering so stale trailer events from a
        # prior turn cannot leak into this turn's reply waiter.
        idempotency_key = str(uuid.uuid4())
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise ChatRelayError("TIMEOUT", "Turn deadline expired before chat.send")
        try:
            ack = await self._rpc(
                "chat.send",
                {
                    "sessionKey": session_key,
                    "message": text,
                    "idempotencyKey": idempotency_key,
                },
                timeout=remaining,
            )
        except ChatRelayError:
            raise
        except Exception as exc:
            raise ChatRelayError("RELAY_FAILED", str(exc)) from exc

        run_id = ""
        if isinstance(ack, dict):
            run_id = str(ack.get("runId", "") or "")

        # Atomic state install — no await between these lines, so events
        # cannot arrive into a half-installed state.
        self._last_assistant_text.pop(canonical_key, None)
        self._terminal_assistant_text.pop(canonical_key, None)
        if run_id:
            self._active_run_id[canonical_key] = run_id
        else:
            self._active_run_id.pop(canonical_key, None)
        reply_event = asyncio.Event()
        self._reply_events[canonical_key] = reply_event

        # Note: chat.send ack now precedes state install (#128), so there
        # is no longer a pre-ack fast-path — any terminal event that may
        # have raced past sessions.create/subscribe is filtered by the
        # runId guard in handle_event, and the wait-on-reply_event path
        # below handles the normal case.

        remaining = deadline - asyncio.get_event_loop().time()
        if remaining > 0:
            try:
                async with asyncio.timeout(remaining):
                    await reply_event.wait()
            except TimeoutError:
                pass
        self._reply_events.pop(canonical_key, None)

        # Terminal text wins; fall back to the last delta as a best-effort
        # truncated reply only when the turn timed out without ever seeing
        # the terminal event.
        reply = self._terminal_assistant_text.get(
            canonical_key, ""
        ) or self._last_assistant_text.get(canonical_key, "")
        if not reply:
            raise ChatRelayError(
                "NO_REPLY",
                f"No assistant reply captured for session {session_key} "
                f"(canonical={canonical_key}, runId={run_id})",
            )
        return reply

    async def _ensure_session(self, session_key: str, conversation_id: str) -> str:
        """Create the session and subscribe for messages if not already done.

        Returns the gateway's CANONICAL session key — this is the form the
        gateway emits in subsequent ``session.message`` / ``chat`` events
        (e.g. ``agent:clawd:ha-assist:01kvh...`` lowercased) and differs from
        the raw ``ha-assist:...`` key the addon sends on the request side.
        Storing internal state under the canonical key is what makes the
        receive-side lookup work (#118).

        Args:
            session_key: Gateway session key (raw form).
            conversation_id: HA conversation id for the label.

        Returns:
            The canonical session key reported by the gateway, or *session_key*
            unchanged if the subscribe response did not carry one.
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
            sub_response = await self._rpc(
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

        canonical_key = session_key
        if isinstance(sub_response, dict):
            response_key = sub_response.get("key")
            if isinstance(response_key, str) and response_key:
                canonical_key = response_key

        if canonical_key != session_key:
            _LOG.info(
                "Relay session %r resolved to canonical key %r",
                session_key,
                canonical_key,
            )
        self._canonical_by_raw[session_key] = canonical_key
        self._subscribed.add(canonical_key)
        return canonical_key

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
        try:
            await self._send(frame)
        except Exception:
            self._pending.pop(req_id, None)
            raise

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
            # Issue #118 diagnostics: even invalid-shape frames are logged
            # so we can see which events arrive at all. Suppress the
            # payload body — only structural metadata.
            _LOG.info(
                "[relay-diag] %r dropped (payload not a dict, type=%s)",
                event,
                type(payload).__name__,
            )
            return
        # Issue #118 diagnostics: log every event the relay sees, with the
        # key/role-shaped metadata that determines whether we capture it.
        # This is INFO-level (not DEBUG) so it lands in the default addon
        # log without re-deploying. Suppress the message text itself — we
        # do not want assistant replies in logs.
        msg_field_diag = payload.get("message")
        msg_role = msg_field_diag.get("role") if isinstance(msg_field_diag, dict) else None
        _LOG.info(
            "[relay-diag] event=%r sessionKey=%r role=%r msg.role=%r "
            "runId=%r state=%r subscribed=%r reply_waiters=%r",
            event,
            payload.get("sessionKey"),
            payload.get("role"),
            msg_role,
            payload.get("runId"),
            payload.get("state"),
            sorted(self._subscribed),
            sorted(self._reply_events.keys()),
        )

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

        # Only capture events when a turn is actively waiting for a reply
        # (non-streaming relay_turn) or actively streaming (stream_turn).
        # This prevents stale events from a prior turn (which may lack a
        # runId) from being captured for the next turn.
        if session_key not in self._reply_events and session_key not in self._delta_queues:
            return

        active_run = self._active_run_id.get(session_key)
        if active_run:
            # An active run is tracked. Reject events whose runId
            # mismatches. For chat-family events (which always carry a
            # runId in protocol v4), an empty runId also means stale —
            # a turn-N trailer arriving after turn-(N+1) started (issue
            # #128). session.message events may legitimately lack runId
            # (non-streaming transcript surface), so empty runId is
            # accepted only on that family.
            is_chat_family = isinstance(event, str) and event.startswith("chat")
            mismatched = bool(event_run_id) and event_run_id != active_run
            stale_chat = is_chat_family and not event_run_id
            if mismatched or stale_chat:
                _LOG.debug(
                    "Ignoring stale event for %s: event=%r runId=%r, active=%r",
                    session_key,
                    event,
                    event_run_id,
                    active_run,
                )
                return

        self._last_assistant_text[session_key] = text
        state = payload.get("state")
        is_terminal_chat = event != "session.message" and (state == "final" or state is None)
        is_session_message = event == "session.message"

        # Streaming consumer: push deltaText for delta events, push tail of
        # final text + sentinel for terminal events.
        queue = self._delta_queues.get(session_key)
        if queue is not None:
            if state == "delta":
                # Gateway includes deltaText for the new chunk. Fall back to
                # the cumulative text - already-yielded if deltaText is absent.
                delta_text = payload.get("deltaText")
                if not isinstance(delta_text, str) or not delta_text:
                    already = self._stream_yielded_chars.get(session_key, 0)
                    delta_text = text[already:] if len(text) > already else ""
                if delta_text:
                    self._stream_yielded_chars[session_key] = self._stream_yielded_chars.get(
                        session_key, 0
                    ) + len(delta_text)
                    queue.put_nowait(delta_text)
            elif is_terminal_chat or is_session_message:
                # Yield any tail not covered by deltas (or the full text on
                # session.message that arrives without preceding deltas),
                # then close.
                already = self._stream_yielded_chars.get(session_key, 0)
                tail = text[already:] if len(text) > already else ""
                # Issue #128 diagnostic: if a chat-family terminal arrives
                # with zero deltas already yielded and substantial text,
                # the gateway promised streaming but emitted only a final.
                # Log loudly so we can detect this pattern in production.
                # session.message terminals legitimately carry full text
                # without deltas (non-streaming surface), so don't warn on
                # those.
                if is_terminal_chat and already == 0 and len(tail) > 0:
                    _LOG.warning(
                        "[relay-diag] chat terminal arrived with no deltas "
                        "for %s (runId=%r, text_len=%d); gateway streaming "
                        "may be degraded — full text yielded as single chunk",
                        session_key,
                        event_run_id,
                        len(tail),
                    )
                if tail:
                    self._stream_yielded_chars[session_key] = self._stream_yielded_chars.get(
                        session_key, 0
                    ) + len(tail)
                    queue.put_nowait(tail)
                queue.put_nowait(None)  # sentinel: close iterator

        # Non-streaming consumer (relay_turn): only fire on TERMINAL events.
        # Deltas (state='delta') would otherwise wake the waiter mid-stream
        # and truncate the reply (#118 part 2). Record terminal text in its
        # own dict so a delta-before-ack race can't short-circuit the
        # fast-path return.
        if not (is_terminal_chat or is_session_message):
            return
        self._terminal_assistant_text[session_key] = text
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
        self._canonical_by_raw.clear()
        self._subscribed.clear()
        self._last_assistant_text.clear()
        self._terminal_assistant_text.clear()
        # Close any active stream consumers with a DISCONNECTED error
        # sentinel — not None — so the stream consumer raises rather
        # than falling through to a clean `{"done":true}` frame (Codex
        # review #126 catch: a mid-turn gateway disconnect was being
        # masked as a successful completed turn).
        for q in self._delta_queues.values():
            q.put_nowait(ChatRelayError("DISCONNECTED", "Gateway connection lost"))
        self._delta_queues.clear()
        self._stream_yielded_chars.clear()
        for evt in self._reply_events.values():
            evt.set()
        self._reply_events.clear()
        self._active_run_id.clear()
        self._turn_locks.clear()
