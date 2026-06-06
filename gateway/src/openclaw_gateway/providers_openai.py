"""OpenAI Chat Completions provider for the brain.

Uses OpenAI's tool-use surface (the chat.completions ``tools`` param plus
``tool_calls`` on the assistant message). Anthropic-shaped tool defs are
translated to ``{"type": "function", "function": {…}}`` on the wire.
"""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from openclaw_gateway.providers import Round, ToolResult, ToolUse


class OpenAIProvider:
    """Wraps :class:`AsyncOpenAI` for :class:`Brain`."""

    name: str = "openai"

    def __init__(self, client: AsyncOpenAI, model: str = "gpt-5.5") -> None:
        """Initialise with a pre-constructed OpenAI client.

        Args:
            client: AsyncOpenAI instance (injectable for tests).
            model: Model id to call.
        """
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        """Return the configured model id."""
        return self._model

    def translate_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert Anthropic-shaped tool defs to OpenAI function-call shape."""
        out: list[dict[str, Any]] = []
        for t in tools:
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {"type": "object"}),
                    },
                }
            )
        return out

    async def run_round(
        self,
        messages: list[Any],
        tools: Any,
        *,
        system_prompt: str,
        max_tokens: int,
    ) -> Round:
        """Run one OpenAI Chat Completions round and normalise the response."""
        full_messages = list(messages)
        if system_prompt and not (full_messages and full_messages[0].get("role") == "system"):
            full_messages.insert(0, {"role": "system", "content": system_prompt})

        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            tools=tools,
            messages=full_messages,
        )
        choice = response.choices[0]
        message = choice.message
        tool_calls = getattr(message, "tool_calls", None) or []
        tool_uses: list[ToolUse] = []
        for tc in tool_calls:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            try:
                args = json.loads(getattr(fn, "arguments", "") or "{}")
            except (ValueError, TypeError):
                args = {}
            tool_uses.append(
                ToolUse(
                    id=str(getattr(tc, "id", "")),
                    name=str(getattr(fn, "name", "")),
                    input=args if isinstance(args, dict) else {},
                )
            )
        text = str(getattr(message, "content", "") or "")
        if choice.finish_reason == "tool_calls" and not tool_uses:
            from openclaw_gateway.brain import BrainError

            msg = "finish_reason=tool_calls without tool_calls field"
            raise BrainError("PROTOCOL_ERROR", msg)
        return Round(text=text, tool_uses=tool_uses, raw_assistant_block=message)

    def append_tool_results(
        self,
        messages: list[Any],
        round_block: Any,
        results: list[ToolResult],
    ) -> list[Any]:
        """Append the assistant message and role=tool messages to messages."""
        # OpenAI wants the assistant message dict (with tool_calls), then one
        # role=tool message per tool_use_id.
        assistant_msg = round_block
        # SDK objects are not raw dicts; build a dict the API will accept.
        tool_calls_payload = []
        for tc in getattr(assistant_msg, "tool_calls", None) or []:
            fn = getattr(tc, "function", None)
            tool_calls_payload.append(
                {
                    "id": getattr(tc, "id", ""),
                    "type": "function",
                    "function": {
                        "name": getattr(fn, "name", "") if fn else "",
                        "arguments": getattr(fn, "arguments", "") if fn else "",
                    },
                }
            )
        messages.append(
            {
                "role": "assistant",
                "content": getattr(assistant_msg, "content", None),
                "tool_calls": tool_calls_payload,
            }
        )
        for r in results:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": r.tool_use_id,
                    "content": r.content,
                }
            )
        return messages
