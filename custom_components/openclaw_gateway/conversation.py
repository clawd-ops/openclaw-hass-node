"""Conversation entity for OpenClaw Gateway."""

from __future__ import annotations

import logging
from typing import Any, Final

import aiohttp
from homeassistant.components import conversation
from homeassistant.components.conversation import (
    AssistantContent,
    ChatLog,
    ConversationEntity,
    ConversationEntityFeature,
    ConversationInput,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_API_TOKEN, CONF_SOCKET_URL, CONVERSATION_ENDPOINT, DOMAIN

_LOG: Final[logging.Logger] = logging.getLogger(__name__)
_REQUEST_TIMEOUT_S: Final[float] = 35.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the OpenClaw conversation entity.

    Args:
        hass: Home Assistant instance.
        entry: Config entry containing the add-on socket URL.
        async_add_entities: Callback used to register entities.
    """
    async_add_entities([OpenClawConversationEntity(hass, entry)])


class OpenClawConversationEntity(ConversationEntity):
    """Conversation agent that forwards turns to the OpenClaw Node add-on."""

    _attr_name = "OpenClaw Gateway"
    _attr_supported_features = ConversationEntityFeature.CONTROL

    @property
    def supported_languages(self) -> list[str] | str:
        """Return the languages the conversation agent supports.

        ``"*"`` is HA's MATCH_ALL sentinel — the agent accepts any
        language the user configures. ``ConversationEntity.supported_languages``
        is an abstract method, so it must be implemented as a property;
        ``_attr_supported_languages`` is NOT honoured by the abstract.
        """
        return "*"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the conversation entity.

        Args:
            hass: Home Assistant instance.
            entry: Config entry with add-on socket settings.
        """
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_conversation"

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device metadata for the integration.

        Returns:
            Home Assistant device registry metadata.
        """
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "OpenClaw Gateway",
            "manufacturer": "OpenClaw",
            "model": "Home Assistant Node",
        }

    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> conversation.ConversationResult:
        """Forward an Assist turn to the OpenClaw Node local API.

        Args:
            user_input: Conversation input from Home Assistant Assist.
            chat_log: Conversation chat log to append assistant content to.

        Returns:
            A Home Assistant conversation result with the node response.
        """
        socket_url = str(self._entry.data[CONF_SOCKET_URL]).rstrip("/")
        url = f"{socket_url}{CONVERSATION_ENDPOINT}"
        session = async_get_clientsession(self.hass)
        timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_S)
        headers: dict[str, str] = {}
        api_token = str(self._entry.data.get(CONF_API_TOKEN, "") or "")
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        try:
            async with session.post(
                url,
                json={
                    "text": user_input.text,
                    "conversation_id": user_input.conversation_id,
                    "language": user_input.language,
                },
                headers=headers,
                timeout=timeout,
            ) as response:
                # `response.ok` is `< 400` in aiohttp, which lets 3xx redirects
                # fall through to `.json()` and produce a confusing parse
                # error. Require a strict 2xx so any other status surfaces
                # cleanly to the user.
                if not 200 <= response.status < 300:
                    _LOG.warning("OpenClaw Node returned HTTP %s for %s", response.status, url)
                    speech = (
                        "OpenClaw Gateway is installed, but the OpenClaw Node add-on "
                        f"returned HTTP {response.status}."
                    )
                    chat_log.async_add_assistant_content_without_tools(
                        AssistantContent(agent_id=user_input.agent_id, content=speech)
                    )
                    intent_response = intent.IntentResponse(language=user_input.language)
                    intent_response.async_set_speech(speech)
                    return conversation.ConversationResult(
                        conversation_id=user_input.conversation_id,
                        response=intent_response,
                        continue_conversation=False,
                    )
                try:
                    data = await response.json()
                except aiohttp.ContentTypeError as exc:
                    _LOG.warning(
                        "OpenClaw Node responded with non-JSON content-type %r at %s: %s",
                        response.headers.get("Content-Type"),
                        url,
                        exc,
                    )
                    speech = "OpenClaw Node returned a non-JSON response."
                    chat_log.async_add_assistant_content_without_tools(
                        AssistantContent(agent_id=user_input.agent_id, content=speech)
                    )
                    intent_response = intent.IntentResponse(language=user_input.language)
                    intent_response.async_set_speech(speech)
                    return conversation.ConversationResult(
                        conversation_id=user_input.conversation_id,
                        response=intent_response,
                        continue_conversation=False,
                    )
            speech = str(
                data.get("response") or data.get("error") or "OpenClaw Node returned no response."
            )
        except TimeoutError:
            _LOG.warning("OpenClaw Node timed out at %s after %ss", url, _REQUEST_TIMEOUT_S)
            speech = (
                "OpenClaw Gateway is installed, but the OpenClaw Node add-on "
                f"did not respond within {int(_REQUEST_TIMEOUT_S)} seconds."
            )
        except aiohttp.ClientError as exc:
            _LOG.warning("OpenClaw Node network error at %s: %s", url, exc)
            speech = (
                "OpenClaw Gateway is installed, but the OpenClaw Node add-on "
                f"is not reachable at {socket_url}."
            )
        except (ValueError, TypeError) as exc:
            _LOG.warning("OpenClaw Node returned malformed JSON: %s", exc)
            speech = "OpenClaw Node returned an unexpected response shape."

        chat_log.async_add_assistant_content_without_tools(
            AssistantContent(agent_id=user_input.agent_id, content=speech)
        )
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(speech)
        return conversation.ConversationResult(
            conversation_id=user_input.conversation_id,
            response=response,
            continue_conversation=False,
        )
