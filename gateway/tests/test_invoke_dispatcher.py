"""Tests for openclaw_gateway.invoke_dispatcher."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from openclaw_gateway.invoke_dispatcher import InvokeDispatcher, InvokeDispatcherError


async def test_invoke_completes_when_result_arrives() -> None:
    sent: list[dict[str, Any]] = []

    async def _send(req: dict[str, Any]) -> None:
        sent.append(req)

    d = InvokeDispatcher(send=_send, timeout_s=2.0)

    async def _later() -> None:
        await asyncio.sleep(0.01)
        d.handle_result({"invokeId": sent[0]["invokeId"], "ok": True, "result": {"x": 1}})

    task = asyncio.create_task(_later())
    out = await d.invoke("ha.list_states", {"domain": "light"})
    await task
    assert out["ok"] is True
    assert out["result"] == {"x": 1}
    assert "invokeId" not in out
    assert sent[0]["command"] == "ha.list_states"
    assert sent[0]["params"] == {"domain": "light"}


async def test_invoke_times_out() -> None:
    async def _send(req: dict[str, Any]) -> None:
        return None

    d = InvokeDispatcher(send=_send, timeout_s=0.05)
    with pytest.raises(InvokeDispatcherError) as ei:
        await d.invoke("ha.get_state", {"entity_id": "x"})
    assert ei.value.code == "TIMEOUT"
    assert d.pending_count == 0


async def test_invoke_send_failure() -> None:
    async def _bad(req: dict[str, Any]) -> None:
        raise RuntimeError("ws gone")

    d = InvokeDispatcher(send=_bad)
    with pytest.raises(InvokeDispatcherError) as ei:
        await d.invoke("ha.list_states", {})
    assert ei.value.code == "SEND_FAILED"
    assert d.pending_count == 0


async def test_handle_result_unknown_id_is_safe() -> None:
    async def _send(req: dict[str, Any]) -> None:
        return None

    d = InvokeDispatcher(send=_send)
    d.handle_result({"invokeId": "nope"})


async def test_handle_result_idempotent_on_completed_future() -> None:
    sent: list[dict[str, Any]] = []

    async def _send(req: dict[str, Any]) -> None:
        sent.append(req)

    d = InvokeDispatcher(send=_send, timeout_s=2.0)

    async def _twice() -> None:
        await asyncio.sleep(0.01)
        rid = sent[0]["invokeId"]
        d.handle_result({"invokeId": rid, "ok": True, "result": "first"})
        d.handle_result({"invokeId": rid, "ok": True, "result": "second"})

    task = asyncio.create_task(_twice())
    out = await d.invoke("ha.x", {})
    await task
    assert out["result"] == "first"


async def test_cancel_all_fails_pending_as_disconnected() -> None:
    sent: list[dict[str, Any]] = []

    async def _send(req: dict[str, Any]) -> None:
        sent.append(req)

    d = InvokeDispatcher(send=_send, timeout_s=5.0)
    task = asyncio.create_task(d.invoke("ha.x", {}))
    await asyncio.sleep(0.01)
    assert d.pending_count == 1
    d.cancel_all("server gone")
    with pytest.raises(InvokeDispatcherError) as ei:
        await task
    assert ei.value.code == "DISCONNECTED"
    assert d.pending_count == 0


async def test_cancel_all_safe_with_no_pending() -> None:
    async def _send(req: dict[str, Any]) -> None:
        return None

    d = InvokeDispatcher(send=_send)
    d.cancel_all()  # must not raise
