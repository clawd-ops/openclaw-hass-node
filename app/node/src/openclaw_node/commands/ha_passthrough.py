"""``ha.ws`` and ``ha.rest``: generic Home Assistant Core pass-through.

Every call goes through one pipeline, in this order, and every refusal happens
before any request reaches Home Assistant:

    normalize -> classify -> authorize -> execute -> audit

- *normalize* validates the shape (strict keys, a Core-relative ``/api/`` path,
  no reserved WS keys) and builds one :class:`Request` per call.
- *classify* and *authorize* are the tables in :mod:`openclaw_node.ha_rules`.
- A call whose role decision is ``approval`` must be a **job**: ``{"requests":
  [...]}``. One native approval, bound to the exact request list, covers the
  whole job. The marker is the existing one (``config_mutation``); it is
  verified and consumed only after every policy check has passed. A job may mix
  WebSocket and REST requests: an item is ``{"ws": {...}}`` or ``{"rest": {...}}``,
  and an untagged item takes the command's own shape.
- A **plan** (``{"requests": [...], "plan": true}``) normalizes and classifies a
  job and returns the full normalized list, per-kind counts and the job digest the
  approval binds. It sends no HA request and needs no approval; it writes one
  ``planned`` audit line. The caller shows that list to the human before asking.
- *audit* appends one JSON line per request (who, role, kind, target, approval,
  result). It never records tokens, headers, payloads, or response bodies. It
  fails closed: an ``attempt`` line for every request is written before the
  first one runs, and if that write fails nothing runs (``AUDIT_UNAVAILABLE``).
  If a result line cannot be written afterwards, the remaining requests are not
  run and the error (``AUDIT_FAILED_AFTER_ACTION``) says what ran and keeps any HA
  error. A marker is spent once consumed; if the attempt write then fails, the job
  never ran and the caller must re-plan and get a new approval.

``ha.ws`` takes ``{type, payload?}``; ``ha.rest`` takes ``{method, path, query?,
body?}``. A job of either command takes ``{requests: [<that shape>, ...]}``.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Final, NoReturn

from openclaw_node import ha_client
from openclaw_node.approval_policy import CURRENT_CALLER
from openclaw_node.authz import redact_code
from openclaw_node.commands.config_mutation import (
    APPROVAL_PARAM,
    approval_bind,
    consume_approval_marker,
)
from openclaw_node.commands.params import strict_keys_error
from openclaw_node.config import load_config
from openclaw_node.ha_client import HAClientError
from openclaw_node.ha_rules import KIND_ORDER, Classification, classify, decide, policy_role

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

_MAX_JOB_REQUESTS: Final[int] = 100
# Total result bytes returned for one call; the same ceiling as one HA WS frame.
_RESULT_BUDGET_BYTES: Final[int] = ha_client.MAX_RESPONSE_BYTES
_AUDIT_FILE: Final[str] = "ha-audit.jsonl"
_AUDIT_MAX_BYTES: Final[int] = 5 * 1024 * 1024
_AUDIT_BACKUPS: Final[int] = 3

_WS_TYPE: Final = re.compile(r"[a-z0-9_]+(?:/[a-z0-9_]+){0,3}")
_NAME: Final = re.compile(r"[a-z0-9_]{1,64}")
_REST_METHODS: Final[frozenset[str]] = frozenset({"GET", "POST", "DELETE"})
# Core-relative API paths only. No scheme, host, query, fragment, backslash or
# percent-escape can be written in this alphabet, so encoded dots and double
# encoding are refused by construction. HA's API needs none of them.
_REST_PATH: Final = re.compile(r"/api/[A-Za-z0-9_.:+/-]{1,500}")
_QUERY_NAME: Final = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_MAX_QUERY_PAIRS: Final[int] = 16
_SUPERVISOR_SEGMENTS: Final[frozenset[str]] = frozenset({"hassio", "supervisor"})


class _Invalid(Exception):
    """A request that fails normalization; the message is returned to the caller."""


def _bad(message: str) -> NoReturn:
    raise _Invalid(message)


@dataclass(frozen=True)
class Request:
    """One normalized request: what to classify, how to name it, how to run it."""

    target: str
    label: str
    item: dict[str, Any]  # the normalized request, tagged ``ws`` or ``rest``
    run: Callable[[], Awaitable[Any]]


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _item_keys(item: dict[str, Any], allowed: frozenset[str]) -> None:
    invalid = strict_keys_error(item, allowed)
    if invalid is not None:
        _bad(invalid["message"])


def _ws_request(item: dict[str, Any]) -> Request:
    _item_keys(item, frozenset({"type", "payload"}))
    msg_type = item.get("type")
    if not isinstance(msg_type, str) or not _WS_TYPE.fullmatch(msg_type):
        _bad("type must be a WebSocket message type such as 'config/area_registry/list'")
    payload = item.get("payload", {})
    if not isinstance(payload, dict):
        _bad("payload must be an object")
    if msg_type.split("/")[0] in _SUPERVISOR_SEGMENTS:
        _bad("Supervisor tunnels are not available through ha.ws")
    clash = ha_client.RESERVED_WS_KEYS & set(payload)
    if clash:
        _bad(f"payload must not set reserved key(s): {', '.join(sorted(clash))}")
    if msg_type == "call_service":
        domain, service = payload.get("domain"), payload.get("service")
        if not (
            isinstance(domain, str)
            and isinstance(service, str)
            and _NAME.fullmatch(domain)
            and _NAME.fullmatch(service)
        ):
            _bad("call_service needs lowercase [a-z0-9_] domain and service in payload")
        target, label = f"svc {domain}.{service}", f"ws call_service {domain}.{service}"
    else:
        target, label = f"ws {msg_type}", f"ws {msg_type}"
    item = {"ws": {"type": msg_type, "payload": payload}}
    return Request(target, label, item, lambda: ha_client.ha_ws_call(msg_type, payload or None))


def _rest_path(path: Any) -> list[str]:
    """Validate a Core-relative API path and return its segments."""
    if not isinstance(path, str) or not _REST_PATH.fullmatch(path):
        _bad("path must be a Core-relative /api/ path using only letters, digits and _ . : + / -")
    segments = path.split("/")[1:]
    if any(not seg or set(seg) == {"."} for seg in segments):
        _bad("path must not contain empty or dot segments")
    if segments[1].lower().startswith(tuple(_SUPERVISOR_SEGMENTS)) or any(
        seg.lower() in _SUPERVISOR_SEGMENTS for seg in segments
    ):
        _bad("Supervisor routes are not available through ha.rest")
    return segments


def _rest_query(raw: Any) -> dict[str, str | int | float]:
    if not isinstance(raw, dict):
        _bad("query must be an object of name/value pairs; duplicate names are refused")
    if len(raw) > _MAX_QUERY_PAIRS:
        _bad(f"query takes at most {_MAX_QUERY_PAIRS} pairs")
    for name, value in raw.items():
        scalar = isinstance(value, str | int | float) and not isinstance(value, bool)
        if not isinstance(name, str) or not _QUERY_NAME.fullmatch(name) or not scalar:
            _bad("query names must be simple and values must be strings or numbers")
        if isinstance(value, str) and (len(value) > 1024 or not value.isprintable()):
            _bad("query values must be short printable strings")
    return raw


def _rest_request(item: dict[str, Any]) -> Request:
    _item_keys(item, frozenset({"method", "path", "query", "body"}))
    method = item.get("method")
    if not isinstance(method, str) or method not in _REST_METHODS:
        _bad(f"method must be one of {', '.join(sorted(_REST_METHODS))}")
    path = item.get("path")
    segments = _rest_path(path)
    query = _rest_query(item["query"]) if "query" in item else None
    body = item.get("body")
    if "body" in item and (method != "POST" or not isinstance(body, dict | list)):
        _bad("body is only for POST and must be an object or a list")
    label = f"{method} {path}"
    target = label
    if method == "POST" and len(segments) == 4 and segments[:2] == ["api", "services"]:
        if not all(_NAME.fullmatch(seg) for seg in segments[2:]):
            _bad("service routes need lowercase [a-z0-9_] domain and service")
        target = f"svc {segments[2]}.{segments[3]}"
    return Request(
        target,
        label,
        {"rest": dict(item)},
        lambda: ha_client.ha_rest(str(method), str(path), query, body),
    )


def _parse(command: str, params: dict[str, Any]) -> tuple[list[Request], bool, bool]:
    """Return the normalized requests, whether the call is a job, and whether it is a plan."""
    body = {k: v for k, v in params.items() if k != APPROVAL_PARAM}
    default = _ws_request if command == "ha.ws" else _rest_request
    if "requests" not in body:
        return [default(body)], False, False
    if not set(body) <= {"requests", "plan"}:
        _bad("a job takes only 'requests' (and 'plan': true)")
    if "plan" in body and body["plan"] is not True:
        _bad("plan must be true")
    raw = body["requests"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= _MAX_JOB_REQUESTS:
        _bad(f"requests must be a list of 1 to {_MAX_JOB_REQUESTS} requests")
    out: list[Request] = []
    for index, item in enumerate(raw):
        try:
            if not isinstance(item, dict):
                _bad("each request must be an object")
            tag = next(iter(item)) if len(item) == 1 else None
            if tag not in {"ws", "rest"}:
                out.append(default(item))
            elif not isinstance(item[tag], dict):
                _bad(f"'{tag}' must be an object")
            else:
                out.append((_ws_request if tag == "ws" else _rest_request)(item[tag]))
        except _Invalid as exc:
            message = f"request {index}: {exc}"
            raise _Invalid(message) from None
    return out, True, "plan" in body


class _StrictAuditHandler(RotatingFileHandler):
    """A rotating file handler whose write or rotation errors reach the caller.

    The stock handler reports them to stderr and carries on, which would let a
    request run with no audit record.
    """

    def handleError(self, record: logging.LogRecord) -> None:
        del record
        raise  # called from the stdlib's except block: re-raises its error


def _stderr(message: str) -> None:
    print(f"ha audit: {message}", file=sys.stderr, flush=True)


def _audit_logger() -> logging.Logger:
    """The append-only audit log in the node's data dir; raises ``OSError`` if unusable."""
    path = load_config().data_dir / _AUDIT_FILE
    logger = logging.getLogger("openclaw_node.ha_audit")
    if not any(getattr(h, "baseFilename", None) == str(path) for h in logger.handlers):
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = _StrictAuditHandler(
            path, maxBytes=_AUDIT_MAX_BYTES, backupCount=_AUDIT_BACKUPS, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        for old in list(logger.handlers):
            logger.removeHandler(old)
            old.close()
        logger.addHandler(handler)
        logger.propagate = False
        logger.setLevel(logging.INFO)
    return logger


def _job_id(command: str, params: dict[str, Any]) -> str | None:
    """Short digest naming the call in the audit log; None if the params cannot be digested.

    A plan's ``plan`` flag is left out so it names the job it plans, as the approval does.
    """
    try:
        return approval_bind(command, "", {k: v for k, v in params.items() if k != "plan"})[:16]
    except (AttributeError, TypeError):  # a non-string key; normalization refuses it
        return None


def _marker_id(params: dict[str, Any]) -> str | None:
    marker = params.get(APPROVAL_PARAM)
    marker_id = marker.get("id") if isinstance(marker, dict) else None
    return marker_id if isinstance(marker_id, str) else None


def _plan(
    command: str,
    params: dict[str, Any],
    requests: list[Request],
    classes: Sequence[Classification],
    decisions: Sequence[str],
    write: Callable[..., bool],
) -> dict[str, Any]:
    """Describe a job without running it: the full normalized list, counts and digest.

    No HA request is made and nothing is consumed. The digest is the one the approval
    binds, because the executed job is exactly ``{"requests": [...]}``. The ``planned``
    audit line carries the same list so the approver can check it later; lock codes are
    masked as everywhere else. If that line cannot be written the plan is not returned.
    """
    digest = approval_bind(command, "", {"requests": params["requests"]})
    entries = [
        {"index": i, "kind": c.kind, "hard": c.hard, "decision": d, **r.item}
        for i, (r, c, d) in enumerate(zip(requests, classes, decisions, strict=True))
    ]
    listing = redact_code({"requests": entries})["requests"]
    counts = {k: sum(c.kind == k for c in classes) for k in KIND_ORDER}
    plan = {
        "ok": True,
        "plan": True,
        "digest": digest,
        "count": len(requests),
        "counts": {k: n for k, n in counts.items() if n},
        "highest_kind": max((c.kind for c in classes), key=KIND_ORDER.index),
        "decision": next(
            (d for d in ("refuse", "approval") if d in decisions),
            "allow",
        ),
        "requests": listing,
    }
    if not write(None, None, "planned", None, {k: v for k, v in plan.items() if k != "ok"}):
        return _error("AUDIT_UNAVAILABLE", "audit log is not writable; the plan was not recorded")
    return plan


async def _handle(command: str, params: dict[str, Any]) -> dict[str, Any]:
    caller = CURRENT_CALLER.get()
    role = policy_role(caller)
    base: dict[str, Any] = {
        "command": command,
        "actor": caller.actor_id if caller else "<none>",
        "role": role,
        "job": _job_id(command, params),
    }
    try:
        audit_log = _audit_logger()
    except OSError as exc:
        _LOG.error("ha audit log unavailable: %s", type(exc).__name__)
        return _error("AUDIT_UNAVAILABLE", "audit log is not writable; no request was sent")

    def write(
        request: Request | None,
        c: Classification | None,
        result: str,
        reason: str | None,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        """Append one audit line; False (and a line on stderr) if it could not be written."""
        entry = {
            **base,
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "target": request.label if request else None,
            "kind": c.kind if c else None,
            "hard": c.hard if c else None,
            "approval": approval_id,
            "result": result,
            "reason": reason,
            **(extra or {}),
        }
        try:
            audit_log.info(json.dumps(entry, sort_keys=True))
        except (OSError, ValueError) as exc:
            _stderr(f"write failed ({type(exc).__name__}): {result} {entry['target']}")
            return False
        return True

    approval_id: str | None = None
    spent = False  # the approval marker was consumed; it is never usable again
    try:
        requests, is_job, is_plan = _parse(command, params)
    except _Invalid as exc:
        write(None, None, "refused", "INVALID_PARAM")
        return _error("INVALID_PARAM", str(exc))
    classes = [classify(r.target) for r in requests]
    decisions = [decide(role, c) for c in classes]
    if is_plan:
        return _plan(command, params, requests, classes, decisions, write)

    def refuse(code: str, message: str) -> dict[str, Any]:
        for request, c in zip(requests, classes, strict=True):
            write(request, c, "refused", code)
        return _error(code, message)

    for request, c, decision in zip(requests, classes, decisions, strict=True):
        if decision == "refuse":
            hard = " (hard-deny)" if c.hard else ""
            return refuse(
                "PERMISSION_DENIED",
                f"{request.label} is a {c.kind} request{hard}; it is not permitted for this "
                f"caller ({role}). Pass it to the owner.",
            )
    if "approval" in decisions:
        if not is_job:
            return refuse(
                "APPROVAL_REQUIRED",
                f"{requests[0].label} needs the owner's approval: resubmit it as a job "
                "('requests': [...]) so the owner can approve it",
            )
        approval_id = _marker_id(params)
        denied = consume_approval_marker(command, "", params)
        if denied is not None:
            return refuse(denied["error"], denied["message"])
        spent = True
    else:
        params.pop(APPROVAL_PARAM, None)  # an approval nobody needed is never forwarded

    # Fail closed: every request is on record as an attempt before the first one runs.
    for request, c in zip(requests, classes, strict=True):
        if not write(request, c, "attempt", None):
            respend = (
                "; the approval is spent, so re-plan and get a new approval to retry"
                if spent
                else ""
            )
            return _error(
                "AUDIT_UNAVAILABLE", f"audit log is not writable; no request was sent{respend}"
            )

    results: list[dict[str, Any]] = []
    budget = _RESULT_BUDGET_BYTES
    failure: dict[str, Any] | None = None
    audit_lost = False
    for request, c in zip(requests, classes, strict=True):
        if failure is not None or audit_lost:
            if not audit_lost:
                audit_lost = not write(request, c, "skipped", "PRIOR_REQUEST_FAILED")
            reason = "an earlier request in the job failed" if failure else "the audit log failed"
            results.append(_error("SKIPPED", f"not run: {reason}"))
            continue
        try:
            data = await request.run()
        except HAClientError as exc:
            failure = _error(exc.code, exc.message)
        except (TypeError, ValueError):
            failure = _error("HA_BAD_RESPONSE", "Home Assistant returned an unreadable response")
        if failure is not None:
            audit_lost = not write(request, c, "error", failure["error"])
            results.append(failure)
            continue
        budget -= len(json.dumps(data))
        audit_lost = not write(request, c, "ok", None)
        results.append(
            {"ok": True, "kind": c.kind, "result": data}
            if budget >= 0
            else {"ok": True, "kind": c.kind, "result_omitted": "response cap reached; it ran"}
        )
    outcome = (
        "the request WAS performed in Home Assistant"
        if failure is None
        else f"the request failed first ({failure['error']}: {failure['message']})"
    )
    lost = (
        {
            "error": "AUDIT_FAILED_AFTER_ACTION",
            "message": f"{outcome}, but its audit record could not be written; later requests "
            "in a job were not run. This is not a refusal",
        }
        if audit_lost
        else {}
    )
    if not is_job:
        return {**results[0], **lost, "ok": results[0]["ok"] and not lost}
    job: dict[str, Any] = {
        "ok": failure is None and not lost,
        "job": base["job"],
        "results": results,
    }
    if failure is not None:
        job.update(error="JOB_FAILED", message=f"{failure['error']}: {failure['message']}")
    return {**job, **lost} if lost else job


async def handle_ha_ws(params: dict[str, Any]) -> dict[str, Any]:
    """Run one HA WebSocket request, or an approved job of them. See the module docstring."""
    return await _handle("ha.ws", params)


async def handle_ha_rest(params: dict[str, Any]) -> dict[str, Any]:
    """Run one HA REST request, or an approved job of them. See the module docstring."""
    return await _handle("ha.rest", params)
