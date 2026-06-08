"""Tests for the bearer-token auth middleware on the local HTTP API."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from aiohttp.web import Application, Request

from openclaw_node.commands import dispatcher as _dispatcher
from openclaw_node.config import NodeConfig
from openclaw_node.http_api import NodeRuntime, create_app


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
async def authed_client(
    tmp_path: Path,
) -> AsyncGenerator[TestClient[Request, Application]]:
    """Client backed by a runtime that requires a bearer token."""
    runtime = NodeRuntime(_make_config(tmp_path, token="s3cret"))
    server = TestServer(create_app(runtime))
    client = TestClient[Request, Application](server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture
async def unauthed_client(
    tmp_path: Path,
) -> AsyncGenerator[TestClient[Request, Application]]:
    """Client with empty token — middleware must be a no-op (legacy dev path)."""
    runtime = NodeRuntime(_make_config(tmp_path, token=""))
    server = TestServer(create_app(runtime))
    client = TestClient[Request, Application](server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_empty_token_fails_closed(
    unauthed_client: TestClient[Request, Application],
) -> None:
    """With no local_api_token configured, non-public paths return 401."""
    response = await unauthed_client.post("/v1/commands/ping", json={"message": "hi"})
    assert response.status == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"
    data = await response.json()
    assert data["error"] == "NO_TOKEN_CONFIGURED"
    assert "local_api_token" in data["response"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/health", "/v1/health", "/v1/conversation/info"])
async def test_empty_token_public_paths_still_open(
    unauthed_client: TestClient[Request, Application], path: str
) -> None:
    """Health + conversation-info must work without a token (HA add-on probe)."""
    response = await unauthed_client.get(path)
    assert response.status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/health", "/v1/health", "/v1/conversation/info"])
async def test_unauthed_paths_still_open_when_token_set(
    authed_client: TestClient[Request, Application], path: str
) -> None:
    """Health + conversation info must serve even without an Authorization header.

    HA add-on health checks and HACS config-flow discovery must not require
    the user to configure the token first.
    """
    response = await authed_client.get(path)
    assert response.status == 200


@pytest.mark.asyncio
async def test_missing_authorization_returns_401(
    authed_client: TestClient[Request, Application],
) -> None:
    """Authed endpoint without an Authorization header returns 401 + WWW-Authenticate."""
    response = await authed_client.post("/v1/commands/ping", json={})
    assert response.status == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"
    data = await response.json()
    assert data["error"] == "UNAUTHORIZED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    [
        "Basic deadbeef",
        "Bearer ",
        "bearer",
        "",
    ],
)
async def test_malformed_authorization_returns_401(
    authed_client: TestClient[Request, Application], header: str
) -> None:
    """Non-bearer / empty Authorization headers are 401, not 403."""
    response = await authed_client.post(
        "/v1/commands/ping",
        json={},
        headers={"Authorization": header} if header else {},
    )
    assert response.status == 401


@pytest.mark.asyncio
async def test_wrong_bearer_returns_403(
    authed_client: TestClient[Request, Application],
) -> None:
    """A well-formed but mismatched bearer returns 403."""
    response = await authed_client.post(
        "/v1/commands/ping",
        json={},
        headers={"Authorization": "Bearer wrongtoken"},
    )
    assert response.status == 403
    data = await response.json()
    assert data["error"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_correct_bearer_passes_through(
    authed_client: TestClient[Request, Application],
) -> None:
    """A correct bearer token allows the request through to the handler."""
    response = await authed_client.post(
        "/v1/commands/ping",
        json={"message": "hi"},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status == 200


@pytest.mark.asyncio
async def test_command_dispatch_runs_async_handler(
    authed_client: TestClient[Request, Application],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/v1/commands/{cmd}`` must await async handlers via dispatch_async.

    Regression guard for Clawd-audit H2: every ``ha.*`` handler is async and
    previously raised AsyncHandlerError because the route called sync
    ``dispatch()``.
    """

    async def _async_echo(params: dict[str, object]) -> dict[str, object]:
        return {"echoed": params, "via": "async"}

    # Use monkeypatch.setitem so the registry is restored after the test, not
    # leaked into sibling tests that may register the same name.
    monkeypatch.setitem(_dispatcher._REGISTRY, "test.async_echo", _async_echo)
    response = await authed_client.post(
        "/v1/commands/test.async_echo",
        json={"x": 2},
        headers={"Authorization": "Bearer s3cret"},
    )
    data = await response.json()
    assert response.status == 200
    assert data["ok"] is True
    assert data["result"] == {"echoed": {"x": 2}, "via": "async"}
