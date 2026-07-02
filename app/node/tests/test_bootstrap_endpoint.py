"""Tests for the /v1/bootstrap security hardening (one-shot + time-window + network origin)."""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from aiohttp.web import Application, Request

from openclaw_node.config import NodeConfig
from openclaw_node.http_api import (
    BOOTSTRAP_WINDOW_SECONDS,
    NodeRuntime,
    _bootstrap_consumed_path,
    create_app,
)

_AUTO_TOKEN = "auto-generated-token-abc123"
_EXPLICIT_TOKEN = "operator-set-token-xyz"


def _make_config(tmp_path: Path, *, token: str = _AUTO_TOKEN, addon_mode: bool = False) -> NodeConfig:
    return NodeConfig(
        addon_mode=addon_mode,
        gateway_url="wss://gw.test/ws",
        pairing_token="",
        node_name="test-node",
        hass_url="",
        hass_token="",
        supervisor_token="",
        data_dir=tmp_path,
        local_api_token=token,
    )


@pytest_asyncio.fixture
async def bootstrap_client(
    tmp_path: Path,
) -> AsyncGenerator[TestClient[Request, Application]]:
    """Client with an auto-generated bootstrap token enabled (standalone mode)."""
    config = _make_config(tmp_path, token=_AUTO_TOKEN)
    runtime = NodeRuntime(config, bootstrap_token=_AUTO_TOKEN)
    server = TestServer(create_app(runtime))
    client = TestClient[Request, Application](server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture
async def no_bootstrap_client(
    tmp_path: Path,
) -> AsyncGenerator[TestClient[Request, Application]]:
    """Client with an operator-supplied token — bootstrap endpoint is disabled."""
    config = _make_config(tmp_path, token=_EXPLICIT_TOKEN)
    runtime = NodeRuntime(config, bootstrap_token="")
    server = TestServer(create_app(runtime))
    client = TestClient[Request, Application](server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Original smoke tests (kept + adapted)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_returns_token_when_auto_generated(
    bootstrap_client: TestClient[Request, Application],
) -> None:
    """GET /v1/bootstrap returns the auto-generated token on first call."""
    resp = await bootstrap_client.get("/v1/bootstrap")
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert data["token"] == _AUTO_TOKEN


@pytest.mark.asyncio
async def test_bootstrap_requires_no_auth_header(
    bootstrap_client: TestClient[Request, Application],
) -> None:
    """Bootstrap endpoint is in the unauthed allowlist — no bearer required."""
    resp = await bootstrap_client.get("/v1/bootstrap")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_bootstrap_disabled_when_explicit_token(
    no_bootstrap_client: TestClient[Request, Application],
) -> None:
    """When the operator supplied the token, /v1/bootstrap returns 404 disabled."""
    resp = await no_bootstrap_client.get("/v1/bootstrap")
    assert resp.status == 404
    data = await resp.json()
    assert data["ok"] is False
    assert data["error"] == "BOOTSTRAP_DISABLED"


@pytest.mark.asyncio
async def test_bootstrap_does_not_expose_explicit_token(
    no_bootstrap_client: TestClient[Request, Application],
) -> None:
    """The explicit operator token must never appear in the bootstrap response body."""
    resp = await no_bootstrap_client.get("/v1/bootstrap")
    body = await resp.text()
    assert _EXPLICIT_TOKEN not in body


@pytest.mark.asyncio
async def test_bootstrap_cache_control_no_store(
    bootstrap_client: TestClient[Request, Application],
) -> None:
    """Bootstrap response must include Cache-Control: no-store."""
    resp = await bootstrap_client.get("/v1/bootstrap")
    assert resp.headers.get("Cache-Control") == "no-store"


# ---------------------------------------------------------------------------
# Layer 2: One-shot semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_one_shot_second_request_returns_410(
    tmp_path: Path,
) -> None:
    """Second GET /v1/bootstrap returns 410 BOOTSTRAP_CONSUMED."""
    config = _make_config(tmp_path)
    runtime = NodeRuntime(config, bootstrap_token=_AUTO_TOKEN)
    server = TestServer(create_app(runtime))
    client: TestClient[Request, Application] = TestClient(server)
    await client.start_server()
    try:
        first = await client.get("/v1/bootstrap")
        assert first.status == 200

        second = await client.get("/v1/bootstrap")
        assert second.status == 410
        data = await second.json()
        assert data["error"] == "BOOTSTRAP_CONSUMED"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bootstrap_consumed_marker_written_after_first_fetch(
    tmp_path: Path,
) -> None:
    """A consumed marker file is written to data_dir after the first fetch."""
    config = _make_config(tmp_path)
    runtime = NodeRuntime(config, bootstrap_token=_AUTO_TOKEN)
    server = TestServer(create_app(runtime))
    client: TestClient[Request, Application] = TestClient(server)
    await client.start_server()
    try:
        consumed_path = _bootstrap_consumed_path(config)
        assert not consumed_path.exists()

        resp = await client.get("/v1/bootstrap")
        assert resp.status == 200
        assert consumed_path.exists()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bootstrap_token_cleared_from_memory_after_first_fetch(
    tmp_path: Path,
) -> None:
    """runtime.bootstrap_token is emptied after the token is served."""
    config = _make_config(tmp_path)
    runtime = NodeRuntime(config, bootstrap_token=_AUTO_TOKEN)
    server = TestServer(create_app(runtime))
    client: TestClient[Request, Application] = TestClient(server)
    await client.start_server()
    try:
        await client.get("/v1/bootstrap")
        assert runtime.bootstrap_token == ""
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bootstrap_consumed_if_marker_pre_exists(
    tmp_path: Path,
) -> None:
    """If the consumed marker already exists on disk, the endpoint returns 410 immediately."""
    config = _make_config(tmp_path)
    consumed_path = _bootstrap_consumed_path(config)
    consumed_path.parent.mkdir(parents=True, exist_ok=True)
    consumed_path.write_text("pre-existing\n", encoding="utf-8")

    runtime = NodeRuntime(config, bootstrap_token=_AUTO_TOKEN)
    server = TestServer(create_app(runtime))
    client: TestClient[Request, Application] = TestClient(server)
    await client.start_server()
    try:
        resp = await client.get("/v1/bootstrap")
        assert resp.status == 410
        data = await resp.json()
        assert data["error"] == "BOOTSTRAP_CONSUMED"
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Layer 3: Time-window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_expired_after_window(
    tmp_path: Path,
) -> None:
    """Requests after BOOTSTRAP_WINDOW_SECONDS returns 410 BOOTSTRAP_EXPIRED."""
    config = _make_config(tmp_path)
    runtime = NodeRuntime(config, bootstrap_token=_AUTO_TOKEN)
    # Simulate a startup time far in the past to expire the window.
    runtime.startup_time = time.monotonic() - (BOOTSTRAP_WINDOW_SECONDS + 1)

    server = TestServer(create_app(runtime))
    client: TestClient[Request, Application] = TestClient(server)
    await client.start_server()
    try:
        resp = await client.get("/v1/bootstrap")
        assert resp.status == 410
        data = await resp.json()
        assert data["error"] == "BOOTSTRAP_EXPIRED"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bootstrap_within_window_succeeds(
    tmp_path: Path,
) -> None:
    """Requests before the window closes still return 200."""
    config = _make_config(tmp_path)
    runtime = NodeRuntime(config, bootstrap_token=_AUTO_TOKEN)
    # Ensure startup_time is very recent (within window).
    runtime.startup_time = time.monotonic()

    server = TestServer(create_app(runtime))
    client: TestClient[Request, Application] = TestClient(server)
    await client.start_server()
    try:
        resp = await client.get("/v1/bootstrap")
        assert resp.status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bootstrap_expired_does_not_write_consumed_marker(
    tmp_path: Path,
) -> None:
    """An expired-window rejection must not write the consumed marker."""
    config = _make_config(tmp_path)
    runtime = NodeRuntime(config, bootstrap_token=_AUTO_TOKEN)
    runtime.startup_time = time.monotonic() - (BOOTSTRAP_WINDOW_SECONDS + 1)

    server = TestServer(create_app(runtime))
    client: TestClient[Request, Application] = TestClient(server)
    await client.start_server()
    try:
        await client.get("/v1/bootstrap")
        assert not _bootstrap_consumed_path(config).exists()
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Layer 1: Network-origin check (addon_mode only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_addon_mode_rejects_non_supervisor_ip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In addon_mode, a source IP outside the Supervisor network returns 404."""
    config = _make_config(tmp_path, addon_mode=True)
    runtime = NodeRuntime(config, bootstrap_token=_AUTO_TOKEN)

    server = TestServer(create_app(runtime))
    client: TestClient[Request, Application] = TestClient(server)
    await client.start_server()
    try:
        # TestClient sends from 127.0.0.1, which is NOT in 172.30.32.0/23
        resp = await client.get("/v1/bootstrap")
        assert resp.status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bootstrap_standalone_mode_accepts_loopback(
    bootstrap_client: TestClient[Request, Application],
) -> None:
    """In standalone mode (addon_mode=False), loopback callers are accepted."""
    # bootstrap_client fixture uses addon_mode=False; TestClient sends from 127.0.0.1
    resp = await bootstrap_client.get("/v1/bootstrap")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_bootstrap_addon_mode_supervisor_ip_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In addon_mode, a Supervisor-network source IP passes the origin check."""
    from openclaw_node import http_api

    # Monkeypatch _is_supervisor_network to simulate a Supervisor-network caller.
    monkeypatch.setattr(http_api, "_is_supervisor_network", lambda _remote: True)

    config = _make_config(tmp_path, addon_mode=True)
    runtime = NodeRuntime(config, bootstrap_token=_AUTO_TOKEN)
    server = TestServer(create_app(runtime))
    client: TestClient[Request, Application] = TestClient(server)
    await client.start_server()
    try:
        resp = await client.get("/v1/bootstrap")
        assert resp.status == 200
        data = await resp.json()
        assert data["token"] == _AUTO_TOKEN
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Order of precedence: window check before one-shot check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_expired_before_consumed_returns_expired(
    tmp_path: Path,
) -> None:
    """Expired window is checked before consumed marker; expired wins."""
    config = _make_config(tmp_path)
    # Both conditions are true: window expired AND marker pre-exists
    consumed_path = _bootstrap_consumed_path(config)
    consumed_path.parent.mkdir(parents=True, exist_ok=True)
    consumed_path.write_text("consumed\n", encoding="utf-8")

    runtime = NodeRuntime(config, bootstrap_token=_AUTO_TOKEN)
    runtime.startup_time = time.monotonic() - (BOOTSTRAP_WINDOW_SECONDS + 1)

    server = TestServer(create_app(runtime))
    client: TestClient[Request, Application] = TestClient(server)
    await client.start_server()
    try:
        resp = await client.get("/v1/bootstrap")
        assert resp.status == 410
        data = await resp.json()
        assert data["error"] == "BOOTSTRAP_EXPIRED"
    finally:
        await client.close()
