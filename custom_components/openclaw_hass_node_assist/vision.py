"""``openclaw_hass_node_assist.describe_image`` service.

Describes a camera snapshot, a media-source item or a file in one line, using
the OpenClaw gateway's vision model. The image is downscaled here, sent to the
OpenClaw Node add-on, and the description comes back as the service response.

A description takes tens of seconds, so call the service from an automation
(or script) and use ``response_variable`` to receive the text.
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_API_TOKEN,
    CONF_DEFAULT_STYLE,
    CONF_SOCKET_URL,
    DEFAULT_STYLE,
    DESCRIBE_IMAGE_ENDPOINT,
    DESCRIBE_IMAGE_TIMEOUT_S,
    DOMAIN,
    IMAGE_JPEG_QUALITY,
    IMAGE_MAX_DIMENSION,
    IMAGE_MAX_SOURCE_BYTES,
    MAX_INSTRUCTIONS_CHARS,
    MAX_STYLE_CHARS,
    SERVICE_DESCRIBE_IMAGE,
)

_LOG = logging.getLogger(__name__)

ATTR_STYLE = "style"
ATTR_INSTRUCTIONS = "instructions"
ATTR_CAMERA = "camera_entity_id"
ATTR_MEDIA = "media_content_id"
ATTR_FILE = "file_path"
SOURCE_KEYS = (ATTR_CAMERA, ATTR_MEDIA, ATTR_FILE)

_CAMERA_TIMEOUT_S = 15

DESCRIBE_IMAGE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_STYLE): vol.All(cv.string, vol.Length(max=MAX_STYLE_CHARS)),
        vol.Optional(ATTR_INSTRUCTIONS): vol.All(cv.string, vol.Length(max=MAX_INSTRUCTIONS_CHARS)),
        vol.Optional(ATTR_CAMERA): cv.entity_domain("camera"),
        vol.Optional(ATTR_MEDIA): cv.string,
        vol.Optional(ATTR_FILE): cv.string,
    }
)


def downscale_to_jpeg(raw: bytes) -> bytes:
    """Return ``raw`` as a JPEG no larger than the maximum dimension.

    Runs in the executor: Pillow is synchronous and a snapshot can be several
    megapixels. EXIF orientation is applied, transparency is flattened, and
    smaller images are re-encoded without being enlarged.

    Args:
        raw: Image bytes in any format Pillow can open.

    Returns:
        JPEG bytes.

    Raises:
        HomeAssistantError: If the bytes are not a readable image.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(raw)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
        image.thumbnail((IMAGE_MAX_DIMENSION, IMAGE_MAX_DIMENSION), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=IMAGE_JPEG_QUALITY, optimize=True)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise HomeAssistantError("IMAGE_UNREADABLE: the source is not a readable image") from exc
    return out.getvalue()


def _read_local_file(path: str) -> bytes:
    """Read a bounded local file (executor)."""
    target = Path(path)
    if not target.is_file():
        raise ServiceValidationError(f"IMAGE_UNAVAILABLE: {path} is not a file")
    if target.stat().st_size > IMAGE_MAX_SOURCE_BYTES:
        raise ServiceValidationError("IMAGE_TOO_LARGE: the source file is too large")
    return target.read_bytes()


async def _load_media_source(hass: HomeAssistant, media_content_id: str) -> bytes:
    """Resolve a media-source id to image bytes."""
    from homeassistant.components.media_player import async_process_play_media_url
    from homeassistant.components.media_source import async_resolve_media

    media = await async_resolve_media(hass, media_content_id, None)
    local_path = getattr(media, "path", None)
    if local_path is not None:
        return await hass.async_add_executor_job(_read_local_file, str(local_path))

    url = async_process_play_media_url(hass, media.url, allow_relative_url=True)
    session = async_get_clientsession(hass)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                raise HomeAssistantError(f"IMAGE_UNAVAILABLE: media source answered {resp.status}")
            data = await resp.content.read(IMAGE_MAX_SOURCE_BYTES + 1)
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise HomeAssistantError("IMAGE_UNAVAILABLE: could not fetch the media source") from exc
    if len(data) > IMAGE_MAX_SOURCE_BYTES:
        raise ServiceValidationError("IMAGE_TOO_LARGE: the media item is too large")
    return data


