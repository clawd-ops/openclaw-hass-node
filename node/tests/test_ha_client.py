"""Tests for openclaw_node.ha_client."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openclaw_node.ha_client import HAClientError, ha_get, ha_post

# ---------------------------------------------------------------------------
# Helpers — fake aiohttp ClientSession
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(
        self,
        status: int = 200,
        body: Any = None,
        content_type: str = "application/json",
        text_body: str | None = None,
    ) -> None:
        self.status = status
        self._body = body
        self.content_type = content_type
        self._text = text_body if text_body is not None else json.dumps(body or {})
        self.url = MagicMock(path="/api/test")

    async def __aenter__(self) -> _FakeResp:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self) -> Any:
        return self._body

    async def text(self) -> str:
        return self._text


def _patch_session(resp: _FakeResp, *, post: bool = False) -> Any:
    """Patch aiohttp.ClientSession to return *resp* on get/post."""
    session = MagicMock()
    if post:
        session.post = MagicMock(return_value=resp)
    else:
        session.get = MagicMock(return_value=resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return patch("aiohttp.ClientSession", return_value=session)


# ---------------------------------------------------------------------------
# Configuration / env failures
# ---------------------------------------------------------------------------


async def test_ha_get_raises_when_no_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("HASS_URL", raising=False)
    monkeypatch.setenv("HASS_TOKEN", "tok")
    with pytest.raises(HAClientError) as ei:
        await ha_get("/api/states")
    assert ei.value.code == "HA_NOT_CONFIGURED"


async def test_ha_get_raises_when_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.setenv("HASS_URL", "http://ha.local")
    monkeypatch.delenv("HASS_TOKEN", raising=False)
    with pytest.raises(HAClientError) as ei:
        await ha_get("/api/states")
    assert ei.value.code == "HA_NOT_CONFIGURED"


async def test_ha_get_uses_supervisor_in_addon_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-tok")
    monkeypatch.delenv("HASS_URL", raising=False)
    resp = _FakeResp(body=[{"entity_id": "x"}])
    with _patch_session(resp) as fake:
        await ha_get("/api/states")
    # The session.get is called with the supervisor base URL.
    call_args = fake.return_value.get.call_args
    assert "supervisor" in call_args[0][0]


# ---------------------------------------------------------------------------
# ha_get — HTTP status handling
# ---------------------------------------------------------------------------


async def test_ha_get_401_raises_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HASS_URL", "http://ha.local")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    resp = _FakeResp(status=401, body={})
    with _patch_session(resp), pytest.raises(HAClientError) as ei:
        await ha_get("/api/states")
    assert ei.value.code == "HA_AUTH"


async def test_ha_get_404_raises_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HASS_URL", "http://ha.local")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    resp = _FakeResp(status=404, body={})
    with _patch_session(resp), pytest.raises(HAClientError) as ei:
        await ha_get("/api/states/sensor.missing")
    assert ei.value.code == "HA_NOT_FOUND"


async def test_ha_get_500_raises_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HASS_URL", "http://ha.local")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    resp = _FakeResp(status=500, text_body="boom")
    with _patch_session(resp), pytest.raises(HAClientError) as ei:
        await ha_get("/api/states")
    assert ei.value.code == "HA_HTTP_ERROR"
    assert "500" in ei.value.message


async def test_ha_get_network_error_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    import aiohttp

    monkeypatch.setenv("HASS_URL", "http://ha.local")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.get = MagicMock(side_effect=aiohttp.ClientError("dns fail"))
    with patch("aiohttp.ClientSession", return_value=session), pytest.raises(HAClientError) as ei:
        await ha_get("/api/states")
    assert ei.value.code == "HA_NETWORK"


async def test_ha_get_returns_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HASS_URL", "http://ha.local")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    resp = _FakeResp(body=[{"entity_id": "sensor.a", "state": "1"}])
    with _patch_session(resp):
        result = await ha_get("/api/states")
    assert isinstance(result, list)
    assert result[0]["entity_id"] == "sensor.a"


async def test_ha_get_returns_text_for_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HASS_URL", "http://ha.local")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    resp = _FakeResp(content_type="text/plain", text_body="hello")
    with _patch_session(resp):
        result = await ha_get("/api/")
    assert result == "hello"


# ---------------------------------------------------------------------------
# ha_post
# ---------------------------------------------------------------------------


async def test_ha_post_sends_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HASS_URL", "http://ha.local")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    resp = _FakeResp(body=[])
    with _patch_session(resp, post=True) as fake:
        await ha_post("/api/services/light/turn_on", {"entity_id": "light.x"})
    call_args = fake.return_value.post.call_args
    assert call_args.kwargs["json"] == {"entity_id": "light.x"}


async def test_ha_post_network_error_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    import aiohttp

    monkeypatch.setenv("HASS_URL", "http://ha.local")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.post = MagicMock(side_effect=aiohttp.ClientError("conn refused"))
    with patch("aiohttp.ClientSession", return_value=session), pytest.raises(HAClientError) as ei:
        await ha_post("/api/services/x/y", {})
    assert ei.value.code == "HA_NETWORK"
