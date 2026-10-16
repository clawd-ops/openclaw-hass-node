"""Tests for the ``openclaw_hass_node_assist.describe_image`` HA service.

Home Assistant is not a dependency of this repo, so the few HA names the
integration imports are stubbed (the same approach as
``test_assist_stream_error_speech.py``). Pillow and voluptuous are real, so
downscaling and schema validation are exercised for real.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiohttp
import pytest
import voluptuous as vol
import yaml
from PIL import Image

_PKG_DIR = Path(__file__).resolve().parents[3] / "custom_components" / "openclaw_hass_node_assist"
_PKG = "oc_assist_service_under_test"


class _HomeAssistantError(Exception):
    pass


class _ServiceValidationError(_HomeAssistantError):
    pass


def _module(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    return mod


class _ConfigFlow:
    def __init_subclass__(cls, domain: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)


class _OptionsFlow:
    config_entry: Any = None

    def async_create_entry(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "create_entry", **kwargs}

    def async_show_form(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "form", **kwargs}


class _Selector:
    def __init__(self, config: Any = None) -> None:
        self.config = config

    def __call__(self, value: Any) -> Any:
        return value


def _selector_config(**kwargs: Any) -> types.SimpleNamespace:
    return types.SimpleNamespace(**kwargs)


@pytest.fixture
def mods(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    camera = _module("homeassistant.components.camera")
    media_source = _module("homeassistant.components.media_source")
    media_player = _module("homeassistant.components.media_player")
    components = _module("homeassistant.components", camera=camera)
    helpers = _module("homeassistant.helpers")
    aiohttp_client = _module(
        "homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda _h: None
    )
    helpers.aiohttp_client = aiohttp_client  # type: ignore[attr-defined]

    def entity_domain(domain: str) -> Callable[[Any], str]:
        def _validate(value: Any) -> str:
            text = str(value)
            if not text.startswith(f"{domain}."):
                raise vol.Invalid(f"expected a {domain} entity")
            return text

        return _validate

    stubs = {
        "homeassistant": _module("homeassistant"),
        "homeassistant.components": components,
        "homeassistant.components.camera": camera,
        "homeassistant.components.media_source": media_source,
        "homeassistant.components.media_player": media_player,
        "homeassistant.const": _module(
            "homeassistant.const", Platform=types.SimpleNamespace(CONVERSATION="conversation")
        ),
        "homeassistant.config_entries": _module(
            "homeassistant.config_entries",
            ConfigEntry=object,
            ConfigFlow=_ConfigFlow,
            ConfigFlowResult=dict,
            OptionsFlow=_OptionsFlow,
        ),
        "homeassistant.core": _module(
            "homeassistant.core",
            HomeAssistant=object,
            ServiceCall=object,
            ServiceResponse=dict,
            SupportsResponse=types.SimpleNamespace(ONLY="only"),
            callback=lambda fn: fn,
        ),
        "homeassistant.exceptions": _module(
            "homeassistant.exceptions",
            HomeAssistantError=_HomeAssistantError,
            ServiceValidationError=_ServiceValidationError,
        ),
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.config_validation": _module(
            "homeassistant.helpers.config_validation", string=str, entity_domain=entity_domain
        ),
        "homeassistant.helpers.aiohttp_client": aiohttp_client,
        "homeassistant.helpers.typing": _module("homeassistant.helpers.typing", ConfigType=dict),
        "homeassistant.helpers.selector": _module(
            "homeassistant.helpers.selector",
            SelectSelector=_Selector,
            SelectSelectorConfig=_selector_config,
            SelectSelectorMode=types.SimpleNamespace(DROPDOWN="dropdown"),
            TextSelector=_Selector,
            TextSelectorConfig=_selector_config,
            TextSelectorType=types.SimpleNamespace(PASSWORD="password"),
        ),
    }
    helpers.config_validation = stubs["homeassistant.helpers.config_validation"]  # type: ignore[attr-defined]
    for name, mod in stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)

    monkeypatch.setitem(sys.modules, _PKG, _module(_PKG, __path__=[str(_PKG_DIR)]))
    loaded: dict[str, types.ModuleType] = {}
    for leaf in ("const", "vision", "config_flow", "__init__"):
        name = _PKG if leaf == "__init__" else f"{_PKG}.{leaf}"
        spec = importlib.util.spec_from_file_location(
            name,
            _PKG_DIR / f"{leaf}.py",
            submodule_search_locations=[str(_PKG_DIR)] if leaf == "__init__" else None,
        )
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, mod)
        spec.loader.exec_module(mod)
        loaded[leaf] = mod
    return types.SimpleNamespace(
        camera=camera,
        media_source=media_source,
        media_player=media_player,
        aiohttp_client=aiohttp_client,
        package=loaded["__init__"],
        const=loaded["const"],
        vision=loaded["vision"],
        config_flow=loaded["config_flow"],
    )


# --- fakes -------------------------------------------------------------


class _Resp:
    def __init__(self, status: int, body: Any, *, raises: Exception | None = None) -> None:
        self.status = status
        self._body = body
        self._raises = raises
        self.content = types.SimpleNamespace(read=self._read)

    async def _read(self, _n: int) -> bytes:
        return self._body  # type: ignore[no-any-return]

    async def json(self, **_kwargs: Any) -> Any:
        if self._raises is not None:
            raise self._raises
        return self._body

    async def __aenter__(self) -> _Resp:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _Session:
    def __init__(self, resp: _Resp | Exception) -> None:
        self._resp = resp
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _Resp:
        self.posts.append({"url": url, **kwargs})
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp

    def get(self, url: str, **_kwargs: Any) -> _Resp:
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp


class _Hass:
    def __init__(self, allowed: Path, entries: list[Any]) -> None:
        self._allowed = str(allowed)
        self.config = types.SimpleNamespace(is_allowed_path=lambda p: p.startswith(self._allowed))
        self.registered: list[dict[str, Any]] = []
        self.services = types.SimpleNamespace(async_register=self._register)
        self.config_entries = types.SimpleNamespace(async_loaded_entries=lambda _d: entries)

    def _register(self, domain: str, service: str, func: Any, **kwargs: Any) -> None:
        self.registered.append({"domain": domain, "service": service, "func": func, **kwargs})

    async def async_add_executor_job(self, fn: Callable[..., Any], *args: Any) -> Any:
        return fn(*args)


def _entry(options: dict[str, Any] | None = None, token: str = "tok") -> types.SimpleNamespace:
    return types.SimpleNamespace(
        entry_id="e1",
        data={"socket_url": "http://node.test:8099/", "local_api_token": token},
        options=options or {},
    )


def _png(size: tuple[int, int] = (400, 300), mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, "red").save(buf, format="PNG")
    return buf.getvalue()


def _call(hass: Any, **data: Any) -> Any:
    return types.SimpleNamespace(hass=hass, data=data)


OK_BODY = {"ok": True, "text": " A courier at the door. "}


@pytest.fixture
def setup(
    mods: types.SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., tuple[_Hass, _Session]]:
    def _setup(
        resp: _Resp | Exception | None = None,
        *,
        entry: Any = None,
        entries: list[Any] | None = None,
    ) -> tuple[_Hass, _Session]:
        hass = _Hass(tmp_path, [entry or _entry()] if entries is None else entries)
        session = _Session(resp if resp is not None else _Resp(200, OK_BODY))
        monkeypatch.setattr(mods.vision, "async_get_clientsession", lambda _h: session)
        return hass, session

    return _setup


# --- registration ------------------------------------------------------


def test_service_is_registered_with_a_response(mods: types.SimpleNamespace) -> None:
    hass = _Hass(Path("/nonexistent"), [])
    assert asyncio.run(mods.package.async_setup(hass, {})) is True
    [reg] = hass.registered
    assert reg["domain"] == "openclaw_hass_node_assist"
    assert reg["service"] == "describe_image"
    assert reg["supports_response"] == "only"
    assert reg["schema"] is mods.vision.DESCRIBE_IMAGE_SCHEMA


def test_services_yaml_strings_and_schema_agree(mods: types.SimpleNamespace) -> None:
    services = yaml.safe_load((_PKG_DIR / "services.yaml").read_text())
    strings = json.loads((_PKG_DIR / "strings.json").read_text())
    schema_keys = {str(k) for k in mods.vision.DESCRIBE_IMAGE_SCHEMA.schema}
    assert set(services["describe_image"]["fields"]) == schema_keys
    assert set(strings["services"]["describe_image"]["fields"]) == schema_keys
    assert services["describe_image"]["fields"]["style"]["selector"]["select"]["options"] == list(
        mods.const.STYLE_PRESETS
    )


def test_manifest_lists_the_helper_components() -> None:
    manifest = json.loads((_PKG_DIR / "manifest.json").read_text())
    assert {"camera", "media_source"} <= set(manifest["after_dependencies"])


# --- schema ------------------------------------------------------------


def test_schema_accepts_a_camera_call(mods: types.SimpleNamespace) -> None:
    data = mods.vision.DESCRIBE_IMAGE_SCHEMA({"camera_entity_id": "camera.front_door"})
    assert data == {"camera_entity_id": "camera.front_door"}


@pytest.mark.parametrize(
    "bad",
    [
        {"camera_entity_id": "light.porch"},
        {"style": "x" * 201},
        {"instructions": "y" * 301},
        {"unknown": 1},
    ],
)
def test_schema_rejects_bad_input(mods: types.SimpleNamespace, bad: dict[str, Any]) -> None:
    with pytest.raises(vol.Invalid):
        mods.vision.DESCRIBE_IMAGE_SCHEMA(bad)


def test_schema_accepts_limits(mods: types.SimpleNamespace) -> None:
    mods.vision.DESCRIBE_IMAGE_SCHEMA({"style": "x" * 200, "instructions": "y" * 300})


# --- downscaling -------------------------------------------------------


def _dims(jpeg: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(jpeg)) as img:
        assert img.format == "JPEG"
        return img.size


def test_large_images_are_scaled_to_1280_on_the_long_side(mods: types.SimpleNamespace) -> None:
    assert _dims(mods.vision.downscale_to_jpeg(_png((4000, 2000)))) == (1280, 640)
    assert _dims(mods.vision.downscale_to_jpeg(_png((1000, 3000)))) == (427, 1280)


def test_small_images_are_not_enlarged(mods: types.SimpleNamespace) -> None:
    assert _dims(mods.vision.downscale_to_jpeg(_png((300, 200)))) == (300, 200)


def test_transparency_is_flattened(mods: types.SimpleNamespace) -> None:
    assert _dims(mods.vision.downscale_to_jpeg(_png((50, 50), "RGBA"))) == (50, 50)


def test_exif_orientation_is_applied(mods: types.SimpleNamespace) -> None:
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    Image.new("RGB", (400, 200), "blue").save(buf, format="JPEG", exif=exif)
    assert _dims(mods.vision.downscale_to_jpeg(buf.getvalue())) == (200, 400)


@pytest.mark.parametrize("junk", [b"", b"not an image", b"\xff\xd8\xff truncated"])
def test_unreadable_images_raise_a_coded_error(mods: types.SimpleNamespace, junk: bytes) -> None:
    with pytest.raises(_HomeAssistantError, match="IMAGE_UNREADABLE"):
        mods.vision.downscale_to_jpeg(junk)


def test_decompression_bombs_are_refused(
    mods: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(_HomeAssistantError, match="IMAGE_UNREADABLE"):
        mods.vision.downscale_to_jpeg(_png((400, 400)))


# --- the service -------------------------------------------------------


@pytest.mark.asyncio
async def test_camera_snapshot_is_downscaled_and_described(
    mods: types.SimpleNamespace, setup: Callable[..., tuple[_Hass, _Session]]
) -> None:
    hass, session = setup(entry=_entry({"default_style": "serious"}))
    seen: dict[str, Any] = {}

    async def get_image(h: Any, entity_id: str, timeout: int) -> Any:
        seen.update(entity_id=entity_id, timeout=timeout)
        return types.SimpleNamespace(content=_png((3000, 2000)), content_type="image/png")

    mods.camera.async_get_image = get_image
    result = await mods.vision._async_describe_image(
        _call(hass, camera_entity_id="camera.front_door", instructions="packages")
    )
    assert result == {"text": "A courier at the door."}
    assert seen == {"entity_id": "camera.front_door", "timeout": 15}
    [post] = session.posts
    assert post["url"] == "http://node.test:8099/v1/vision/describe"
    assert post["headers"] == {"Authorization": "Bearer tok"}
    payload = post["json"]
    assert payload["mime"] == "image/jpeg"
    assert payload["style"] == "serious"
    assert payload["instructions"] == "packages"
    assert payload["source"] == "camera.front_door"
    assert _dims(base64.b64decode(payload["image_base64"])) == (1280, 853)


@pytest.mark.asyncio
async def test_explicit_style_beats_the_default_and_default_falls_back(
    mods: types.SimpleNamespace, setup: Callable[..., tuple[_Hass, _Session]], tmp_path: Path
) -> None:
    image = tmp_path / "a.png"
    image.write_bytes(_png())
    hass, session = setup(entry=_entry({"default_style": "funny"}))
    await mods.vision._async_describe_image(_call(hass, file_path=str(image), style="serious"))
    await mods.vision._async_describe_image(_call(hass, file_path=str(image)))
    hass2, session2 = setup(entry=_entry())
    await mods.vision._async_describe_image(_call(hass2, file_path=str(image)))
    assert session.posts[0]["json"]["style"] == "serious"
    assert session.posts[1]["json"]["style"] == "funny"
    assert session2.posts[0]["json"]["style"] == "straightforward"
    assert "instructions" not in session.posts[0]["json"]


@pytest.mark.asyncio
async def test_no_auth_header_without_a_token(
    mods: types.SimpleNamespace, setup: Callable[..., tuple[_Hass, _Session]], tmp_path: Path
) -> None:
    image = tmp_path / "a.png"
    image.write_bytes(_png())
    hass, session = setup(entry=_entry(token=""))
    await mods.vision._async_describe_image(_call(hass, file_path=str(image)))
    assert session.posts[0]["headers"] == {}


@pytest.mark.asyncio
async def test_file_source_must_be_allowed(
    mods: types.SimpleNamespace, setup: Callable[..., tuple[_Hass, _Session]]
) -> None:
    hass, session = setup()
    with pytest.raises(_ServiceValidationError, match="IMAGE_PATH_NOT_ALLOWED"):
        await mods.vision._async_describe_image(_call(hass, file_path="/etc/passwd"))
    assert session.posts == []


@pytest.mark.asyncio
async def test_missing_and_oversized_files(
    mods: types.SimpleNamespace,
    setup: Callable[..., tuple[_Hass, _Session]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass, _ = setup()
    with pytest.raises(_ServiceValidationError, match="IMAGE_UNAVAILABLE"):
        await mods.vision._async_describe_image(_call(hass, file_path=str(tmp_path / "nope.png")))
    big = tmp_path / "big.png"
    big.write_bytes(_png())
    monkeypatch.setattr(mods.vision, "IMAGE_MAX_SOURCE_BYTES", 10)
    with pytest.raises(_ServiceValidationError, match="IMAGE_TOO_LARGE"):
        await mods.vision._async_describe_image(_call(hass, file_path=str(big)))


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"style": "funny"},
        {"camera_entity_id": "camera.a", "file_path": "/config/a.png"},
        {"camera_entity_id": "camera.a", "media_content_id": "m", "file_path": "/config/a.png"},
    ],
)
@pytest.mark.asyncio
async def test_exactly_one_image_source_is_required(
    mods: types.SimpleNamespace, setup: Callable[..., tuple[_Hass, _Session]], data: dict[str, Any]
) -> None:
    hass, _ = setup()
    with pytest.raises(_ServiceValidationError, match="exactly one"):
        await mods.vision._async_describe_image(_call(hass, **data))


@pytest.mark.asyncio
async def test_node_entry_must_be_loaded(
    mods: types.SimpleNamespace, setup: Callable[..., tuple[_Hass, _Session]]
) -> None:
    hass, _ = setup(entries=[])
    with pytest.raises(_ServiceValidationError, match="NODE_NOT_CONFIGURED"):
        await mods.vision._async_describe_image(_call(hass, camera_entity_id="camera.a"))


@pytest.mark.asyncio
async def test_media_source_local_file(
    mods: types.SimpleNamespace, setup: Callable[..., tuple[_Hass, _Session]], tmp_path: Path
) -> None:
    image = tmp_path / "m.png"
    image.write_bytes(_png())
    hass, session = setup()

    async def resolve(h: Any, content_id: str, target: Any) -> Any:
        assert content_id == "media-source://media_source/local/m.png"
        return types.SimpleNamespace(path=image, url="/media/local/m.png", mime_type="image/png")

    mods.media_source.async_resolve_media = resolve
    mods.media_player.async_process_play_media_url = lambda *_a, **_k: "unused"
    await mods.vision._async_describe_image(
        _call(hass, media_content_id="media-source://media_source/local/m.png")
    )
    assert session.posts[0]["json"]["source"] == "media-source://media_source/local/m.png"


@pytest.mark.asyncio
async def test_media_source_remote_url(
    mods: types.SimpleNamespace,
    setup: Callable[..., tuple[_Hass, _Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass, _ = setup()

    async def resolve(h: Any, content_id: str, target: Any) -> Any:
        return types.SimpleNamespace(path=None, url="/api/img/1", mime_type="image/png")

    class _FetchThenDescribe(_Session):
        def get(self, url: str, **_kwargs: Any) -> _Resp:
            self.fetched = url
            return _Resp(200, _png())

        def post(self, url: str, **kwargs: Any) -> _Resp:
            self.posts.append({"url": url, **kwargs})
            return _Resp(200, OK_BODY)

    session = _FetchThenDescribe(_Resp(200, OK_BODY))
    monkeypatch.setattr(mods.vision, "async_get_clientsession", lambda _h: session)
    mods.media_source.async_resolve_media = resolve
    mods.media_player.async_process_play_media_url = lambda _h, url, **_k: f"http://ha.test{url}"
    result = await mods.vision._async_describe_image(
        _call(hass, media_content_id="media-source://x")
    )
    assert result == {"text": "A courier at the door."}
    assert session.fetched == "http://ha.test/api/img/1"


@pytest.mark.asyncio
async def test_media_source_remote_failures(
    mods: types.SimpleNamespace,
    setup: Callable[..., tuple[_Hass, _Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve(h: Any, content_id: str, target: Any) -> Any:
        return types.SimpleNamespace(path=None, url="/api/img/1", mime_type="image/png")

    mods.media_source.async_resolve_media = resolve
    mods.media_player.async_process_play_media_url = lambda _h, url, **_k: url

    hass, _ = setup(_Resp(404, b""))
    with pytest.raises(_HomeAssistantError, match="IMAGE_UNAVAILABLE"):
        await mods.vision._async_describe_image(_call(hass, media_content_id="x"))

    hass, _ = setup(aiohttp.ClientError("down"))
    with pytest.raises(_HomeAssistantError, match="IMAGE_UNAVAILABLE"):
        await mods.vision._async_describe_image(_call(hass, media_content_id="x"))

    monkeypatch.setattr(mods.vision, "IMAGE_MAX_SOURCE_BYTES", 5)
    hass, _ = setup(_Resp(200, b"123456789"))
    with pytest.raises(_ServiceValidationError, match="IMAGE_TOO_LARGE"):
        await mods.vision._async_describe_image(_call(hass, media_content_id="x"))


@pytest.mark.parametrize(
    ("resp", "pattern"),
    [
        (
            _Resp(
                504, {"ok": False, "error": "TIMEOUT", "message": "The description took too long."}
            ),
            "TIMEOUT: The description took too long",
        ),
        (
            _Resp(409, {"ok": False, "error": "SUPERSEDED", "message": "replaced"}),
            "SUPERSEDED: replaced",
        ),
        (_Resp(401, {"ok": False, "error": "UNAUTHORIZED"}), "UNAUTHORIZED"),
        (_Resp(502, {"ok": False}), "HTTP_502"),
        (_Resp(500, None, raises=ValueError("bad json")), "HTTP_500"),
        (_Resp(200, ["not", "a", "dict"]), "HTTP_200"),
        (_Resp(200, {"ok": True, "text": "   "}), "HTTP_200"),
        (_Resp(200, {"ok": True}), "HTTP_200"),
    ],
)
@pytest.mark.asyncio
async def test_node_errors_surface_the_code(
    mods: types.SimpleNamespace,
    setup: Callable[..., tuple[_Hass, _Session]],
    tmp_path: Path,
    resp: _Resp,
    pattern: str,
) -> None:
    image = tmp_path / "a.png"
    image.write_bytes(_png())
    hass, _ = setup(resp)
    with pytest.raises(_HomeAssistantError, match=pattern):
        await mods.vision._async_describe_image(_call(hass, file_path=str(image)))


@pytest.mark.parametrize(
    ("exc", "code"),
    [(TimeoutError(), "TIMEOUT"), (aiohttp.ClientConnectionError("refused"), "NODE_UNREACHABLE")],
)
@pytest.mark.asyncio
async def test_transport_errors_are_coded(
    mods: types.SimpleNamespace,
    setup: Callable[..., tuple[_Hass, _Session]],
    tmp_path: Path,
    exc: Exception,
    code: str,
) -> None:
    image = tmp_path / "a.png"
    image.write_bytes(_png())
    hass, _ = setup(exc)
    with pytest.raises(_HomeAssistantError, match=code):
        await mods.vision._async_describe_image(_call(hass, file_path=str(image)))


# --- options flow ------------------------------------------------------


def test_options_flow_is_exposed_on_the_config_flow(mods: types.SimpleNamespace) -> None:
    flow = mods.config_flow.OpenClawGatewayConfigFlow.async_get_options_flow(object())
    assert isinstance(flow, mods.config_flow.OpenClawOptionsFlow)


@pytest.mark.asyncio
async def test_options_form_defaults_to_the_current_style(mods: types.SimpleNamespace) -> None:
    flow = mods.config_flow.OpenClawOptionsFlow()
    flow.config_entry = types.SimpleNamespace(options={"default_style": "funny"})
    form = await flow.async_step_init()
    assert form["step_id"] == "init"
    [marker] = list(form["data_schema"].schema)
    assert marker.schema == "default_style"
    assert marker.default() == "funny"
    selector = form["data_schema"].schema[marker]
    assert selector.config.options == ["straightforward", "serious", "funny"]
    assert selector.config.custom_value is True
    assert selector.config.translation_key == "describe_image_style"
    assert form["errors"] == {}

    flow.config_entry = types.SimpleNamespace(options={})
    [marker2] = list((await flow.async_step_init())["data_schema"].schema)
    assert marker2.default() == "straightforward"


@pytest.mark.asyncio
async def test_options_submit_stores_the_style(mods: types.SimpleNamespace) -> None:
    flow = mods.config_flow.OpenClawOptionsFlow()
    flow.config_entry = types.SimpleNamespace(options={})
    created = await flow.async_step_init({"default_style": "  dry and sarcastic "})
    assert created["data"] == {"default_style": "dry and sarcastic"}
    blank = await flow.async_step_init({"default_style": "  "})
    assert blank["data"] == {"default_style": "straightforward"}


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", ["straightforward", "serious", "funny"])
async def test_options_submit_stores_each_preset(mods: types.SimpleNamespace, preset: str) -> None:
    flow = mods.config_flow.OpenClawOptionsFlow()
    flow.config_entry = types.SimpleNamespace(options={})
    created = await flow.async_step_init({"default_style": preset})
    assert created["type"] == "create_entry"
    assert created["data"] == {"default_style": preset}


@pytest.mark.asyncio
async def test_options_submit_accepts_exactly_the_cap(mods: types.SimpleNamespace) -> None:
    flow = mods.config_flow.OpenClawOptionsFlow()
    flow.config_entry = types.SimpleNamespace(options={})
    text = "x" * mods.const.MAX_STYLE_CHARS
    created = await flow.async_step_init({"default_style": text})
    assert created["data"] == {"default_style": text}


@pytest.mark.asyncio
async def test_options_submit_rejects_over_long_text_with_a_form_error(
    mods: types.SimpleNamespace,
) -> None:
    flow = mods.config_flow.OpenClawOptionsFlow()
    flow.config_entry = types.SimpleNamespace(options={"default_style": "funny"})
    text = "x" * (mods.const.MAX_STYLE_CHARS + 1)
    form = await flow.async_step_init({"default_style": text})
    assert form["type"] == "form"
    assert form["step_id"] == "init"
    assert form["errors"] == {"default_style": "style_too_long"}
    [marker] = list(form["data_schema"].schema)
    assert marker.default() == text
    # Padding around an in-range value is trimmed before the cap is applied.
    padded = await flow.async_step_init({"default_style": f"  {'x' * 200}  "})
    assert padded["type"] == "create_entry"


@pytest.mark.asyncio
async def test_saved_option_is_used_when_no_style_is_passed(
    mods: types.SimpleNamespace,
    setup: Callable[..., tuple[_Hass, _Session]],
    tmp_path: Path,
) -> None:
    image = tmp_path / "a.png"
    image.write_bytes(_png())
    flow = mods.config_flow.OpenClawOptionsFlow()
    flow.config_entry = types.SimpleNamespace(options={})
    saved = (await flow.async_step_init({"default_style": "dry and sarcastic"}))["data"]
    hass, session = setup(entry=_entry(saved))
    await mods.vision._async_describe_image(_call(hass, file_path=str(image)))
    await mods.vision._async_describe_image(_call(hass, file_path=str(image), style="serious"))
    assert session.posts[0]["json"]["style"] == "dry and sarcastic"
    assert session.posts[1]["json"]["style"] == "serious"


def test_translations_mirror_strings_and_cover_every_key_the_code_uses(
    mods: types.SimpleNamespace,
) -> None:
    strings = json.loads((_PKG_DIR / "strings.json").read_text())
    translations = json.loads((_PKG_DIR / "translations" / "en.json").read_text())
    assert translations == strings
    init = translations["options"]["step"]["init"]
    assert set(init["data"]) == {"default_style"} == set(init["data_description"])
    assert init["title"]
    assert "style_too_long" in translations["options"]["error"]
    assert set(translations["selector"]["describe_image_style"]["options"]) == set(
        mods.const.STYLE_PRESETS
    )
    service = translations["services"]["describe_image"]
    assert service["name"]
    assert service["description"]
