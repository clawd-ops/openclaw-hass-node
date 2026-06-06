"""Provider-agnostic brain for HA Assist turns.

The brain runs the conversation loop and routes tool calls back through an
injected callback. Model selection (Anthropic, OpenAI, future runtimes)
lives behind the :class:`Provider` interface in
:mod:`openclaw_gateway.providers`.

Per the Routing-model decision (PLAN.md §3, 2026-06-06): the user-facing
brain runs on a premium tier (Claude Opus 4.7 or GPT-5.5). Subagents the
brain spawns are unpinned. Provider choice is config-driven.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final

from openclaw_gateway.providers import (
    InvokeCallback,
    Provider,
    Round,
    ToolResult,
    ToolUse,
)

_LOG: Final[logging.Logger] = logging.getLogger(__name__)
_DEFAULT_MAX_TOKENS: Final[int] = 2048
_MAX_TOOL_ROUNDS: Final[int] = 12


class BrainError(Exception):
    """Raised when a turn cannot complete."""

    def __init__(self, code: str, message: str) -> None:
        """Initialise with a stable code and human message.

        Args:
            code: Stable wire-style error code.
            message: Human-readable detail.
        """
        super().__init__(message)
        self.code = code
        self.message = message


class Brain:
    """Conversation brain. Provider-agnostic.

    Args:
        provider: Concrete provider (Anthropic / OpenAI / ...).
        invoke_tool: Async callback that runs an ``ha.*`` tool on the node.
        tools: Anthropic-shaped tool defs from
            :data:`openclaw_gateway.tools.HA_TOOLS`. Translated for the
            provider once at construction time.
        max_tokens: Per-round output cap.
        system_prompt: Optional system prompt.
    """

    def __init__(
        self,
        provider: Provider,
        invoke_tool: InvokeCallback,
        tools: list[dict[str, Any]] | None = None,
        *,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        system_prompt: str = "",
    ) -> None:
        """Initialise the brain."""
        self._provider = provider
        self._invoke = invoke_tool
        self._tools_raw = tools or []
        self._tools_translated = provider.translate_tools(self._tools_raw)
        self._max_tokens = max_tokens
        self._system = system_prompt

    async def handle_turn(
        self,
        text: str,
        conversation_id: str | None = None,  # noqa: ARG002 - reserved
        language: str | None = None,  # noqa: ARG002 - reserved
    ) -> dict[str, Any]:
        """Run a single Assist turn end-to-end.

        Args:
            text: User-supplied Assist text.
            conversation_id: Reserved; thread state lookup may use this later.
            language: Reserved.

        Returns:
            ``{"response": str, "model": str, "provider": str, "rounds": int}``.

        Raises:
            BrainError: On model failure or tool-loop overrun.
        """
        messages: list[Any] = [{"role": "user", "content": text}]
        rounds = 0
        while rounds < _MAX_TOOL_ROUNDS:
            rounds += 1
            try:
                rd: Round = await self._provider.run_round(
                    messages,
                    self._tools_translated,
                    system_prompt=self._system,
                    max_tokens=self._max_tokens,
                )
            except BrainError:
                raise
            except Exception as exc:
                _LOG.exception("Brain model call failed on round %d", rounds)
                raise BrainError("MODEL_CALL_FAILED", str(exc)) from exc

            if rd.tool_uses:
                results = await self._run_tools(rd.tool_uses)
                messages = self._provider.append_tool_results(
                    messages, rd.raw_assistant_block, results
                )
                continue
            return {
                "response": rd.text,
                "model": self._provider.model,
                "provider": self._provider.name,
                "rounds": rounds,
            }

        raise BrainError(
            "TOOL_LOOP_OVERRUN",
            f"Brain exceeded {_MAX_TOOL_ROUNDS} tool-use rounds without resolving",
        )

    async def _run_tools(self, tool_uses: list[ToolUse]) -> list[ToolResult]:
        results: list[ToolResult] = []
        for use in tool_uses:
            try:
                wire = await self._invoke(use.name, use.input)
            except Exception as exc:
                _LOG.warning("Tool %r raised in invoke callback: %s", use.name, exc)
                wire = {"ok": False, "error": "INVOKE_FAILED", "message": str(exc)}
            results.append(
                ToolResult(
                    tool_use_id=use.id,
                    content=_format_tool_result(wire),
                    is_error=not wire.get("ok", False),
                )
            )
        return results


def _format_tool_result(wire: dict[str, Any]) -> str:
    """Render a wire result dict as compact JSON for the model."""
    try:
        return json.dumps(wire, default=str)[:8000]
    except (TypeError, ValueError):
        return str(wire)[:8000]
