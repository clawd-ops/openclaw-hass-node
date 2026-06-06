"""Tests for openclaw_gateway.providers_openai.OpenAIProvider."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from openclaw_gateway.brain import BrainError
from openclaw_gateway.providers import ToolResult
from openclaw_gateway.providers_openai import OpenAIProvider


def _tool_call(*, id: str, name: str, arguments: str) -> MagicMock:
    fn = MagicMock()
    fn.name = name
    fn.arguments = arguments
    tc = MagicMock()
    tc.id = id
    tc.function = fn
    return tc


def _response(*, content: str | None, tool_calls: list[Any] | None, finish: str) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = finish
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _client(responses: list[Any]) -> Any:
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=responses)
    return client


async def test_openai_run_round_text() -> None:
    prov = OpenAIProvider(_client([_response(content="hi", tool_calls=None, finish="stop")]))
    rd = await prov.run_round([], [], system_prompt="", max_tokens=100)
    assert rd.text == "hi"
    assert rd.tool_uses == []


async def test_openai_run_round_tool_use_parses_arguments() -> None:
    tc = _tool_call(id="call_1", name="ha.list_states", arguments='{"domain": "light"}')
    prov = OpenAIProvider(_client([_response(content=None, tool_calls=[tc], finish="tool_calls")]))
    rd = await prov.run_round([], [], system_prompt="", max_tokens=100)
    assert rd.text == ""
    assert len(rd.tool_uses) == 1
    assert rd.tool_uses[0].id == "call_1"
    assert rd.tool_uses[0].input == {"domain": "light"}


async def test_openai_run_round_tool_use_with_malformed_args_falls_back_to_empty() -> None:
    tc = _tool_call(id="call_1", name="ha.x", arguments="not json")
    prov = OpenAIProvider(_client([_response(content=None, tool_calls=[tc], finish="tool_calls")]))
    rd = await prov.run_round([], [], system_prompt="", max_tokens=100)
    assert rd.tool_uses[0].input == {}


async def test_openai_protocol_error_when_finish_says_tool_calls_but_none_present() -> None:
    prov = OpenAIProvider(_client([_response(content=None, tool_calls=[], finish="tool_calls")]))
    with pytest.raises(BrainError) as ei:
        await prov.run_round([], [], system_prompt="", max_tokens=100)
    assert ei.value.code == "PROTOCOL_ERROR"


async def test_openai_system_prompt_prepended_once() -> None:
    captured: list[list[dict[str, Any]]] = []

    async def _create(**kwargs: Any) -> Any:
        captured.append(list(kwargs["messages"]))
        return _response(content="ok", tool_calls=None, finish="stop")

    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=_create)

    prov = OpenAIProvider(client)
    await prov.run_round(
        [{"role": "user", "content": "hi"}], [], system_prompt="be brief", max_tokens=100
    )
    assert captured[0][0]["role"] == "system"
    assert captured[0][0]["content"] == "be brief"


def test_openai_translate_tools_to_function_shape() -> None:
    tools = [
        {
            "name": "ha.list_states",
            "description": "list",
            "input_schema": {"type": "object", "properties": {"domain": {"type": "string"}}},
        }
    ]
    out = OpenAIProvider(_client([])).translate_tools(tools)
    assert out[0]["type"] == "function"
    assert out[0]["function"]["name"] == "ha.list_states"
    assert out[0]["function"]["parameters"] == tools[0]["input_schema"]


def test_openai_append_tool_results_adds_assistant_and_tool_messages() -> None:
    tc = _tool_call(id="call_1", name="ha.x", arguments="{}")
    assistant = MagicMock()
    assistant.content = None
    assistant.tool_calls = [tc]
    msgs: list[Any] = []
    msgs = OpenAIProvider(_client([])).append_tool_results(
        msgs,
        assistant,
        [ToolResult(tool_use_id="call_1", content='{"ok":true}', is_error=False)],
    )
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["tool_calls"][0]["id"] == "call_1"
    assert msgs[1]["role"] == "tool"
    assert msgs[1]["tool_call_id"] == "call_1"
