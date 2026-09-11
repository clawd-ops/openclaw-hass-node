"""End-to-end contract for ``system.run`` through GatewayClient._handle_invoke.

Real callers reach ``system.run`` through the gateway forwarding an approved
``systemRunPlan`` as a ``node.invoke.request`` event. This suite drives the
node's actual invoke entry point with that shape and asserts the terminal
``node.invoke.result`` frame the gateway would receive.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import patch as mock_patch

import pytest

from openclaw_node.config import NodeConfig
from openclaw_node.gateway_ws import GatewayClient
from openclaw_node.identity import generate_identity


async def _invoke(
    params: dict[str, Any], *, subprocess_result: subprocess.CompletedProcess[bytes] | None = None
) -> dict[str, Any]:
    with (
        pytest.MonkeyPatch.context() as ctx,
    ):
        ctx.setenv("OPENCLAW_ALLOWED_ROOTS", "/tmp")
        config = NodeConfig(
            addon_mode=False,
            gateway_url="wss://gateway.example.invalid/ws",
            pairing_token="",
            node_name="contract-fixture",
            hass_url="",
            hass_token="",
            supervisor_token="",
            data_dir=Path("/tmp/system-run-contract"),
        )
        client = GatewayClient(config=config, identity=generate_identity(), device_token="")
        socket = AsyncMock()
        completed = subprocess_result or subprocess.CompletedProcess(
            args=["true"], returncode=0, stdout=b"ok\n", stderr=b""
        )
        with mock_patch("subprocess.run", return_value=completed):
            await client._handle_invoke(
                socket,
                {
                    "id": "forwarded-plan",
                    "nodeId": "hass",
                    "command": "system.run",
                    "paramsJSON": json.dumps(params),
                },
            )
    frame = json.loads(socket.send.call_args.args[0])
    result: dict[str, Any] = frame["params"]
    return result


async def test_forwarded_plan_executes_and_returns_ok() -> None:
    """The gateway forwards the approved plan; the node executes and returns ok."""
    frame = await _invoke(
        {
            "command": ["echo", "hi"],
            "rawCommand": "echo hi",
            "cwd": "/tmp",
            "agentId": "agent",
            "sessionKey": "sess",
            "proposalId": "prop-1",
        },
        subprocess_result=subprocess.CompletedProcess(
            args=["echo", "hi"], returncode=0, stdout=b"hi\n", stderr=b""
        ),
    )
    assert frame["ok"] is True
    assert frame["payload"]["ok"] is True
    assert frame["payload"]["stdout"] == "hi\n"
    assert frame["payload"]["returncode"] == 0


async def test_missing_command_is_reported_as_command_failure() -> None:
    frame = await _invoke({})
    assert frame["ok"] is False
    assert frame["error"]["code"] == "MISSING_PARAM"


async def test_cwd_outside_allowed_roots_is_reported_as_command_failure() -> None:
    frame = await _invoke({"command": ["true"], "cwd": "/etc"})
    assert frame["ok"] is False
    assert frame["error"]["code"] == "PATH_NOT_ALLOWED"


async def test_raw_command_mismatch_is_reported_as_command_failure() -> None:
    frame = await _invoke({"command": ["true"], "rawCommand": "something else"})
    assert frame["ok"] is False
    assert frame["error"]["code"] == "RAW_COMMAND_MISMATCH"


async def test_admin_token_and_cmd_alias_are_no_longer_accepted() -> None:
    """The legacy shape (``cmd`` + ``admin_token``) must be refused post-#258."""
    frame = await _invoke({"cmd": ["true"], "admin_token": "anything"})
    assert frame["ok"] is False
    assert frame["error"]["code"] == "MISSING_PARAM"
