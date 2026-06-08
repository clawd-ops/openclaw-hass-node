"""OpenClaw Gateway companion integration for Home Assistant.

The integration registers a conversation entity that forwards Assist turns to
OpenClaw Node's local add-on HTTP endpoint.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

PLATFORMS: list[Platform] = [Platform.CONVERSATION]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up OpenClaw Gateway from a config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload OpenClaw Gateway config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
