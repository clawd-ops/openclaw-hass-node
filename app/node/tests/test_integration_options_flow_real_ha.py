"""The options flow against real Home Assistant, not stubs.

The stubbed tests in ``test_describe_image_service.py`` cannot notice a schema
that Home Assistant's frontend serializer rejects, which is the failure that
leaves a user with no form. These tests run the integration's flow and push
its schema through Home Assistant's own ``to_field_list`` serializer. They are
skipped when Home Assistant is not installed (it is not a dependency of this
repository).
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

_PKG_DIR = Path(__file__).resolve().parents[3] / "custom_components" / "openclaw_hass_node_assist"
_PKG = "oc_assist_real_ha_under_test"


@pytest.fixture
def real_flow(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    pytest.importorskip("homeassistant")
    cv = pytest.importorskip("homeassistant.helpers.config_validation")
    probatio = pytest.importorskip("probatio")
    package = ModuleType(_PKG)
    package.__path__ = [str(_PKG_DIR)]
    monkeypatch.setitem(sys.modules, _PKG, package)
    for leaf in ("const", "config_flow"):
        spec = importlib.util.spec_from_file_location(f"{_PKG}.{leaf}", _PKG_DIR / f"{leaf}.py")
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, f"{_PKG}.{leaf}", mod)
        spec.loader.exec_module(mod)
        setattr(package, leaf, mod)
    return SimpleNamespace(
        flow=package.config_flow.OpenClawOptionsFlow,
        const=package.const,
        serialize=lambda schema: probatio.to_field_list(
            schema, custom_serializer=cv.custom_serializer
        ),
    )


def _flow(
    real_flow: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, options: dict[str, Any]
) -> Any:
    # ``config_entry`` is a read-only property on the real base class.
    monkeypatch.setattr(
        real_flow.flow, "config_entry", property(lambda _self: SimpleNamespace(options=options))
    )
    return real_flow.flow()


def test_options_form_schema_serializes_for_the_frontend(
    real_flow: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    form = asyncio.run(_flow(real_flow, monkeypatch, {}).async_step_init())
    assert form["type"] == "form"
    [field] = real_flow.serialize(form["data_schema"])
    assert field["name"] == "default_style"
    assert field["default"] == "straightforward"
    select = field["selector"]["select"]
    assert select["options"] == ["straightforward", "serious", "funny"]
    assert select["custom_value"] is True
    assert select["translation_key"] == "describe_image_style"


def test_error_form_serializes_and_keeps_the_text(
    real_flow: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "x" * (real_flow.const.MAX_STYLE_CHARS + 1)
    form = asyncio.run(_flow(real_flow, monkeypatch, {}).async_step_init({"default_style": text}))
    assert form["errors"] == {"default_style": "style_too_long"}
    [field] = real_flow.serialize(form["data_schema"])
    assert field["default"] == text


def test_the_selector_accepts_presets_and_custom_text(
    real_flow: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    form = asyncio.run(_flow(real_flow, monkeypatch, {}).async_step_init())
    schema = form["data_schema"]
    assert schema({"default_style": "funny"}) == {"default_style": "funny"}
    assert schema({"default_style": "dry and sarcastic"}) == {"default_style": "dry and sarcastic"}