async def _load_image(hass: HomeAssistant, key: str, value: str) -> bytes:
    """Return the raw bytes for the single image source the caller named."""
    if key == ATTR_CAMERA:
        from homeassistant.components import camera

        image = await camera.async_get_image(hass, value, timeout=_CAMERA_TIMEOUT_S)
        return bytes(image.content)
    if key == ATTR_MEDIA:
        return await _load_media_source(hass, value)
    if not hass.config.is_allowed_path(value):
        raise ServiceValidationError("IMAGE_PATH_NOT_ALLOWED: that path is not in an allowed folder")
    return await hass.async_add_executor_job(_read_local_file, value)


def _node_entry(hass: HomeAssistant) -> Any:
    """Return the configured OpenClaw Node entry that will serve the request."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("NODE_NOT_CONFIGURED: the OpenClaw Node entry is not loaded")
    return entries[0]


async def _post_describe(
    hass: HomeAssistant,
    entry: Any,
    jpeg: bytes,
    *,
    style: str,
    instructions: str | None,
    source: str,
) -> str:
    """Send the image to the add-on and return the description."""
    base_url = str(entry.data[CONF_SOCKET_URL]).rstrip("/")
    token = str(entry.data.get(CONF_API_TOKEN, "") or "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    payload: dict[str, Any] = {
        "image_base64": base64.b64encode(jpeg).decode("ascii"),
        "mime": "image/jpeg",
        "style": style,
        "source": source,
    }
    if instructions:
        payload["instructions"] = instructions

    session = async_get_clientsession(hass)
    try:
        async with session.post(
            base_url + DESCRIBE_IMAGE_ENDPOINT,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=DESCRIBE_IMAGE_TIMEOUT_S),
        ) as resp:
            status = resp.status
            try:
                body = await resp.json(content_type=None)
            except (aiohttp.ContentTypeError, ValueError):
                body = {}
    except TimeoutError as exc:
        raise HomeAssistantError("TIMEOUT: the OpenClaw Node did not answer in time") from exc
    except aiohttp.ClientError as exc:
        raise HomeAssistantError("NODE_UNREACHABLE: could not reach the OpenClaw Node") from exc

    if not isinstance(body, dict):
        body = {}
    text = body.get("text")
    if status == 200 and body.get("ok") and isinstance(text, str) and text.strip():
        return text.strip()
    code = str(body.get("error") or f"HTTP_{status}")
    message = body.get("message")
    _LOG.warning("describe_image failed: %s (HTTP %s)", code, status)
    raise HomeAssistantError(f"{code}: {message}" if isinstance(message, str) and message else code)


async def _async_describe_image(call: ServiceCall) -> ServiceResponse:
    """Handle ``openclaw_hass_node_assist.describe_image``."""
    hass = call.hass
    data = call.data
    named = [key for key in SOURCE_KEYS if data.get(key)]
    if len(named) != 1:
        raise ServiceValidationError(
            "Provide exactly one of camera_entity_id, media_content_id or file_path."
        )
    key = named[0]
    source = str(data[key])

    entry = _node_entry(hass)
    style = str(data.get(ATTR_STYLE) or entry.options.get(CONF_DEFAULT_STYLE) or DEFAULT_STYLE)

    raw = await _load_image(hass, key, source)
    jpeg = await hass.async_add_executor_job(downscale_to_jpeg, raw)
    text = await _post_describe(
        hass,
        entry,
        jpeg,
        style=style,
        instructions=data.get(ATTR_INSTRUCTIONS),
        source=source,
    )
    return {"text": text}


def async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's services."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_DESCRIBE_IMAGE,
        _async_describe_image,
        schema=DESCRIBE_IMAGE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
