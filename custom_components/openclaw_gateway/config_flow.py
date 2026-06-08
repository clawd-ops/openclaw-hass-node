"""Config flow for the OpenClaw Gateway companion integration."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .const import CONF_API_TOKEN, CONF_SOCKET_URL, DEFAULT_SOCKET_URL, DOMAIN

# Supervisor add-on hostnames look like '<hash>_<slug>' or '<hash>-<slug>'.
# Extract the human-readable slug so the entry title is recognisable
# regardless of which install hash HA generated.
_SLUG_RE = re.compile(r"^[0-9a-f]+[_-](?P<slug>[a-z0-9_-]+)$", re.IGNORECASE)


def _entry_title(socket_url: str) -> str:
    """Derive a friendly entry title from the add-on socket URL.

    Examples:
        ``http://fcccfbbd-openclaw-hass-node:8099`` -> ``OpenClaw Node (openclaw-hass-node)``
        ``http://10.0.10.20:8099``                  -> ``OpenClaw Node (10.0.10.20)``
    """
    try:
        host = urlparse(socket_url).hostname or socket_url
    except ValueError:
        host = socket_url
    match = _SLUG_RE.match(host)
    label = match.group("slug") if match else host
    return f"OpenClaw Node ({label})"


def _normalise_socket_url(raw: str) -> str:
    """Normalise the socket URL: strip trailing slash + fix underscore hostnames.

    HA Supervisor publishes add-on hostnames with dashes
    (``<hash>-openclaw-hass-node``). The underscore form
    (``<hash>_openclaw_hass_node``) is the Docker container name and is
    NOT DNS-resolvable from HA Core's container, so an integration that
    receives the underscore form will fail with a DNS timeout (see #76).

    This helper rewrites the host part from underscores to dashes when
    the URL looks like a Supervisor add-on hostname (``<hex>_<slug>``).
    IP literals and explicit-dash hostnames pass through unchanged.
    """
    url = raw.strip().rstrip("/")
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    host = parsed.hostname or ""
    if not host or "_" not in host:
        return url
    if not _SLUG_RE.match(host):
        # Not a Supervisor add-on hostname — leave it alone so we don't
        # mangle URLs the user deliberately set.
        return url
    new_host = host.replace("_", "-")
    netloc = new_host
    if parsed.port is not None:
        netloc = f"{new_host}:{parsed.port}"
    if parsed.username or parsed.password:
        userpass = parsed.username or ""
        if parsed.password:
            userpass = f"{userpass}:{parsed.password}"
        netloc = f"{userpass}@{netloc}"
    return urlunparse(parsed._replace(netloc=netloc))


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
            socket_url = _normalise_socket_url(str(user_input[CONF_SOCKET_URL]))
            api_token = str(user_input.get(CONF_API_TOKEN, "") or "")
            await self.async_set_unique_id(socket_url)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=_entry_title(socket_url),
                data={CONF_SOCKET_URL: socket_url, CONF_API_TOKEN: api_token},
            )

        _pw = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
        schema = vol.Schema(
            {
                vol.Required(CONF_SOCKET_URL, default=DEFAULT_SOCKET_URL): str,
                vol.Optional(CONF_API_TOKEN, default=""): _pw,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors={})

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle reconfiguration of an existing entry.

        Lets the user change the add-on socket URL and/or API token without
        removing and re-adding the integration.
        """
        entry = self._get_reconfigure_entry()
        current_url = str(entry.data.get(CONF_SOCKET_URL, DEFAULT_SOCKET_URL))

        if user_input is not None:
            socket_url = _normalise_socket_url(str(user_input[CONF_SOCKET_URL]))
            submitted_token = str(user_input.get(CONF_API_TOKEN, "") or "")
            url_changed = socket_url != _normalise_socket_url(current_url)
            if submitted_token:
                api_token = submitted_token
            elif url_changed:
                api_token = ""
            else:
                api_token = str(entry.data.get(CONF_API_TOKEN, "") or "")
            # If URL is unchanged, skip unique_id update. If changed, the
            # new URL must not collide with another entry's unique_id.
            # _abort_if_unique_id_mismatch can't be used because the URL
            # is user-editable by design.
            if socket_url != entry.unique_id:
                for other in self._async_current_entries(include_ignore=False):
                    if other.entry_id != entry.entry_id and other.unique_id == socket_url:
                        return self.async_abort(reason="already_configured")
                self.hass.config_entries.async_update_entry(entry, unique_id=socket_url)
            return self.async_update_reload_and_abort(
                entry,
                title=_entry_title(socket_url),
                data={CONF_SOCKET_URL: socket_url, CONF_API_TOKEN: api_token},
            )

        _pw = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
        schema = vol.Schema(
            {
                vol.Required(CONF_SOCKET_URL, default=current_url): str,
                vol.Optional(CONF_API_TOKEN, default=""): _pw,
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors={})
