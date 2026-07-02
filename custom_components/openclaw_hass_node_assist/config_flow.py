"""Config flow for the OpenClaw HA Node — Assist companion integration."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .const import (
    BOOTSTRAP_ENDPOINT,
    CONF_API_TOKEN,
    CONF_SOCKET_URL,
    CONVERSATION_INFO_ENDPOINT,
    DEFAULT_SOCKET_URL,
    DOMAIN,
    NODE_LOCAL_PORT,
    PROBE_CANDIDATE_HOSTS,
    PROBE_TIMEOUT_S,
)

_LOG = logging.getLogger(__name__)


async def _probe_one(session: aiohttp.ClientSession, url: str) -> bool:
    """Return True when ``GET {url}/v1/conversation/info`` answers 200.

    Uses the per-request short timeout; never raises on network errors.
    The probe endpoint is in the unauthed allowlist on the node side so a
    fresh install with no token configured still answers.
    """
    try:
        async with session.get(
            url + CONVERSATION_INFO_ENDPOINT,
            timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT_S),
        ) as resp:
            return resp.status == 200
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        _LOG.debug("probe %s failed: %s", url, exc)
        return False


async def _supervisor_addon_url(hass: Any) -> str | None:
    """Ask HA Supervisor for our addon's hostname (most reliable path).

    When running under HA Supervisor, the `hassio` HA integration knows
    every installed addon's real hostname (the install-specific
    ``<repo-uuid>-<slug>`` form). Returns ``http://<hostname>:8099`` if
    the openclaw_hass_node addon is installed and Supervisor reports a
    hostname, otherwise None.

    Falls back gracefully on every error path — the caller already has
    HTTP probes + the hard-coded default to try after this.
    """
    try:
        from homeassistant.components.hassio import (  # type: ignore[import-not-found]
            get_addons_info,
        )
        from homeassistant.helpers.hassio import (  # type: ignore[import-not-found]
            is_hassio,
        )
    except ImportError:
        return None
    try:
        if not is_hassio(hass):
            return None
        addons = get_addons_info(hass) or {}
    except Exception as exc:
        _LOG.debug("supervisor addon enumeration failed: %s", exc)
        return None
    for slug, info in addons.items():
        # Supervisor slugs look like '<repo-uuid>_openclaw_hass_node'.
        # Exact or suffix match only — substring matching would catch
        # unrelated addons like '<x>_openclaw_hass_node_proxy'.
        if not isinstance(slug, str):
            continue
        if slug != "openclaw_hass_node" and not slug.endswith("_openclaw_hass_node"):
            continue
        if not isinstance(info, dict):
            continue
        hostname = info.get("hostname")
        if isinstance(hostname, str) and hostname:
            url = f"http://{hostname}:{NODE_LOCAL_PORT}"
            _LOG.info("openclaw node hostname from Supervisor: %s", url)
            return url
    return None


async def _fetch_bootstrap_token(session: aiohttp.ClientSession, url: str) -> str | None:
    """Attempt to retrieve an auto-generated API token from the node bootstrap endpoint.

    Returns the token string if the node reports one, or None if bootstrap is
    disabled (operator set the token explicitly) or the request fails for any
    reason. Failures are non-fatal — the config flow falls back to manual entry.
    """
    try:
        async with session.get(
            url + BOOTSTRAP_ENDPOINT,
            timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT_S),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            if not isinstance(data, dict) or not data.get("ok"):
                return None
            token = data.get("token")
            return str(token) if isinstance(token, str) and token else None
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        _LOG.debug("bootstrap token fetch from %s failed: %s", url, exc)
        return None


async def _probe_default_socket_url(
    session: aiohttp.ClientSession,
    hass: Any = None,
) -> str:
    """Discover the OpenClaw Node API URL.

    Two-stage discovery:

    1. **Supervisor API** — if we're running under HA Supervisor, ask
       it directly for the addon's hostname. This is install-specific
       (varies by repo-uuid hash) but Supervisor knows it. Most
       reliable path when available.
    2. **HTTP probes** — fall back to concurrent ``GET
       /v1/conversation/info`` probes against a list of candidate
       hostnames. Worst-case stall is bounded by a single
       ``PROBE_TIMEOUT_S`` window (~1s).

    Returns the first reachable URL, or :data:`DEFAULT_SOCKET_URL` if
    neither path succeeds (#88/1).
    """
    if hass is not None:
        supervisor_url = await _supervisor_addon_url(hass)
        if supervisor_url is not None and await _probe_one(session, supervisor_url):
            return supervisor_url
    urls = [f"http://{host}:{NODE_LOCAL_PORT}" for host in PROBE_CANDIDATE_HOSTS]
    results = await asyncio.gather(
        *(_probe_one(session, url) for url in urls), return_exceptions=False
    )
    for url, ok in zip(urls, results, strict=True):
        if ok:
            _LOG.info("openclaw node detected at %s", url)
            return url
    return DEFAULT_SOCKET_URL


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
    """Handle a config flow for OpenClaw HA Node — Assist.

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
                data={
                    CONF_SOCKET_URL: socket_url,
                    CONF_API_TOKEN: api_token,
                },
            )

        session = aiohttp_client.async_get_clientsession(self.hass)
        suggested_url = await _probe_default_socket_url(session, self.hass)
        suggested_token = await _fetch_bootstrap_token(session, suggested_url) or ""
        _pw = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
        schema = vol.Schema(
            {
                vol.Required(CONF_SOCKET_URL, default=suggested_url): str,
                vol.Optional(CONF_API_TOKEN, default=suggested_token): _pw,
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
                data={
                    CONF_SOCKET_URL: socket_url,
                    CONF_API_TOKEN: api_token,
                },
            )

        _pw = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
        schema = vol.Schema(
            {
                vol.Required(CONF_SOCKET_URL, default=current_url): str,
                vol.Optional(CONF_API_TOKEN, default=""): _pw,
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors={})
