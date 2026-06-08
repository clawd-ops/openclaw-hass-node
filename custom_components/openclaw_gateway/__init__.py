"""OpenClaw Gateway companion integration for Home Assistant.

The integration registers a conversation entity that forwards Assist turns to
OpenClaw Node's local add-on HTTP endpoint.
"""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .config_flow import _normalise_socket_url
from .const import CONF_SOCKET_URL

_LOG: Final[logging.Logger] = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.CONVERSATION]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries to the current schema version."""
    if entry.version < 2:
        old_url = str(entry.data.get(CONF_SOCKET_URL, ""))
        new_url = _normalise_socket_url(old_url)
        if old_url != new_url:
            _LOG.info("Migrating underscore hostname: %s -> %s", old_url, new_url)
            hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, CONF_SOCKET_URL: new_url},
                unique_id=new_url,
                version=2,
            )
        else:
            hass.config_entries.async_update_entry(entry, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up OpenClaw Gateway from a config entry.

    Args:
        hass: Home Assistant instance.
        entry: Stored config entry.

    Returns:
        True when setup succeeds.
    """
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload OpenClaw Gateway config entry.

    Args:
        hass: Home Assistant instance.
        entry: Stored config entry.

    Returns:
        True when all forwarded platforms unload successfully.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
