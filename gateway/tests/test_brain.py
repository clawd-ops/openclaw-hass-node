"""Tests for openclaw_gateway.brain — Provider-agnostic."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from openclaw_gateway.brain import Brain, BrainError
from openclaw_gateway.providers import Round, ToolResult, ToolUse


class _FakeProvider:
    """Minimal Provider stub. Returns scripted Rounds in order."""

    name = "fake"

    def __init__(self, rounds: list[Round], *, model: str = "fake-model") -> None:
        self._rounds = list(rounds)
        self._model = model
        self.calls: list[tuple[list[Any], Any, str, int]] = []
        self.appended: list[list[ToolResult]] = []

    @property
    def model(self) -> str:
        return self._model

    def translate_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return tools

    async def run_round(
        self,
        messages: list[Any],
        tools: Any,
        *,
        system_prompt: str,
        max_tokens: int,
    ) -> Round:
        self.calls.append((list(messages), tools, system_prompt, max_tokens))
        if not self._rounds:
            msg = "fake provider ran out of scripted rounds"
            raise BrainError("PROTOCOL_ERROR", msg)
        return self._rounds.pop(0)

    def append_tool_results(
        self,
        messages: list[Any],
        round_block: Any,
        results: list[ToolResult],
    ) -> list[Any]:
        self.appended.append(results)
        # Use the same shape as AnthropicProvider so tests can assert on it.
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


async def test_brain_single_turn_no_tools() -> None:
    """One round with text returns immediately."""
    prov = _FakeProvider([Round(text="hello there", tool_uses=[], raw_assistant_block=None)])
    brain = Brain(prov, invoke_tool=AsyncMock())
    out = await brain.handle_turn("hi")
    assert out["response"] == "hello there"
    assert out["rounds"] == 1
    assert out["provider"] == "fake"
    assert out["model"] == "fake-model"


async def test_brain_one_tool_round_then_final_text() -> None:
    """tool_use → invoke → next round text completes the turn."""
    use = ToolUse(id="t1", name="ha.list_states", input={"domain": "light"})
    rounds = [
        Round(text="", tool_uses=[use], raw_assistant_block="block1"),
        Round(text="Kitchen light is on.", tool_uses=[], raw_assistant_block=None),
    ]
    prov = _FakeProvider(rounds)
    invoke = AsyncMock(return_value={"ok": True, "states": [{"entity_id": "light.kitchen"}]})
    brain = Brain(prov, invoke_tool=invoke, tools=[{"name": "ha.list_states"}])
    out = await brain.handle_turn("which lights are on?")
    assert out["response"] == "Kitchen light is on."
    assert out["rounds"] == 2
    invoke.assert_awaited_once_with("ha.list_states", {"domain": "light"})
    # The provider's append_tool_results saw the result.
    assert prov.appended
    assert prov.appended[0][0].tool_use_id == "t1"
    assert prov.appended[0][0].is_error is False


async def test_brain_tool_result_carries_wire_payload() -> None:
    """Wire dict is JSON-encoded into the tool_result content string."""
    use = ToolUse(id="t1", name="ha.get_state", input={"entity_id": "x"})
    rounds = [
        Round(text="", tool_uses=[use], raw_assistant_block=None),
        Round(text="ok", tool_uses=[], raw_assistant_block=None),
    ]
    prov = _FakeProvider(rounds)
    invoke = AsyncMock(return_value={"ok": True, "state": {"entity_id": "x", "state": "on"}})
    brain = Brain(prov, invoke_tool=invoke)
    await brain.handle_turn("status?")
    payload = prov.appended[0][0].content
    parsed = json.loads(payload)
    assert parsed["state"]["state"] == "on"


async def test_brain_invoke_failure_becomes_tool_error() -> None:
    """Exception from invoke callback → is_error=True with INVOKE_FAILED."""
    use = ToolUse(id="t1", name="ha.get_state", input={"entity_id": "x"})
    rounds = [
        Round(text="", tool_uses=[use], raw_assistant_block=None),
        Round(text="retried", tool_uses=[], raw_assistant_block=None),
    ]
    prov = _FakeProvider(rounds)

    async def _bad(command: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("boom")

    brain = Brain(prov, invoke_tool=_bad)
    await brain.handle_turn("hi")
    block = prov.appended[0][0]
    assert block.is_error is True
    assert "INVOKE_FAILED" in block.content


async def test_brain_wire_error_propagated_as_is_error() -> None:
    """A wire result with ok:False is surfaced as is_error."""
    use = ToolUse(id="t1", name="ha.light_turn_on", input={})
    rounds = [
        Round(text="", tool_uses=[use], raw_assistant_block=None),
        Round(text="done", tool_uses=[], raw_assistant_block=None),
    ]
    prov = _FakeProvider(rounds)
    invoke = AsyncMock(return_value={"ok": False, "error": "MISSING_PARAM"})
    brain = Brain(prov, invoke_tool=invoke)
    await brain.handle_turn("on")
    assert prov.appended[0][0].is_error is True


async def test_brain_model_call_failure_raises_brain_error() -> None:
    """An exception inside run_round raises BrainError(MODEL_CALL_FAILED)."""

    class _BadProvider(_FakeProvider):
        async def run_round(self, *args: Any, **kwargs: Any) -> Round:
            raise ConnectionError("api down")

    brain = Brain(_BadProvider([]), invoke_tool=AsyncMock())
    with pytest.raises(BrainError) as ei:
        await brain.handle_turn("hi")
    assert ei.value.code == "MODEL_CALL_FAILED"


async def test_brain_tool_loop_overrun_raises() -> None:
    """13 consecutive tool_use rounds raise TOOL_LOOP_OVERRUN."""
    looping = Round(
        text="",
        tool_uses=[ToolUse(id="t", name="ha.list_states", input={})],
        raw_assistant_block=None,
    )
    prov = _FakeProvider([looping] * 13)
    invoke = AsyncMock(return_value={"ok": True})
    brain = Brain(prov, invoke_tool=invoke)
    with pytest.raises(BrainError) as ei:
        await brain.handle_turn("loop")
    assert ei.value.code == "TOOL_LOOP_OVERRUN"


async def test_brain_propagates_provider_brain_error() -> None:
    """If a provider raises BrainError, the brain re-raises (no wrap)."""

    class _ProtoBadProvider(_FakeProvider):
        async def run_round(self, *args: Any, **kwargs: Any) -> Round:
            raise BrainError("PROTOCOL_ERROR", "bad shape")

    brain = Brain(_ProtoBadProvider([]), invoke_tool=AsyncMock())
    with pytest.raises(BrainError) as ei:
        await brain.handle_turn("hi")
    assert ei.value.code == "PROTOCOL_ERROR"


async def test_brain_passes_system_prompt_and_max_tokens() -> None:
    prov = _FakeProvider([Round(text="hi", tool_uses=[], raw_assistant_block=None)])
    brain = Brain(prov, invoke_tool=AsyncMock(), system_prompt="be brief", max_tokens=42)
    await brain.handle_turn("yo")
    _, _, system, max_tokens = prov.calls[0]
    assert system == "be brief"
    assert max_tokens == 42
