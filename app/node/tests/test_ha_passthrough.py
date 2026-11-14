"""ha.ws / ha.rest: pipeline order, roles, job approvals, path attacks and audit.

Every refusal test also proves zero Home Assistant requests: both client entry
points are mocked and asserted never awaited.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openclaw_node import ha_client
from openclaw_node.authz import forbidden_for_role
from openclaw_node.caller import UNTRUSTED, Caller
from openclaw_node.commands import ha_passthrough
from openclaw_node.commands.config_mutation import approval_bind
from openclaw_node.commands.dispatcher import dispatch_async
from openclaw_node.config import IdentityConfig
from openclaw_node.ha_client import HAClientError

OWNER = Caller(role="super_admin", actor_id="owner-id")
HOUSEHOLD = Caller(
    role="user", actor_id="adult-id", forbidden=forbidden_for_role(IdentityConfig(), "user")
)
HOUSEHOLD_ADMIN = Caller(
    role="admin", actor_id="adult-admin", forbidden=forbidden_for_role(IdentityConfig(), "admin")
)
OPERATOR = Caller.operator("gateway-invoke")


@dataclass
class Env:
    ws: AsyncMock
    rest: AsyncMock
    audit: Path

    def assert_no_requests(self) -> None:
        self.ws.assert_not_called()
        self.rest.assert_not_called()

    def lines(self) -> list[dict[str, Any]]:
        if not self.audit.exists():
            return []
        return [json.loads(line) for line in self.audit.read_text().splitlines()]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Env]:
    ws = AsyncMock(return_value=[{"ok": True}])
    rest = AsyncMock(return_value={"state": "on"})
    monkeypatch.setattr(ha_client, "ha_ws_call", ws)
    monkeypatch.setattr(ha_client, "ha_rest", rest)
    monkeypatch.setattr(ha_passthrough, "load_config", lambda: SimpleNamespace(data_dir=tmp_path))
    yield Env(ws, rest, tmp_path / "ha-audit.jsonl")
    log = logging.getLogger("openclaw_node.ha_audit")
    for handler in list(log.handlers):
        log.removeHandler(handler)
        handler.close()


async def call(command: str, params: dict[str, Any], caller: Caller = OWNER) -> dict[str, Any]:
    return await dispatch_async(command, params, caller=caller)


def approved(command: str, params: dict[str, Any], *, exp_in: int = 600) -> dict[str, Any]:
    marker = {
        "id": str(uuid.uuid4()),
        "exp": int(time.time()) + exp_in,
        "bind": approval_bind(command, "", params),
    }
    return {**params, "_openclaw_approval": marker}


def svc(domain: str, service: str, **extra: Any) -> dict[str, Any]:
    return {"type": "call_service", "payload": {"domain": domain, "service": service, **extra}}


def remove(entity_id: str) -> dict[str, Any]:
    return {"type": "config/entity_registry/remove", "payload": {"entity_id": entity_id}}


# --- allowed paths ---------------------------------------------------------


@pytest.mark.parametrize("caller", [HOUSEHOLD, HOUSEHOLD_ADMIN, OWNER, OPERATOR])
@pytest.mark.parametrize(
    ("params", "kind"),
    [
        (svc("light", "turn_on", target={"entity_id": "light.kitchen"}), "control"),
        ({"type": "get_states"}, "diagnose"),
        (svc("homeassistant", "restart"), "fix"),
        (svc("hassio", "addon_restart", addon="45df7312_zigbee2mqtt"), "fix"),
    ],
)
async def test_control_diagnose_and_fix_run_for_every_verified_caller(
    env: Env, caller: Caller, params: dict[str, Any], kind: str
) -> None:
    result = await call("ha.ws", params, caller)

    assert result == {"ok": True, "kind": kind, "result": [{"ok": True}]}
    env.ws.assert_awaited_once_with(params["type"], params.get("payload"))
    assert env.lines()[0]["result"] == "ok"


async def test_rest_reads_and_service_calls(env: Env) -> None:
    read = await call("ha.rest", {"method": "GET", "path": "/api/states/light.kitchen"}, HOUSEHOLD)
    turn_on = await call(
        "ha.rest",
        {"method": "POST", "path": "/api/services/light/turn_on", "body": {"entity_id": "light.a"}},
        HOUSEHOLD,
    )
    history = await call(
        "ha.rest",
        {
            "method": "GET",
            "path": "/api/history/period/2026-11-13T00:00:00+00:00",
            "query": {"filter_entity_id": "light.a", "minimal_response": 1},
        },
        HOUSEHOLD,
    )

    assert (read["kind"], turn_on["kind"], history["kind"]) == ("diagnose", "control", "diagnose")
    assert env.rest.await_args_list[0].args == ("GET", "/api/states/light.kitchen", None, None)
    assert env.rest.await_args_list[1].args[3] == {"entity_id": "light.a"}
    assert env.rest.await_args_list[2].args[2] == {
        "filter_entity_id": "light.a",
        "minimal_response": 1,
    }


async def test_a_path_that_merely_contains_supervisor_in_an_entity_id_is_fine(env: Env) -> None:
    path = "/api/states/update.home_assistant_supervisor_update"
    result = await call("ha.rest", {"method": "GET", "path": path}, HOUSEHOLD)
    assert result["ok"] is True


async def test_guest_gets_control_and_diagnose_only(env: Env) -> None:
    assert (await call("ha.ws", {"type": "get_states"}, UNTRUSTED))["ok"] is True
    refused = await call("ha.ws", svc("homeassistant", "restart"), UNTRUSTED)
    assert refused["error"] == "PERMISSION_DENIED"
    assert env.ws.await_count == 1


# --- refusals: always before any request -----------------------------------

REFUSED_FOR_HOUSEHOLD: list[tuple[str, dict[str, Any], str]] = [
    (
        "ha.ws",
        {"type": "config/entity_registry/update", "payload": {"entity_id": "a.b"}},
        "configure",
    ),
    ("ha.ws", {"type": "lovelace/config/save", "payload": {"config": {}}}, "configure"),
    ("ha.ws", remove("light.old"), "admin"),
    ("ha.ws", {"type": "execute_script", "payload": {"sequence": []}}, "admin"),
    ("ha.ws", svc("update", "install", entity_id="update.core"), "admin"),
    ("ha.ws", svc("hassio", "host_reboot"), "admin"),
    ("ha.ws", svc("hassio", "addon_update", addon="x"), "admin"),
    ("ha.ws", {"type": "supervisor/api", "payload": {"endpoint": "/addons/x/install"}}, "admin"),
    ("ha.ws", {"type": "backup/remove", "payload": {"backup_id": "abc"}}, "admin"),
    ("ha.ws", {"type": "config/auth/delete", "payload": {"user_id": "u"}}, "admin"),
    (
        "ha.rest",
        {"method": "POST", "path": "/api/config/automation/config/new", "body": {}},
        "configure",
    ),
    ("ha.rest", {"method": "DELETE", "path": "/api/config/automation/config/12"}, "admin"),
    ("ha.rest", {"method": "POST", "path": "/api/services/update/install", "body": {}}, "admin"),
]


@pytest.mark.parametrize("caller", [HOUSEHOLD, HOUSEHOLD_ADMIN])
@pytest.mark.parametrize(("command", "params", "kind"), REFUSED_FOR_HOUSEHOLD)
async def test_household_is_refused_above_fix(
    env: Env, caller: Caller, command: str, params: dict[str, Any], kind: str
) -> None:
    result = await call(command, params, caller)

    assert result["ok"] is False
    assert result["error"] == "PERMISSION_DENIED"
    assert "owner" in result["message"]
    env.assert_no_requests()
    entry = env.lines()[0]
    assert (entry["result"], entry["kind"], entry["reason"]) == (
        "refused",
        kind,
        "PERMISSION_DENIED",
    )


async def test_misspoken_household_request_is_refused_and_never_executed(env: Env) -> None:
    """ "Rename the lamp" must not become a config change, even with an approval attached."""
    rename = {
        "type": "config/entity_registry/update",
        "payload": {"entity_id": "light.lamp", "name": "x"},
    }

    plain = await call("ha.ws", rename, HOUSEHOLD)
    as_job = await call("ha.ws", approved("ha.ws", {"requests": [rename]}), HOUSEHOLD)

    assert plain["error"] == as_job["error"] == "PERMISSION_DENIED"
    env.assert_no_requests()
    assert all(e["kind"] == "configure" and e["result"] == "refused" for e in env.lines())


async def test_hard_deny_inside_a_household_job_is_refused_with_a_valid_approval(env: Env) -> None:
    job = approved("ha.ws", {"requests": [svc("light", "turn_on"), svc("update", "install")]})

    result = await call("ha.ws", job, HOUSEHOLD)

    assert result["error"] == "PERMISSION_DENIED"
    env.assert_no_requests()


@pytest.mark.parametrize(
    ("caller", "params"),
    [
        (OWNER, remove("light.old")),
        (OPERATOR, {"type": "config/entity_registry/update", "payload": {"entity_id": "a.b"}}),
        (OWNER, svc("update", "install", entity_id="update.core")),
        (OWNER, {"type": "supervisor/api", "payload": {"endpoint": "/info"}}),
    ],
)
async def test_approval_needing_single_request_must_be_resubmitted_as_a_job(
    env: Env, caller: Caller, params: dict[str, Any]
) -> None:
    result = await call("ha.ws", params, caller)

    assert result["error"] == "APPROVAL_REQUIRED"
    assert "job" in result["message"]
    env.assert_no_requests()


async def test_owner_configure_needs_no_approval(env: Env) -> None:
    params = {
        "type": "config/entity_registry/update",
        "payload": {"entity_id": "light.a", "name": "A"},
    }
    assert (await call("ha.ws", params, OWNER))["kind"] == "configure"


# --- job approval ----------------------------------------------------------


async def test_one_approval_covers_n_requests(env: Env) -> None:
    ids = [f"light.old_{i}" for i in range(21)]
    job = approved("ha.ws", {"requests": [remove(i) for i in ids]})

    result = await call("ha.ws", job, OWNER)

    assert result["ok"] is True
    assert len(result["results"]) == 21
    assert env.ws.await_count == 21
    entries = env.lines()
    assert {e["job"] for e in entries} == {result["job"]}
    assert len({e["approval"] for e in entries}) == 1
    assert all(e["result"] == "ok" and e["kind"] == "admin" for e in entries)


async def test_replaying_an_approval_fails(env: Env) -> None:
    job = approved("ha.ws", {"requests": [remove("light.a")]})
    replay = json.loads(json.dumps(job))

    assert (await call("ha.ws", job, OWNER))["ok"] is True
    again = await call("ha.ws", replay, OWNER)

    assert again["error"] == "APPROVAL_INVALID"
    assert "already used" in again["message"]
    assert env.ws.await_count == 1


async def test_approval_for_one_request_set_cannot_run_another(env: Env) -> None:
    approved_job = approved("ha.ws", {"requests": [remove("light.a")]})
    swapped = {
        "requests": [remove("light.b")],
        "_openclaw_approval": approved_job["_openclaw_approval"],
    }
    extended = {
        "requests": [remove("light.a"), remove("light.b")],
        "_openclaw_approval": approved_job["_openclaw_approval"],
    }

    for params in (swapped, extended):
        result = await call("ha.ws", params, OWNER)
        assert result["error"] == "APPROVAL_INVALID"
        assert "does not match" in result["message"]
    env.assert_no_requests()


async def test_a_marker_for_ha_ws_does_not_authorize_ha_rest(env: Env) -> None:
    rest_job = {"requests": [{"method": "DELETE", "path": "/api/config/automation/config/12"}]}
    marker = approved("ha.ws", rest_job)["_openclaw_approval"]

    result = await call("ha.rest", {**rest_job, "_openclaw_approval": marker}, OWNER)

    assert result["error"] == "APPROVAL_INVALID"
    env.assert_no_requests()


async def test_job_without_or_with_expired_approval_is_refused_whole(env: Env) -> None:
    job = {"requests": [svc("light", "turn_on"), svc("update", "install")]}

    missing = await call("ha.ws", job, OWNER)
    expired = await call("ha.ws", approved("ha.ws", job, exp_in=-5), OWNER)

    assert missing["error"] == "PROPOSAL_REQUIRED"
    assert expired["error"] == "APPROVAL_INVALID"
    env.assert_no_requests()  # the harmless first request did not run either


async def test_owner_approval_runs_a_job_with_a_hard_deny_item(env: Env) -> None:
    job = approved("ha.ws", {"requests": [svc("light", "turn_on"), svc("update", "install")]})

    result = await call("ha.ws", job, OWNER)

    assert result["ok"] is True
    assert [e["hard"] for e in env.lines()] == [False, True]


async def test_operator_job_with_approval_can_configure(env: Env) -> None:
    rename = {"type": "config/entity_registry/update", "payload": {"entity_id": "a.b"}}
    assert (await call("ha.ws", approved("ha.ws", {"requests": [rename]}), OPERATOR))["ok"] is True


async def test_unneeded_marker_is_ignored_and_never_forwarded(env: Env) -> None:
    job = approved("ha.ws", {"requests": [svc("light", "turn_on")]})
    marker = job["_openclaw_approval"]

    assert (await call("ha.ws", job, HOUSEHOLD))["ok"] is True

    assert "_openclaw_approval" not in json.dumps(env.ws.await_args_list[0].args)
    assert all(e["approval"] is None for e in env.lines())
    assert marker["id"] not in env.audit.read_text()


async def test_job_stops_at_the_first_failure(env: Env) -> None:
    env.ws.side_effect = [[1], HAClientError("HA_NOT_FOUND", "no such entity"), [3]]
    job = approved("ha.ws", {"requests": [remove("a.1"), remove("a.2"), remove("a.3")]})

    result = await call("ha.ws", job, OWNER)

    assert result["ok"] is False
    assert result["error"] == "JOB_FAILED"
    assert "HA_NOT_FOUND" in result["message"]
    assert [r.get("error") for r in result["results"]] == [None, "HA_NOT_FOUND", "SKIPPED"]
    assert env.ws.await_count == 2
    assert [e["result"] for e in env.lines()] == ["ok", "error", "skipped"]


@pytest.mark.parametrize(
    ("params", "needle"),
    [
        ({"requests": []}, "1 to 100"),
        ({"requests": "nope"}, "1 to 100"),
        ({"requests": [{"type": "get_states"}] * 101}, "1 to 100"),
        ({"requests": [{"type": "get_states"}, 7]}, "request 1: each request must be an object"),
        ({"requests": [{"type": "get_states"}, {"type": "Bad Type"}]}, "request 1: type must be"),
        ({"requests": [{"type": "get_states"}], "type": "get_states"}, "only 'requests'"),
    ],
)
async def test_malformed_jobs_are_refused(env: Env, params: dict[str, Any], needle: str) -> None:
    result = await call("ha.ws", params, OWNER)

    assert result["error"] == "INVALID_PARAM"
    assert needle in result["message"]
    env.assert_no_requests()


# --- must-fix 1: reserved WS keys -----------------------------------------


@pytest.mark.parametrize(
    "payload",
    [{"id": 7}, {"type": "supervisor/api"}, {"id": 2, "type": "supervisor/api", "endpoint": "/x"}],
)
async def test_payload_cannot_override_id_or_type(env: Env, payload: dict[str, Any]) -> None:
    single = await call("ha.ws", {"type": "config/area_registry/list", "payload": payload})
    job = await call(
        "ha.ws", {"requests": [{"type": "get_states"}, {"type": "get_states", "payload": payload}]}
    )

    assert single["error"] == job["error"] == "INVALID_PARAM"
    assert "reserved" in single["message"]
    assert job["message"].startswith("request 1:")
    env.assert_no_requests()


# --- must-fix 2: REST path corpus -----------------------------------------

BAD_PATHS = [
    "http://evil.example/api/states",
    "https://evil.example/api/states",
    "//evil.example/api/states",
    "api/states",
    "/states",
    "/auth/token",
    "/api",
    "/api/",
    "/api/states#frag",
    "/api/states?x=1",
    "/api/states/..",
    "/api/./states",
    "/api/states/.../x",
    "/api/states/%2e%2e",
    "/api/states/%2E%2E/x",
    "/api/states/..%2f..%2fhassio",
    "/api/%252e%252e/states",
    "/api/states%2fx",
    "/api/states\\..\\x",
    "/api\\hassio/x",
    "/api//states",
    "/api/states/",
    "/api/states/\n",
    "/api/stätes",
    "/api/hassio/addons",
    "/api/hassio/addons/x/install",
    "/api/HASSIO/addons",
    "/api/hassio_ingress/token/x",
    "/api/hassio_push/x",
    "/api/supervisor/info",
    "/api/states/supervisor",
    "/api/x/hassio/y",
    "/api/" + "a" * 600,
]


@pytest.mark.parametrize("caller", [OWNER, OPERATOR])
@pytest.mark.parametrize("path", BAD_PATHS)
async def test_rest_path_attacks_are_refused_before_any_request(
    env: Env, caller: Caller, path: str
) -> None:
    for method in ("GET", "POST", "DELETE"):
        result = await call("ha.rest", {"method": method, "path": path}, caller)
        assert result["error"] == "INVALID_PARAM", (method, path)
    env.assert_no_requests()


@pytest.mark.parametrize(
    "params",
    [
        {"method": "GET", "path": "/api/states", "headers": {"X-Evil": "1"}},
        {"method": "GET", "path": "/api/states", "Authorization": "Bearer x"},
        {"method": "PUT", "path": "/api/states"},
        {"method": "get", "path": "/api/states"},
        {"method": ["GET"], "path": "/api/states"},
        {"method": "GET", "path": 7},
        {"method": "GET"},
        {"method": "GET", "path": "/api/states", "body": {"x": 1}},
        {"method": "DELETE", "path": "/api/config/automation/config/1", "body": {}},
        {"method": "POST", "path": "/api/template", "body": "x"},
        {"method": "GET", "path": "/api/states", "query": [["a", "1"], ["a", "2"]]},
        {"method": "GET", "path": "/api/states", "query": {"a b": "1"}},
        {"method": "GET", "path": "/api/states", "query": {"a": True}},
        {"method": "GET", "path": "/api/states", "query": {"a": ["1", "2"]}},
        {"method": "GET", "path": "/api/states", "query": {"a": "line\nbreak"}},
        {"method": "GET", "path": "/api/states", "query": {"a": "x" * 2000}},
        {"method": "GET", "path": "/api/states", "query": {f"k{i}": "1" for i in range(17)}},
        {"method": "POST", "path": "/api/services/Light/turn_on", "body": {}},
        {"method": "POST", "path": "/api/services/light/turn on", "body": {}},
    ],
)
async def test_rest_shape_errors_are_refused_before_any_request(
    env: Env, params: dict[str, Any]
) -> None:
    result = await call("ha.rest", params, OWNER)

    assert result["error"] == "INVALID_PARAM"
    env.assert_no_requests()


@pytest.mark.parametrize(
    "params",
    [
        {"type": "supervisor/../api"},
        {"type": "Supervisor/api"},
        {"type": "a/b/c/d/e"},
        {"type": ""},
        {"type": 5},
        {"type": "get_states", "payload": []},
        {"type": "get_states", "payload": None},
        {"type": "get_states", "headers": {}},
        {"type": "call_service", "payload": {"domain": "light"}},
        {"type": "call_service", "payload": {"domain": "Light", "service": "turn_on"}},
        {"type": "call_service", "payload": {"domain": "light", "service": ["turn_on"]}},
        {},
    ],
)
async def test_ws_shape_errors_are_refused_before_any_request(
    env: Env, params: dict[str, Any]
) -> None:
    result = await call("ha.ws", params, OWNER)

    assert result["error"] == "INVALID_PARAM"
    env.assert_no_requests()


async def test_supervisor_tunnel_needs_owner_job_approval(env: Env) -> None:
    tunnel = {"type": "supervisor/api", "payload": {"endpoint": "/info", "method": "get"}}

    assert (await call("ha.ws", tunnel, OWNER))["error"] == "APPROVAL_REQUIRED"
    assert (await call("ha.ws", {"requests": [tunnel]}, OWNER))["error"] == "PROPOSAL_REQUIRED"
    env.assert_no_requests()


# --- audit -----------------------------------------------------------------


async def test_audit_records_ok_refused_and_error_without_secrets(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok-SECRET-123")
    monkeypatch.setenv("HASS_TOKEN", "tok-SECRET-456")
    env.ws.side_effect = [[1], HAClientError("HA_NOT_FOUND", "gone")]
    env.rest.return_value = {"answer": "body-SECRET"}

    await call("ha.ws", svc("light", "turn_on", note="payload-SECRET"), HOUSEHOLD)
    await call("ha.ws", svc("light", "turn_on"), HOUSEHOLD)
    await call("ha.ws", remove("light.a"), HOUSEHOLD)
    await call("ha.rest", {"method": "GET", "path": "/api/states", "query": {"q": "query-SECRET"}})
    await call("ha.rest", {"method": "GET", "path": "/api/hassio/x"})

    ok, error, refused, rest_ok, invalid = env.lines()
    assert (ok["result"], ok["role"], ok["actor"], ok["kind"]) == (
        "ok",
        "household",
        "adult-id",
        "control",
    )
    assert ok["target"] == "ws call_service light.turn_on"
    assert (error["result"], error["reason"]) == ("error", "HA_NOT_FOUND")
    assert (refused["result"], refused["reason"], refused["kind"]) == (
        "refused",
        "PERMISSION_DENIED",
        "admin",
    )
    assert (rest_ok["target"], rest_ok["command"]) == ("GET /api/states", "ha.rest")
    assert (invalid["result"], invalid["reason"], invalid["target"]) == (
        "refused",
        "INVALID_PARAM",
        None,
    )
    text = env.audit.read_text()
    assert "SECRET" not in text
    assert all(e["ts"] and e["job"] for e in env.lines())


async def test_unwritable_audit_log_refuses_before_any_request(
    env: Env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(
        ha_passthrough, "load_config", lambda: SimpleNamespace(data_dir=blocker / "d")
    )

    result = await call("ha.ws", {"type": "get_states"})

    assert result["error"] == "AUDIT_UNAVAILABLE"
    env.assert_no_requests()


# --- results ---------------------------------------------------------------


async def test_ha_failures_keep_their_codes(env: Env) -> None:
    env.ws.side_effect = [HAClientError("HA_AUTH", "rejected"), TypeError("frame")]

    first = await call("ha.ws", {"type": "get_states"})
    second = await call("ha.ws", {"type": "get_states"})

    assert (first["error"], first["message"]) == ("HA_AUTH", "rejected")
    assert second["error"] == "HA_BAD_RESPONSE"


async def test_results_past_the_response_cap_are_omitted_not_truncated(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ha_passthrough, "_RESULT_BUDGET_BYTES", 50)
    env.rest.return_value = "x" * 100

    result = await call("ha.rest", {"method": "GET", "path": "/api/error_log"})

    assert result["ok"] is True
    assert "result" not in result
    assert "cap" in result["result_omitted"]
    assert env.lines()[0]["result"] == "ok"


async def test_supplied_lock_codes_are_masked_in_results(env: Env) -> None:
    env.ws.return_value = {"echo": "code 4821"}

    result = await call("ha.ws", svc("lock", "unlock", code="4821"), HOUSEHOLD)

    assert "4821" not in json.dumps(result)
    assert "4821" not in env.audit.read_text()


async def test_audit_log_follows_a_changed_data_dir(
    env: Env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await call("ha.ws", {"type": "get_states"})
    other = tmp_path / "other"
    monkeypatch.setattr(ha_passthrough, "load_config", lambda: SimpleNamespace(data_dir=other))

    await call("ha.ws", {"type": "get_states"})

    assert len(env.lines()) == 1
    assert len((other / "ha-audit.jsonl").read_text().splitlines()) == 1
