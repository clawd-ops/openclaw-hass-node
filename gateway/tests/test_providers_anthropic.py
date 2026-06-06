"""Tests for openclaw_gateway.providers_anthropic.AnthropicProvider."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from openclaw_gateway.brain import BrainError
from openclaw_gateway.providers import ToolResult
from openclaw_gateway.providers_anthropic import AnthropicProvider


def _block(t: str, **attrs: Any) -> MagicMock:
    blk = MagicMock()
    blk.type = t
    for k, v in attrs.items():
        setattr(blk, k, v)
    return blk


def _response(stop_reason: str, content: list[MagicMock]) -> MagicMock:
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.content = content
    return resp


def _client(responses: list[Any]) -> Any:
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(side_effect=responses)
    return client


async def test_anthropic_run_round_text() -> None:
    prov = AnthropicProvider(_client([_response("end_turn", [_block("text", text="hi")])]))
    rd = await prov.run_round([], [], system_prompt="", max_tokens=100)
    assert rd.text == "hi"
    assert rd.tool_uses == []


async def test_anthropic_run_round_tool_use() -> None:
    use = _block(
        "tool_use",
        id="t1",
        name="ha.list_states",
        input={"domain": "light"},
    )
    prov = AnthropicProvider(_client([_response("tool_use", [use])]))
    rd = await prov.run_round([], [], system_prompt="", max_tokens=100)
    assert rd.text == ""
    assert len(rd.tool_uses) == 1
    assert rd.tool_uses[0].name == "ha.list_states"
    assert rd.tool_uses[0].input == {"domain": "light"}


async def test_anthropic_protocol_error_on_orphan_tool_use_stop() -> None:
    prov = AnthropicProvider(_client([_response("tool_use", [_block("text", text="oops")])]))
    with pytest.raises(BrainError) as ei:
        await prov.run_round([], [], system_prompt="", max_tokens=100)
    assert ei.value.code == "PROTOCOL_ERROR"


def test_anthropic_translate_tools_passthrough() -> None:
    tools = [{"name": "ha.x", "description": "d", "input_schema": {"type": "object"}}]
    out = AnthropicProvider(_client([])).translate_tools(tools)
    assert out is tools


def test_anthropic_append_tool_results_adds_assistant_and_user() -> None:
    prov = AnthropicProvider(_client([]))
    msgs: list[Any] = []
    msgs = prov.append_tool_results(
        msgs,
        "assistant-block",
        [ToolResult(tool_use_id="t1", content='{"ok":true}', is_error=False)],
    )
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["content"] == "assistant-block"
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"][0]["tool_use_id"] == "t1"
    assert msgs[1]["content"][0]["is_error"] is False
