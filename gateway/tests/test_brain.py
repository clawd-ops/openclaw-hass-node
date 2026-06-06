"""Tests for openclaw_gateway.brain."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from openclaw_gateway.brain import Brain, BrainError


def _text_block(text: str) -> MagicMock:
    """Build a fake Anthropic text content block."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _tool_use_block(name: str, tool_id: str, params: dict[str, Any]) -> MagicMock:
    """Build a fake Anthropic tool_use content block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.id = tool_id
    block.input = params
    return block


def _response(stop_reason: str, content: list[MagicMock]) -> MagicMock:
    """Build a fake Anthropic Messages response."""
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.content = content
    return resp


def _make_client(responses: list[MagicMock]) -> MagicMock:
    """Build a mocked AsyncAnthropic client that returns responses in order."""
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(side_effect=responses)
    return client


async def test_brain_single_turn_no_tools() -> None:
    """A response with stop_reason=end_turn returns immediately."""
    client = _make_client([_response("end_turn", [_text_block("hello there")])])
    invoke = AsyncMock()
    brain = Brain(client, invoke_tool=invoke)
    result = await brain.handle_turn("hi")
    assert result["response"] == "hello there"
    assert result["rounds"] == 1
    invoke.assert_not_called()


async def test_brain_one_tool_round_then_final_text() -> None:
    """tool_use → invoke → next response with final text completes the turn."""
    use_response = _response(
        "tool_use",
        [_tool_use_block("ha.list_states", "t1", {"domain": "light"})],
    )
    final = _response("end_turn", [_text_block("Kitchen light is on.")])
    client = _make_client([use_response, final])
    invoke = AsyncMock(return_value={"ok": True, "states": [{"entity_id": "light.kitchen"}]})
    brain = Brain(client, invoke_tool=invoke, tools=[{"name": "ha.list_states"}])

    result = await brain.handle_turn("which lights are on?")
    assert result["response"] == "Kitchen light is on."
    assert result["rounds"] == 2
    invoke.assert_awaited_once_with("ha.list_states", {"domain": "light"})

    # The model received the tool result as a user-turn block.
    second_call = client.messages.create.await_args_list[1]
    msgs = second_call.kwargs["messages"]
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"][0]["type"] == "tool_result"
    assert msgs[-1]["content"][0]["tool_use_id"] == "t1"
    assert msgs[-1]["content"][0]["is_error"] is False


async def test_brain_tool_result_carries_wire_payload() -> None:
    """Wire result is JSON-encoded into the tool_result content."""
    client = _make_client(
        [
            _response("tool_use", [_tool_use_block("ha.get_state", "t1", {"entity_id": "x"})]),
            _response("end_turn", [_text_block("done")]),
        ]
    )
    invoke = AsyncMock(return_value={"ok": True, "state": {"entity_id": "x", "state": "on"}})
    brain = Brain(client, invoke_tool=invoke)
    await brain.handle_turn("status?")
    msgs = client.messages.create.await_args_list[1].kwargs["messages"]
    payload = msgs[-1]["content"][0]["content"]
    parsed = json.loads(payload)
    assert parsed["state"]["state"] == "on"


async def test_brain_invoke_failure_becomes_tool_error() -> None:
    """An exception raised by the invoke callback becomes is_error=True."""
    client = _make_client(
        [
            _response("tool_use", [_tool_use_block("ha.get_state", "t1", {"entity_id": "x"})]),
            _response("end_turn", [_text_block("retrying")]),
        ]
    )

    async def _bad_invoke(command: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("boom")

    brain = Brain(client, invoke_tool=_bad_invoke)
    await brain.handle_turn("hi")
    msgs = client.messages.create.await_args_list[1].kwargs["messages"]
    block = msgs[-1]["content"][0]
    assert block["is_error"] is True
    assert "INVOKE_FAILED" in block["content"]


async def test_brain_wire_error_propagated_as_is_error() -> None:
    """A wire result with ok:False is surfaced as is_error in the tool_result."""
    client = _make_client(
        [
            _response("tool_use", [_tool_use_block("ha.light_turn_on", "t1", {})]),
            _response("end_turn", [_text_block("ok")]),
        ]
    )
    invoke = AsyncMock(return_value={"ok": False, "error": "MISSING_PARAM"})
    brain = Brain(client, invoke_tool=invoke)
    await brain.handle_turn("on")
    msgs = client.messages.create.await_args_list[1].kwargs["messages"]
    assert msgs[-1]["content"][0]["is_error"] is True


async def test_brain_model_call_failure_raises_braerror() -> None:
    """An exception from the client.messages.create raises BrainError."""
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(side_effect=ConnectionError("api down"))
    brain = Brain(client, invoke_tool=AsyncMock())
    with pytest.raises(BrainError) as ei:
        await brain.handle_turn("hi")
    assert ei.value.code == "MODEL_CALL_FAILED"


async def test_brain_tool_loop_overrun_raises() -> None:
    """Endless tool_use rounds eventually raise TOOL_LOOP_OVERRUN."""
    looping = _response("tool_use", [_tool_use_block("ha.list_states", "t1", {})])
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(return_value=looping)
    invoke = AsyncMock(return_value={"ok": True})
    brain = Brain(client, invoke_tool=invoke)
    with pytest.raises(BrainError) as ei:
        await brain.handle_turn("loop forever")
    assert ei.value.code == "TOOL_LOOP_OVERRUN"


async def test_brain_protocol_error_when_tool_use_block_missing() -> None:
    """stop_reason=tool_use with no tool_use block raises PROTOCOL_ERROR."""
    client = _make_client([_response("tool_use", [_text_block("orphan text")])])
    brain = Brain(client, invoke_tool=AsyncMock())
    with pytest.raises(BrainError) as ei:
        await brain.handle_turn("hi")
    assert ei.value.code == "PROTOCOL_ERROR"


async def test_brain_uses_configured_model_and_system_prompt() -> None:
    """Brain forwards model + system_prompt to client.messages.create."""
    client = _make_client([_response("end_turn", [_text_block("hi")])])
    brain = Brain(
        client,
        invoke_tool=AsyncMock(),
        model="claude-sonnet-4-6",
        system_prompt="be brief",
    )
    await brain.handle_turn("yo")
    call_kwargs = client.messages.create.await_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6"
    assert call_kwargs["system"] == "be brief"
