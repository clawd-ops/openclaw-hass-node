"""Claude-powered brain for HA Assist turns.

The brain takes a single user turn and returns a final response, with
unbounded tool-call rounds in between. Tool calls are routed back through
an injected callback so the brain stays I/O-agnostic — the gateway WS
client supplies the actual ``node.invoke.request`` plumbing.

Default model is **Claude Opus 4.7** (``claude-opus-4-7``). Per the
Routing-model decision (PLAN.md §3, 2026-06-06), the user-facing brain
runs on a premium tier; subagents the brain spawns are unpinned.

Workers (subagents) are deliberately not implemented here. The brain calls
``ha.*`` tools directly. If/when the brain needs to delegate, it does so
inside its own tool-call sequence — the protocol does not change.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final, cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolParam

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

# Default brain model. Premium tier per Routing-model decision.
_DEFAULT_MODEL: Final[str] = "claude-opus-4-7"
_DEFAULT_MAX_TOKENS: Final[int] = 2048
# Hard cap on tool-use rounds to bound a single turn.
_MAX_TOOL_ROUNDS: Final[int] = 12

# Callable[(command, params)] -> result dict or error dict.
InvokeCallback = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class BrainError(Exception):
    """Raised when a turn cannot complete."""

    def __init__(self, code: str, message: str) -> None:
        """Initialise with a stable code and human message.

        Args:
            code: Stable wire-style error code.
            message: Human-readable detail (for logs and gateway responses).
        """
        super().__init__(message)
        self.code = code
        self.message = message


class Brain:
    """Claude-backed conversation brain with HA tool use.

    Args:
        client: Anthropic async client (real or mocked for tests).
        invoke_tool: Async callback the brain calls to run an ``ha.*`` tool
            on the node side. Receives (command, params), returns the wire
            result dict (``{ok: True, ...}`` or ``{ok: False, error, ...}``).
        tools: Anthropic-shaped tool definitions advertised to the model.
        model: Override the default model id.
        max_tokens: Cap output tokens per round.
        system_prompt: System prompt prepended to every turn.

    Example:
        >>> brain = Brain(client, invoke_cb, tools=[{"name": "ha.list_states", ...}])
        >>> result = await brain.handle_turn("turn off the kitchen light")
    """

    def __init__(
        self,
        client: AsyncAnthropic,
        invoke_tool: InvokeCallback,
        tools: list[dict[str, Any]] | None = None,
        *,
        model: str = _DEFAULT_MODEL,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        system_prompt: str = "",
    ) -> None:
        """Initialise the brain.

        Args:
            client: Anthropic async client (injected so tests can mock it).
            invoke_tool: Async callback for routing tool calls to the node.
            tools: List of Anthropic-shaped tool definitions.
            model: Override the default brain model id.
            max_tokens: Per-round output token cap.
            system_prompt: Optional system prompt.
        """
        self._client = client
        self._invoke = invoke_tool
        self._tools = tools or []
        self._model = model
        self._max_tokens = max_tokens
        self._system = system_prompt

    async def handle_turn(
        self,
        text: str,
        conversation_id: str | None = None,  # noqa: ARG002 - reserved for thread state
        language: str | None = None,  # noqa: ARG002 - reserved
    ) -> dict[str, Any]:
        """Run a single Assist turn end-to-end.

        Args:
            text: User-supplied Assist text.
            conversation_id: Reserved; thread state lookup may use this later.
            language: Reserved; system prompt language tuning may use this later.

        Returns:
            ``{"response": str, "model": str, "rounds": int}`` on success.

        Raises:
            BrainError: On model failure or tool-loop overrun.
        """
        messages: list[dict[str, Any]] = [{"role": "user", "content": text}]
        rounds = 0

        while rounds < _MAX_TOOL_ROUNDS:
            rounds += 1
            try:
                response = await self._client.messages.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=self._system or "",
                    tools=cast("list[ToolParam]", self._tools),
                    messages=cast("list[MessageParam]", messages),
                )
            except Exception as exc:
                _LOG.exception("Brain model call failed on round %d", rounds)
                raise BrainError("MODEL_CALL_FAILED", str(exc)) from exc

            stop_reason = getattr(response, "stop_reason", None)
            content = getattr(response, "content", []) or []

            if stop_reason == "tool_use":
                tool_uses = [b for b in content if getattr(b, "type", None) == "tool_use"]
                if not tool_uses:
                    raise BrainError(
                        "PROTOCOL_ERROR",
                        "stop_reason=tool_use without tool_use block",
                    )
                messages.append({"role": "assistant", "content": content})
                tool_results = await self._run_tools(tool_uses)
                messages.append({"role": "user", "content": tool_results})
                continue

            text_out = _extract_text(content)
            return {"response": text_out, "model": self._model, "rounds": rounds}

        raise BrainError(
            "TOOL_LOOP_OVERRUN",
            f"Brain exceeded {_MAX_TOOL_ROUNDS} tool-use rounds without resolving",
        )

    async def _run_tools(self, tool_uses: list[Any]) -> list[dict[str, Any]]:
        """Execute each tool_use block via the injected callback.

        Args:
            tool_uses: List of tool_use content blocks from the model response.

        Returns:
            A list of tool_result blocks shaped for the next assistant turn.
        """
        results: list[dict[str, Any]] = []
        for use in tool_uses:
            command = str(getattr(use, "name", ""))
            tool_id = str(getattr(use, "id", ""))
            params = dict(getattr(use, "input", {}) or {})
            try:
                wire_result = await self._invoke(command, params)
            except Exception as exc:
                _LOG.warning("Tool %r raised in invoke callback: %s", command, exc)
                wire_result = {"ok": False, "error": "INVOKE_FAILED", "message": str(exc)}
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": _format_tool_result(wire_result),
                    "is_error": not wire_result.get("ok", False),
                }
            )
        return results


def _extract_text(content: list[Any]) -> str:
    """Concatenate text content blocks into a single string.

    Args:
        content: Content block list from the model response.

    Returns:
        Joined text, or an empty string if no text blocks are present.
    """
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
    return "".join(parts)


def _format_tool_result(wire: dict[str, Any]) -> str:
    """Render a wire result dict as a compact string for the model.

    Args:
        wire: Wire result dict from the node-side ``ha.*`` handler.

    Returns:
        Compact JSON-style text describing the result; safe for the model
        to parse.
    """
    import json

    try:
        return json.dumps(wire, default=str)[:8000]
    except (TypeError, ValueError):
        return str(wire)[:8000]
