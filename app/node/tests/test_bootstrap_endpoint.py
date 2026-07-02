"""Tests for the /v1/bootstrap auto-token endpoint."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from aiohttp.web import Application, Request

from openclaw_node.config import NodeConfig
from openclaw_node.http_api import NodeRuntime, create_app

_AUTO_TOKEN = "auto-generated-token-abc123"
_EXPLICIT_TOKEN = "operator-set-token-xyz"


def _make_config(tmp_path: Path, token: str) -> NodeConfig:
    return NodeConfig(
        addon_mode=False,
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
    """Client with an auto-generated bootstrap token enabled."""
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


@pytest.mark.asyncio
async def test_bootstrap_returns_token_when_auto_generated(
    bootstrap_client: TestClient[Request, Application],
) -> None:
    """GET /v1/bootstrap returns the auto-generated token."""
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
