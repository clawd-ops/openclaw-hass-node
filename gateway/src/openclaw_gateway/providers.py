"""Model-provider abstraction for the brain.

The brain runs a generic ``messages → tool_use → tool_result → messages``
loop. The provider hides which SDK actually answers ``run_round``: Anthropic
Messages, OpenAI Responses, or a future runtime (Codex, OpenRouter, etc.).

Provider authors implement two methods:

- :meth:`Provider.translate_tools` — convert Anthropic-shaped tool defs from
  ``openclaw_gateway.tools.HA_TOOLS`` into the provider's wire format.
- :meth:`Provider.run_round` — send the current message history plus tools
  to the model and return a normalised :class:`Round` describing whether the
  model returned text, asked for tool calls, or failed.

The brain itself does the conversation bookkeeping and tool routing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolUse:
    """A normalised tool_use block emitted by the model."""

    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class Round:
    """Normalised result of a single ``messages.create``-shaped call.

    Attributes:
        text: Final assistant text. Set only when ``tool_uses`` is empty.
        tool_uses: List of tool calls the model wants the brain to run.
        raw_assistant_block: Provider-specific object to append back to the
            messages list when there are tool_uses (Anthropic wants the
            assistant message; OpenAI wants its message dict).
    """

    text: str
    tool_uses: list[ToolUse]
    raw_assistant_block: Any


@dataclass
class ToolResult:
    """A normalised tool result to feed back to the model on the next round.

    Attributes:
        tool_use_id: The id from the originating tool_use block.
        content: Stringified wire result the node returned.
        is_error: True when the node call failed or the brain's invoke
            callback raised.
    """

    tool_use_id: str
    content: str
    is_error: bool


class Provider(Protocol):
    """Interface every model provider implements."""

    @property
    def name(self) -> str:
        """Stable provider id (``"anthropic"``, ``"openai"``, …)."""

    @property
    def model(self) -> str:
        """The configured model id used for this provider."""

    def translate_tools(self, tools: list[dict[str, Any]]) -> Any:
        """Convert Anthropic-shaped tool defs to the provider's wire format.

        Args:
            tools: Anthropic-shaped tool definitions
                (``{name, description, input_schema}``).

        Returns:
            Provider-specific value suitable for the ``tools`` kwarg.
        """

    async def run_round(
        self,
        messages: list[Any],
        tools: Any,
        *,
        system_prompt: str,
        max_tokens: int,
    ) -> Round:
        """Run one round of conversation.

        Args:
            messages: The running message history. Provider-specific shape.
            tools: The translated tool catalog from :meth:`translate_tools`.
            system_prompt: Optional system prompt.
            max_tokens: Per-round output cap.

        Returns:
            A :class:`Round` describing what the model produced.
        """

    def append_tool_results(
        self,
        messages: list[Any],
        round_block: Any,
        results: list[ToolResult],
    ) -> list[Any]:
        """Extend *messages* with the assistant block and the tool_results.

        Returns:
            The updated message list (providers may copy or mutate in place).
        """


InvokeCallback = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
