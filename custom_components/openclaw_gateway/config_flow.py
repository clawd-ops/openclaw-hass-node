"""Config flow for the OpenClaw Gateway companion integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries

from .const import CONF_SOCKET_URL, DEFAULT_SOCKET_URL, DOMAIN


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
                title="OpenClaw Gateway",
                data={CONF_SOCKET_URL: socket_url},
            )

        schema = vol.Schema({vol.Required(CONF_SOCKET_URL, default=DEFAULT_SOCKET_URL): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors={})
