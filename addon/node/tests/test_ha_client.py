"""Tests for openclaw_node.ha_client."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openclaw_node.ha_client import (
    HAClientError,
    ha_get,
    ha_post,
    ha_ws_call,
    supervisor_get_json,
    supervisor_get_text,
    supervisor_post_json,
)

# ---------------------------------------------------------------------------
# Helpers — fake aiohttp ClientSession
# ---------------------------------------------------------------------------


class _FakeContent:
    def __init__(self, body: str) -> None:
        self._body = body.encode()

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        for pos in range(0, len(self._body), size):
            yield self._body[pos : pos + size]


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
        self.content = _FakeContent(self._text)
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


async def test_ha_url_ignores_hass_url_when_supervisor_token_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HASS_URL must not override the Supervisor URL when SUPERVISOR_TOKEN is set."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-tok")
    monkeypatch.setenv("HASS_URL", "http://evil.example.com")
    resp = _FakeResp(body=[{"entity_id": "x"}])
    with _patch_session(resp) as fake:
        await ha_get("/api/states")
    call_url = fake.return_value.get.call_args[0][0]
    assert "supervisor" in call_url
    assert "evil" not in call_url


def test_is_addon_mode_with_supervisor_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """SUPERVISOR_TOKEN makes _is_addon_mode return True."""
    from openclaw_node.ha_client import _is_addon_mode

    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    assert _is_addon_mode() is True


def test_is_addon_mode_writable_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Writable /data without SUPERVISOR_TOKEN is addon mode."""
    from openclaw_node.ha_client import _is_addon_mode

    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    with (
        patch("openclaw_node.ha_client.Path") as mock_path,
        patch("openclaw_node.ha_client.os.access", return_value=True),
    ):
        mock_path.return_value.is_dir.return_value = True
        assert _is_addon_mode() is True


def test_is_addon_mode_no_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """No SUPERVISOR_TOKEN and no /data is not addon mode."""
    from openclaw_node.ha_client import _is_addon_mode

    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    with (
        patch("openclaw_node.ha_client.Path") as mock_path,
        patch("openclaw_node.ha_client.os.access", return_value=False),
    ):
        mock_path.return_value.is_dir.return_value = False
        assert _is_addon_mode() is False


async def test_ha_url_addon_fallback_without_supervisor_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Addon mode via /data fallback uses http://homeassistant."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("HASS_URL", raising=False)
    monkeypatch.setenv("HASS_TOKEN", "ha-tok")
    with patch("openclaw_node.ha_client._is_addon_mode", return_value=True):
        resp = _FakeResp(body=[])
        with _patch_session(resp) as fake:
            await ha_get("/api/states")
        call_args = fake.return_value.get.call_args
        assert "homeassistant" in call_args[0][0]


async def test_ha_token_addon_fallback_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Addon mode without any token gives a descriptive error."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("HASS_TOKEN", raising=False)
    monkeypatch.setenv("HASS_URL", "http://homeassistant")
    with (
        patch("openclaw_node.ha_client._is_addon_mode", return_value=True),
        pytest.raises(HAClientError) as ei,
    ):
        await ha_get("/api/states")
    assert ei.value.code == "HA_NOT_CONFIGURED"
    assert "SUPERVISOR_TOKEN" in ei.value.message


# ---------------------------------------------------------------------------
# supervisor_post_json
# ---------------------------------------------------------------------------


async def test_supervisor_post_json_requires_supervisor_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    with pytest.raises(HAClientError) as ei:
        await supervisor_post_json("/addons/x/start")
    assert ei.value.code == "SUPERVISOR_UNAVAILABLE"


async def test_supervisor_post_json_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sv-token")
    resp = _FakeResp(body={"result": "ok"}, text_body='{"result":"ok"}')
    with _patch_session(resp, post=True) as fake:
        result = await supervisor_post_json("/addons/x/start", {"a": 1})
    assert result == {"result": "ok"}
    fake.return_value.post.assert_called_once()


