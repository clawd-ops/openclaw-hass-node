"""Integration-level regression: Assist speaks the node's curated stream remedy (#348).

The node's ``/v1/conversation/stream`` error frame carries ``{"error": code,
"message": remedy}``. The HA integration used to keep only the code and speak
``OpenClaw Node stream error: <code>``. These tests drive the real
``OpenClawConversationEntity._async_handle_message`` against a fake node
stream. Home Assistant is not a dependency of this repo, so the few HA names
the module imports are stubbed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

_PKG_DIR = Path(__file__).resolve().parents[3] / "custom_components" / "openclaw_hass_node_assist"
_PKG = "oc_assist_under_test"


class _Recorder:
    """Stand-in for an HA object that just records what it was given."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)
        self.speech = ""

    def async_set_speech(self, speech: str) -> None:
        self.speech = speech


def _module(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    return mod


@pytest.fixture
def conversation_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    ha_conversation = _module(
        "homeassistant.components.conversation",
        AssistantContent=_Recorder,
        ChatLog=object,
        ConversationEntity=object,
        ConversationEntityFeature=types.SimpleNamespace(CONTROL=1),
        ConversationInput=object,
        ConversationResult=_Recorder,
    )
    stubs = {
        "homeassistant": _module("homeassistant"),
        "homeassistant.components": _module("homeassistant.components"),
        "homeassistant.components.conversation": ha_conversation,
        "homeassistant.config_entries": _module("homeassistant.config_entries", ConfigEntry=object),
        "homeassistant.core": _module("homeassistant.core", HomeAssistant=object),
        "homeassistant.helpers": _module("homeassistant.helpers"),
        "homeassistant.helpers.intent": _module(
            "homeassistant.helpers.intent", IntentResponse=_Recorder
        ),
        "homeassistant.helpers.aiohttp_client": _module(
            "homeassistant.helpers.aiohttp_client",
            async_get_clientsession=lambda _hass: None,
        ),
        "homeassistant.helpers.entity_platform": _module(
            "homeassistant.helpers.entity_platform", AddEntitiesCallback=object
        ),
    }
    stubs["homeassistant.components"].conversation = ha_conversation  # type: ignore[attr-defined]
    stubs["homeassistant.helpers"].intent = stubs["homeassistant.helpers.intent"]  # type: ignore[attr-defined]
    for name, mod in stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)

    monkeypatch.setitem(sys.modules, _PKG, _module(_PKG, __path__=[str(_PKG_DIR)]))
    loaded: dict[str, types.ModuleType] = {}
    for leaf in ("const", "conversation"):
        spec = importlib.util.spec_from_file_location(f"{_PKG}.{leaf}", _PKG_DIR / f"{leaf}.py")
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, f"{_PKG}.{leaf}", mod)
        spec.loader.exec_module(mod)
        loaded[leaf] = mod
    return loaded["conversation"]


class _FakeResponse:
    status = 200

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self._lines = [json.dumps(f).encode("utf-8") + b"\n" for f in frames]

    @property
    def content(self) -> AsyncIterator[bytes]:
        async def _iter() -> AsyncIterator[bytes]:
            for line in self._lines:
                yield line

        return _iter()

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _FakeSession:
    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self._frames = frames

    def post(self, *_args: Any, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(self._frames)


class _FakeChatLog:
    def __init__(self) -> None:
        self.assistant_content: list[Any] = []

    async def async_add_delta_content_stream(
        self, _agent_id: str, stream: AsyncIterator[dict[str, Any]]
    ) -> AsyncIterator[dict[str, Any]]:
        async for delta in stream:
            yield delta

    def async_add_assistant_content_without_tools(self, content: Any) -> None:
        self.assistant_content.append(content)


async def _speak(
    module: types.ModuleType, monkeypatch: pytest.MonkeyPatch, frames: list[dict[str, Any]]
) -> tuple[str, _FakeChatLog]:
    monkeypatch.setattr(module, "async_get_clientsession", lambda _hass: _FakeSession(frames))
    entry = types.SimpleNamespace(
        entry_id="e1", data={module.CONF_SOCKET_URL: "http://node.test", module.CONF_API_TOKEN: ""}
    )
    entity = module.OpenClawConversationEntity(object(), entry)
    user_input = types.SimpleNamespace(
        text="turn off the light",
        conversation_id="c1",
        language="en",
        agent_id="agent.test",
        context=types.SimpleNamespace(user_id=None),
    )
    chat_log = _FakeChatLog()
    result = await entity._async_handle_message(user_input, chat_log)
    return result.response.speech, chat_log


_REMEDY = (
    "No agent is configured to answer. An administrator needs to set "
    "identity.default_agent_id in the OpenClaw add-on configuration."
)


async def test_error_frame_remedy_is_spoken(
    conversation_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    speech, chat_log = await _speak(
        conversation_module,
        monkeypatch,
        [{"error": "INVALID_REQUEST", "message": _REMEDY}],
    )
    assert speech == _REMEDY
    assert [c.content for c in chat_log.assistant_content] == [_REMEDY]


async def test_error_frame_without_message_falls_back_to_code(
    conversation_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    speech, _ = await _speak(conversation_module, monkeypatch, [{"error": "INTERNAL_ERROR"}])
    assert speech == "OpenClaw Node stream error: INTERNAL_ERROR"
