"""Config flow for the OpenClaw Gateway companion integration."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant import config_entries

from .const import CONF_SOCKET_URL, DEFAULT_SOCKET_URL, DOMAIN

# Supervisor add-on hostnames look like '<hash>_<slug>' or '<hash>-<slug>'.
# Extract the human-readable slug so the entry title is recognisable
# regardless of which install hash HA generated.
_SLUG_RE = re.compile(r"^[0-9a-f]+[_-](?P<slug>[a-z0-9_-]+)$", re.IGNORECASE)


def _entry_title(socket_url: str) -> str:
    """Derive a friendly entry title from the add-on socket URL.

    Examples:
        ``http://fcccfbbd_openclaw_hass_node:8099`` -> ``OpenClaw Node (openclaw_hass_node)``
        ``http://10.0.10.20:8099``                  -> ``OpenClaw Node (10.0.10.20)``
    """
    try:
        host = urlparse(socket_url).hostname or socket_url
    except ValueError:
        host = socket_url
    match = _SLUG_RE.match(host)
    label = match.group("slug") if match else host
    return f"OpenClaw Node ({label})"


class OpenClawGatewayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for OpenClaw Gateway.

    The flow asks for the local add-on socket URL. The default points at the
    add-on hostname and port used by this repository.
    """

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial config flow step.

        Args:
            user_input: Values submitted by the user, or None while showing
                the form.

        Returns:
            A Home Assistant config flow result.
        """
        if user_input is not None:
            socket_url = str(user_input[CONF_SOCKET_URL]).rstrip("/")
            await self.async_set_unique_id(socket_url)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=_entry_title(socket_url),
                data={CONF_SOCKET_URL: socket_url},
            )

        schema = vol.Schema({vol.Required(CONF_SOCKET_URL, default=DEFAULT_SOCKET_URL): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors={})

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle reconfiguration of an existing entry.

        Lets the user change the add-on socket URL without removing and
        re-adding the integration.
        """
        entry = self._get_reconfigure_entry()
        current = str(entry.data.get(CONF_SOCKET_URL, DEFAULT_SOCKET_URL))

        if user_input is not None:
            socket_url = str(user_input[CONF_SOCKET_URL]).rstrip("/")
            # If unchanged, just update + reload without touching unique_id.
            # If changed, the new URL must not collide with another entry's
            # unique_id. We can't use _abort_if_unique_id_mismatch here
            # because that's for entries whose unique_id must NOT change;
            # the socket URL is user-editable by design.
            if socket_url != entry.unique_id:
                for other in self._async_current_entries(include_ignore=False):
                    if other.entry_id != entry.entry_id and other.unique_id == socket_url:
                        return self.async_abort(reason="already_configured")
                self.hass.config_entries.async_update_entry(entry, unique_id=socket_url)
            return self.async_update_reload_and_abort(
                entry,
                title=_entry_title(socket_url),
                data={CONF_SOCKET_URL: socket_url},
            )

        schema = vol.Schema({vol.Required(CONF_SOCKET_URL, default=current): str})
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors={})