async def test_supervisor_post_json_empty_body_returns_empty_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sv-token")
    resp = _FakeResp(body={}, text_body="")
    with _patch_session(resp, post=True):
        result = await supervisor_post_json("/addons/x/start")
    assert result == {}


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "HA_AUTH"),
        (404, "HA_NOT_FOUND"),
        (500, "HA_HTTP_ERROR"),
    ],
)
async def test_supervisor_post_json_http_errors(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    code: str,
) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sv-token")
    resp = _FakeResp(status=status, text_body="boom")
    with _patch_session(resp, post=True), pytest.raises(HAClientError) as ei:
        await supervisor_post_json("/addons/x/start")
    assert ei.value.code == code


async def test_supervisor_post_json_response_too_large(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sv-token")
    resp = _FakeResp(body={}, text_body='{"too":"large"}')
    with _patch_session(resp, post=True), pytest.raises(HAClientError) as ei:
        await supervisor_post_json("/addons/x/start", max_bytes=1)
    assert ei.value.code == "HA_RESPONSE_TOO_LARGE"


async def test_supervisor_post_json_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sv-token")
    resp = _FakeResp(body={}, text_body="{not-json")
    with _patch_session(resp, post=True), pytest.raises(HAClientError) as ei:
        await supervisor_post_json("/addons/x/start")
    assert ei.value.code == "HA_BAD_RESPONSE"


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


# ---------------------------------------------------------------------------
# ha_ws_call — WebSocket helper
# ---------------------------------------------------------------------------


def _patch_ws_session(
    messages: list[dict[str, Any]],
    *,
    ws_error: Exception | None = None,
) -> Any:
    """Patch aiohttp.ClientSession so ws_connect returns a fake WS."""
    ws = MagicMock()
    if ws_error is not None:
        ws.receive_json = AsyncMock(side_effect=ws_error)
    else:
        ws.receive_json = AsyncMock(side_effect=messages)
    ws.send_json = AsyncMock()
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.ws_connect = MagicMock(return_value=ws)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return patch("aiohttp.ClientSession", return_value=session)


_AUTH_REQUIRED = {"type": "auth_required", "ha_version": "2026.6.0"}
_AUTH_OK = {"type": "auth_ok"}


def _ws_success(result: Any) -> dict[str, Any]:
    return {"id": 1, "type": "result", "success": True, "result": result}


def _ws_error_msg(code: str, message: str) -> dict[str, Any]:
    return {
        "id": 1,
        "type": "result",
        "success": False,
        "error": {"code": code, "message": message},
    }


async def test_ws_call_returns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HASS_URL", "http://ha.local")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    areas = [{"area_id": "living_room", "name": "Living Room"}]
    messages = [_AUTH_REQUIRED, _AUTH_OK, _ws_success(areas)]
    with _patch_ws_session(messages):
        result = await ha_ws_call("config/area_registry/list")
    assert result == areas


async def test_ws_call_sends_correct_message_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HASS_URL", "http://ha.local")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    messages = [_AUTH_REQUIRED, _AUTH_OK, _ws_success([])]
    with _patch_ws_session(messages) as fake:
        await ha_ws_call("config/device_registry/list")
    ws = fake.return_value.ws_connect.return_value.__aenter__.return_value
    sent = ws.send_json.call_args_list
    # Second send_json call is the registry request.
    assert sent[1][0][0]["type"] == "config/device_registry/list"
    assert sent[1][0][0]["id"] == 1


async def test_ws_call_auth_rejected_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HASS_URL", "http://ha.local")
    monkeypatch.setenv("HASS_TOKEN", "bad-tok")
    messages = [_AUTH_REQUIRED, {"type": "auth_invalid", "message": "Invalid password"}]
    with _patch_ws_session(messages), pytest.raises(HAClientError) as ei:
        await ha_ws_call("config/area_registry/list")
    assert ei.value.code == "HA_AUTH"


async def test_ws_call_unexpected_first_message_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HASS_URL", "http://ha.local")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    messages = [{"type": "unexpected"}]
    with _patch_ws_session(messages), pytest.raises(HAClientError) as ei:
        await ha_ws_call("config/area_registry/list")
    assert ei.value.code == "HA_WS_ERROR"


async def test_ws_call_error_response_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HASS_URL", "http://ha.local")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    messages = [_AUTH_REQUIRED, _AUTH_OK, _ws_error_msg("not_found", "Area not found")]
    with _patch_ws_session(messages), pytest.raises(HAClientError) as ei:
        await ha_ws_call("config/area_registry/list")
    assert "NOT_FOUND" in ei.value.code


async def test_ws_call_network_error_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    import aiohttp

    monkeypatch.setenv("HASS_URL", "http://ha.local")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.ws_connect = MagicMock(side_effect=aiohttp.ClientError("ws refused"))
    with patch("aiohttp.ClientSession", return_value=session), pytest.raises(HAClientError) as ei:
        await ha_ws_call("config/area_registry/list")
    assert ei.value.code == "HA_NETWORK"


async def test_ws_call_uses_ws_url_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HASS_URL", "https://ha.local")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    messages = [_AUTH_REQUIRED, _AUTH_OK, _ws_success([])]
    with _patch_ws_session(messages) as fake:
        await ha_ws_call("config/area_registry/list")
    call_url = str(fake.return_value.ws_connect.call_args[0][0])
    assert call_url.startswith("wss://")


async def test_ws_call_supervisor_mode_uses_supervisor_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-tok")
    monkeypatch.delenv("HASS_URL", raising=False)
    messages = [_AUTH_REQUIRED, _AUTH_OK, _ws_success([])]
    with _patch_ws_session(messages) as fake:
        await ha_ws_call("config/area_registry/list")
    call_url = str(fake.return_value.ws_connect.call_args[0][0])
    assert "supervisor" in call_url


# ---------------------------------------------------------------------------
# supervisor_get_text
# ---------------------------------------------------------------------------


async def test_supervisor_get_text_requires_supervisor_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    with pytest.raises(HAClientError) as ei:
        await supervisor_get_text("/addons/self/logs")
    assert ei.value.code == "SUPERVISOR_UNAVAILABLE"


async def test_supervisor_get_text_returns_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-tok")
    resp = _FakeResp(
        status=200,
        content_type="text/plain",
        text_body="line1\nline2\n",
    )
    with _patch_session(resp) as fake:
        body = await supervisor_get_text("/addons/self/logs")
    assert body == "line1\nline2\n"
    call_args = fake.return_value.get.call_args
    assert call_args[0][0] == "http://supervisor/addons/self/logs"
    assert call_args[1]["headers"]["Authorization"] == "Bearer sup-tok"


async def test_supervisor_get_text_keeps_only_trailing_max_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-tok")
    resp = _FakeResp(
        status=200,
        content_type="text/plain",
        text_body="0123456789",
    )
    with _patch_session(resp):
        body = await supervisor_get_text("/addons/self/logs", max_bytes=4)
    assert body == "6789"


async def test_supervisor_get_text_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-tok")
    resp = _FakeResp(status=401, content_type="text/plain", text_body="")
    with _patch_session(resp), pytest.raises(HAClientError) as ei:
        await supervisor_get_text("/addons/self/logs")
    assert ei.value.code == "HA_AUTH"


async def test_supervisor_get_text_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-tok")
    resp = _FakeResp(status=404, content_type="text/plain", text_body="")
    with _patch_session(resp), pytest.raises(HAClientError) as ei:
        await supervisor_get_text("/addons/missing/logs")
    assert ei.value.code == "HA_NOT_FOUND"


async def test_supervisor_get_text_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-tok")
    resp = _FakeResp(status=500, content_type="text/plain", text_body="boom")
    with _patch_session(resp), pytest.raises(HAClientError) as ei:
        await supervisor_get_text("/addons/self/logs")
    assert ei.value.code == "HA_HTTP_ERROR"
    assert ei.value.message == "Supervisor returned 500: boom"


async def test_supervisor_get_text_suppresses_html_error_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-tok")
    resp = _FakeResp(
        status=504,
        content_type="text/html",
        text_body=(
            "<html><head><title>504 Gateway Time-out</title></head>"
            "<body><center><h1>504 Gateway Time-out</h1></center></body></html>"
        ),
    )
    with _patch_session(resp), pytest.raises(HAClientError) as ei:
        await supervisor_get_text("/addons/self/logs")
    assert ei.value.code == "HA_HTTP_ERROR"
    assert ei.value.message == "Supervisor returned 504 (HTML error page suppressed)"
    assert "<html>" not in ei.value.message


async def test_supervisor_get_text_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aiohttp

    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-tok")
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.get = MagicMock(side_effect=aiohttp.ClientError("conn refused"))
    with (
        patch("aiohttp.ClientSession", return_value=session),
        pytest.raises(HAClientError) as ei,
    ):
        await supervisor_get_text("/addons/self/logs")
    assert ei.value.code == "HA_NETWORK"


# ---------------------------------------------------------------------------
# supervisor_get_json
# ---------------------------------------------------------------------------


async def test_supervisor_get_json_requires_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    with pytest.raises(HAClientError) as ei:
        await supervisor_get_json("/addons")
    assert ei.value.code == "SUPERVISOR_UNAVAILABLE"


async def test_supervisor_get_json_returns_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-tok")
    payload = {"result": "ok", "data": {"addons": [{"slug": "a"}]}}
    resp = _FakeResp(status=200, body=payload, content_type="application/json")
    with _patch_session(resp) as fake:
        out = await supervisor_get_json("/addons")
    assert out == payload
    call_args = fake.return_value.get.call_args
    assert call_args[0][0] == "http://supervisor/addons"
    assert call_args[1]["headers"]["Authorization"] == "Bearer sup-tok"


async def test_supervisor_get_json_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-tok")
    resp = _FakeResp(status=401, content_type="application/json")
    with _patch_session(resp), pytest.raises(HAClientError) as ei:
        await supervisor_get_json("/addons")
    assert ei.value.code == "HA_AUTH"


async def test_supervisor_get_json_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-tok")
    resp = _FakeResp(status=404, content_type="application/json")
    with _patch_session(resp), pytest.raises(HAClientError) as ei:
        await supervisor_get_json("/addons/nope")
    assert ei.value.code == "HA_NOT_FOUND"


async def test_supervisor_get_json_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-tok")
    resp = _FakeResp(status=500, body={"result": "error"}, content_type="application/json")
    with _patch_session(resp), pytest.raises(HAClientError) as ei:
        await supervisor_get_json("/addons")
    assert ei.value.code == "HA_HTTP_ERROR"


async def test_supervisor_get_json_bad_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-tok")

    class _BadJsonResp(_FakeResp):
        async def json(self, content_type: Any = None) -> Any:
            raise ValueError("not json")

    resp = _BadJsonResp(status=200, content_type="application/json", text_body="not json")
    with _patch_session(resp), pytest.raises(HAClientError) as ei:
        await supervisor_get_json("/addons")
    assert ei.value.code == "HA_BAD_RESPONSE"


async def test_supervisor_get_json_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-tok")
    big = "x" * 2048
    resp = _FakeResp(status=200, content_type="application/json", text_body=big)
    with _patch_session(resp), pytest.raises(HAClientError) as ei:
        await supervisor_get_json("/addons/self/info", max_bytes=1024)
    assert ei.value.code == "HA_RESPONSE_TOO_LARGE"


async def test_supervisor_get_json_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aiohttp

    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-tok")
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.get = MagicMock(side_effect=aiohttp.ClientError("conn refused"))
    with (
        patch("aiohttp.ClientSession", return_value=session),
        pytest.raises(HAClientError) as ei,
    ):
        await supervisor_get_json("/addons")
    assert ei.value.code == "HA_NETWORK"
