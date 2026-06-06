"""Anthropic Messages provider for the brain."""

from __future__ import annotations

from typing import Any, cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolParam

from openclaw_gateway.providers import Round, ToolResult, ToolUse


class AnthropicProvider:
    """Wraps :class:`AsyncAnthropic` for :class:`Brain`."""

    name: str = "anthropic"

    def __init__(self, client: AsyncAnthropic, model: str = "claude-opus-4-7") -> None:
        """Initialise with a pre-constructed Anthropic client.

        Args:
            client: AsyncAnthropic instance (injectable for tests).
            model: Model id to call.
        """
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        """Return the configured model id."""
        return self._model

    def translate_tools(self, tools: list[dict[str, Any]]) -> list[ToolParam]:
        """Pass through — Anthropic tool defs already match HA_TOOLS shape."""
        return cast("list[ToolParam]", tools)

    async def run_round(
        self,
        messages: list[Any],
        tools: Any,
        *,
        system_prompt: str,
        max_tokens: int,
    ) -> Round:
        """Run one Anthropic Messages round and normalise the response."""
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system_prompt or "",
            tools=tools,
            messages=cast("list[MessageParam]", messages),
        )
        stop_reason = getattr(response, "stop_reason", None)
        content = getattr(response, "content", []) or []
        tool_uses: list[ToolUse] = []
        text_parts: list[str] = []
        for block in content:
            if getattr(block, "type", None) == "tool_use":
                tool_uses.append(
                    ToolUse(
                        id=str(getattr(block, "id", "")),
                        name=str(getattr(block, "name", "")),
                        input=dict(getattr(block, "input", {}) or {}),
                    )
                )
            elif getattr(block, "type", None) == "text":
                text_parts.append(str(getattr(block, "text", "")))
        if stop_reason == "tool_use" and not tool_uses:
            from openclaw_gateway.brain import BrainError

            msg = "stop_reason=tool_use without tool_use block"
            raise BrainError("PROTOCOL_ERROR", msg)
        return Round(text="".join(text_parts), tool_uses=tool_uses, raw_assistant_block=content)

    def append_tool_results(
        self,
        messages: list[Any],
        round_block: Any,
        results: list[ToolResult],
    ) -> list[Any]:
        """Append the assistant block and a user tool_result block to messages."""
        messages.append({"role": "assistant", "content": round_block})
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.tool_use_id,
                        "content": r.content,
                        "is_error": r.is_error,
                    }
                    for r in results
                ],
            }
        )
        return messages
