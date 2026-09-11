"""Gateway-entry contract for ``system.run``.

Real callers reach ``system.run`` through the Gateway forwarding a
``node.invoke.request`` frame after the operator approves a canonical
``systemRunPlan``. This suite drives the node's actual Gateway-facing
invoke handler (``GatewayClient._handle_invoke``) end to end and asserts:

- a forwarded plan missing the approval envelope fails closed,
- a forwarded request whose argv/cwd disagrees with the stored plan
  fails closed,
- the ``timeoutMs`` field is honored (native wire contract), and
- the terminal ``node.invoke.result`` payload carries the
  ``success``/``exitCode``/``timedOut`` fields the exec tool parses.
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


def _plan(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "argv": ["echo", "hi"],
        "commandText": "echo hi",
    }
    base.update(overrides)
    return base


def _authorized_params(**overrides: Any) -> dict[str, Any]:
    """Envelope the Gateway forwards after an operator approves a plan."""
    argv = overrides.pop("command", ["echo", "hi"])
    raw = overrides.pop("rawCommand", "echo hi")
    plan_overrides = overrides.pop("systemRunPlan", None)
    plan = _plan(argv=list(argv), commandText=raw)
    if plan_overrides is not None:
        plan.update(plan_overrides)
    params: dict[str, Any] = {
        "command": list(argv),
        "rawCommand": raw,
        "systemRunPlan": plan,
        "runId": "run-uuid-1",
        "approvalDecision": "allow-once",
        "approvalSource": None,
        "approved": True,
    }
    params.update(overrides)
    return params


async def _invoke(
    params: dict[str, Any],
    *,
    subprocess_result: subprocess.CompletedProcess[bytes] | None = None,
    subprocess_side_effect: Any = None,
) -> dict[str, Any]:
    with pytest.MonkeyPatch.context() as ctx:
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
            args=["echo", "hi"], returncode=0, stdout=b"hi\n", stderr=b""
        )
        patcher = (
            mock_patch("subprocess.run", side_effect=subprocess_side_effect)
            if subprocess_side_effect is not None
            else mock_patch("subprocess.run", return_value=completed)
        )
        with patcher:
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


# ---------------------------------------------------------------------------
# fail-closed: authorization envelope missing
# ---------------------------------------------------------------------------


async def test_gateway_forward_without_plan_is_refused() -> None:
    """A ``system.run`` invoke with no ``systemRunPlan`` fails closed."""
    frame = await _invoke(
        {
            "command": ["echo", "sneak"],
            "rawCommand": "echo sneak",
            "cwd": "/tmp",
            "agentId": "agent",
            "sessionKey": "sess",
        }
    )
    assert frame["ok"] is False
    assert frame["error"]["code"] == "UNAUTHORIZED"


async def test_gateway_forward_without_approval_signal_is_refused() -> None:
    frame = await _invoke(
        {
            "command": ["echo", "hi"],
            "rawCommand": "echo hi",
            "systemRunPlan": _plan(),
            "runId": "run-uuid-1",
        }
    )
    assert frame["ok"] is False
    assert frame["error"]["code"] == "UNAUTHORIZED"


async def test_gateway_forward_without_run_id_is_refused() -> None:
    frame = await _invoke(
        {
            "command": ["echo", "hi"],
            "rawCommand": "echo hi",
            "systemRunPlan": _plan(),
            "approvalDecision": "allow-once",
        }
    )
    assert frame["ok"] is False
    assert frame["error"]["code"] == "UNAUTHORIZED"


# ---------------------------------------------------------------------------
# fail-closed: stored plan overrides forwarded fields
# ---------------------------------------------------------------------------


async def test_argv_diverging_from_stored_plan_is_refused() -> None:
    """A forward whose argv disagrees with the stored plan is refused."""
    frame = await _invoke(
        _authorized_params(
            command=["rm", "-rf", "/"],
            rawCommand="rm -rf /",
            systemRunPlan={"argv": ["echo", "hi"], "commandText": "echo hi"},
        )
    )
    assert frame["ok"] is False
    assert frame["error"]["code"] == "PLAN_MISMATCH"


async def test_cwd_diverging_from_stored_plan_is_refused() -> None:
    frame = await _invoke(
        _authorized_params(
            command=["echo", "hi"],
            rawCommand="echo hi",
            cwd="/tmp",
            systemRunPlan={"cwd": "/var/tmp/other"},
        )
    )
    assert frame["ok"] is False
    assert frame["error"]["code"] == "PLAN_MISMATCH"


async def test_env_credential_shaped_key_is_refused() -> None:
    frame = await _invoke(
        _authorized_params(env={"OPENCLAW_ADMIN_TOKEN": "please"}),
    )
    assert frame["ok"] is False
    assert frame["error"]["code"] == "INVALID_PARAM"


# ---------------------------------------------------------------------------
# happy path: forwarded plan executes and returns the native wire payload
# ---------------------------------------------------------------------------


async def test_forwarded_plan_executes_and_returns_native_payload() -> None:
    frame = await _invoke(
        _authorized_params(),
        subprocess_result=subprocess.CompletedProcess(
            args=["echo", "hi"], returncode=0, stdout=b"hi\n", stderr=b""
        ),
    )
    assert frame["ok"] is True
    payload = frame["payload"]
    # Native exec-tool parser reads these payload fields directly.
    assert payload["success"] is True
    assert payload["exitCode"] == 0
    assert payload["timedOut"] is False
    assert payload["stdout"] == "hi\n"
    assert payload["stderr"] == ""


async def test_forwarded_plan_nonzero_exit_reports_success_false() -> None:
    frame = await _invoke(
        _authorized_params(
            command=["false"],
            rawCommand="false",
            systemRunPlan={"argv": ["false"], "commandText": "false"},
        ),
        subprocess_result=subprocess.CompletedProcess(
            args=["false"], returncode=1, stdout=b"", stderr=b"boom\n"
        ),
    )
    assert frame["ok"] is True
    assert frame["payload"]["success"] is False
    assert frame["payload"]["exitCode"] == 1


# ---------------------------------------------------------------------------
# timeoutMs is the native wire field and is honored
# ---------------------------------------------------------------------------


async def test_timeout_ms_is_forwarded_to_subprocess() -> None:
    """The forwarded ``timeoutMs`` sets the subprocess timeout in seconds."""
    captured: list[float] = []

    def _fake(*_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured.append(kwargs["timeout"])
        return subprocess.CompletedProcess(
            args=["echo", "hi"], returncode=0, stdout=b"hi\n", stderr=b""
        )

    frame = await _invoke(
        _authorized_params(timeoutMs=2500),
        subprocess_side_effect=_fake,
    )
    assert frame["ok"] is True
    assert captured == [pytest.approx(2.5)]


async def test_seconds_timeout_field_is_rejected() -> None:
    """The retired seconds-based ``timeout`` field is refused."""
    frame = await _invoke(_authorized_params(timeout=30))
    assert frame["ok"] is False
    assert frame["error"]["code"] == "INVALID_PARAM"


async def test_subprocess_timeout_returns_timed_out_payload() -> None:
    """A subprocess timeout returns the native ``timedOut`` payload, not an invoke error."""
    frame = await _invoke(
        _authorized_params(timeoutMs=1000),
        subprocess_side_effect=subprocess.TimeoutExpired(["echo", "hi"], 1.0),
    )
    assert frame["ok"] is True
    payload = frame["payload"]
    assert payload["timedOut"] is True
    assert payload["success"] is False
    assert payload["exitCode"] is None
