"""Home Assistant control surface (P4.1 - P4.5 commands).

Implements the read and call-service primitives that replace the equivalent
``mcp__homeassistant__*`` MCP tools.  All handlers are async and talk to HA
via :mod:`openclaw_node.ha_client`.

Commands in this module:

- ``ha.list_states``          — return state of all entities.
- ``ha.get_state``            — return state of one entity.
- ``ha.call_service``         — call a service (domain, service, target, data).
- ``ha.list_areas``           — return all area-registry entries.
- ``ha.list_devices``         — return all device-registry entries.
- ``ha.list_services``        — return all service descriptions by domain.
- ``ha.list_entity_registry`` — return all entity-registry entries.
- ``ha.logbook``              — return logbook entries (optional entity + time window).
- ``ha.history``              — return state history for entities (optional time window).
- ``ha.reload_config``        — reload HA core config (operator-admin gated).
- ``ha.light_turn_on``        — turn on one or more lights (entity/area/device target).
- ``ha.light_turn_off``       — turn off one or more lights.
- ``ha.list_automations``     — list automation entities and optionally include traces.
- ``ha.check_config``         — validate HA configuration.yaml.
- ``ha.addon_logs``           — fetch Supervisor add-on logs (read-only).
- ``ha.list_addons``          — list Supervisor add-ons with slug + state (read-only).
- ``ha.addon_info``           — per-addon metadata, options STRIPPED (read-only).
- ``ha.addon_stats``          — per-addon CPU/memory/network/io numbers (read-only).
- ``ha.addon_changelog``      — per-addon changelog markdown (read-only).
- ``ha.addon_documentation``  — per-addon documentation markdown (read-only).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final

from openclaw_node.ha_client import (
    HAClientError,
    ha_get,
    ha_post,
    ha_ws_call,
    supervisor_get_json,
    supervisor_get_text,
)

_LOG: Final[logging.Logger] = logging.getLogger(__name__)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _to_error(exc: HAClientError) -> dict[str, Any]:
    return _error(exc.code, exc.message)


async def handle_ha_list_states(params: dict[str, Any]) -> dict[str, Any]:
    """Return the state of every entity registered in HA.

    Params:
        domain (str, optional): If provided, only return entities whose
            ``entity_id`` is under that domain (e.g. ``"sensor"``).

    Returns:
        ``{ok: True, count, states}`` or an error dict.
    """
    domain_filter = params.get("domain")
    try:
        raw = await ha_get("/api/states")
    except HAClientError as exc:
        return _to_error(exc)

    if not isinstance(raw, list):
        return _error("HA_BAD_RESPONSE", "Expected list from /api/states")

    states: list[dict[str, Any]] = raw
    if domain_filter:
        prefix = f"{domain_filter}."
        states = [
            s
            for s in states
            if isinstance(s.get("entity_id"), str) and s["entity_id"].startswith(prefix)
        ]

    return {"ok": True, "count": len(states), "states": states}


async def handle_ha_get_state(params: dict[str, Any]) -> dict[str, Any]:
    """Return the state object for a single entity.

    Params:
        entity_id (str): Required; e.g. ``"sensor.kitchen_temperature"``.

    Returns:
        ``{ok: True, state}`` or an error dict.
    """
    entity_id = str(params.get("entity_id", ""))
    if not entity_id:
        return _error("MISSING_PARAM", "entity_id is required")

    try:
        state = await ha_get(f"/api/states/{entity_id}")
    except HAClientError as exc:
        return _to_error(exc)

    if not isinstance(state, dict):
        return _error("HA_BAD_RESPONSE", "Expected dict from /api/states/<entity_id>")

    return {"ok": True, "state": state}


async def handle_ha_call_service(params: dict[str, Any]) -> dict[str, Any]:
    """Call a Home Assistant service.

    Params:
        domain (str): Required; e.g. ``"light"``.
        service (str): Required; e.g. ``"turn_on"``.
        target (dict, optional): Service target dict (entity_id/area_id/device_id).
        data (dict, optional): Service-specific data payload.

    Returns:
        ``{ok: True, changed_states}`` with the HA response (list of state
        objects that changed) or an error dict.
    """
    domain = str(params.get("domain", ""))
    service = str(params.get("service", ""))
    if not domain:
        return _error("MISSING_PARAM", "domain is required")
    if not service:
        return _error("MISSING_PARAM", "service is required")

    target = params.get("target")
    data = params.get("data")
    if target is not None and not isinstance(target, dict):
        return _error("INVALID_PARAM", "target must be a dict")
    if data is not None and not isinstance(data, dict):
        return _error("INVALID_PARAM", "data must be a dict")

    body: dict[str, Any] = {}
    if data:
        body.update(data)
    if target:
        # HA REST collapses target into the body for service calls.
        body.update(target)

    try:
        result = await ha_post(f"/api/services/{domain}/{service}", body or None)
    except HAClientError as exc:
        return _to_error(exc)

    changed: list[dict[str, Any]] = result if isinstance(result, list) else []
    return {"ok": True, "changed_states": changed}


async def handle_ha_list_areas(_params: dict[str, Any]) -> dict[str, Any]:
    """Return all area-registry entries from Home Assistant.

    Returns:
        ``{ok: True, count, areas}`` where each area is a dict with at least
        ``area_id`` and ``name``, or an error dict.
    """
    try:
        result = await ha_ws_call("config/area_registry/list")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(result, list):
        return _error("HA_BAD_RESPONSE", "Expected list from area_registry/list")
    return {"ok": True, "count": len(result), "areas": result}


async def handle_ha_list_devices(_params: dict[str, Any]) -> dict[str, Any]:
    """Return all device-registry entries from Home Assistant.

    Returns:
        ``{ok: True, count, devices}`` or an error dict.
    """
    try:
        result = await ha_ws_call("config/device_registry/list")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(result, list):
        return _error("HA_BAD_RESPONSE", "Expected list from device_registry/list")
    return {"ok": True, "count": len(result), "devices": result}


async def handle_ha_list_services(_params: dict[str, Any]) -> dict[str, Any]:
    """Return all service descriptions from Home Assistant.

    Uses the REST ``/api/services`` endpoint which returns a list of domain
    objects, each containing the services available in that domain.

    Returns:
        ``{ok: True, count, services}`` or an error dict.
    """
    try:
        raw = await ha_get("/api/services")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(raw, list):
        return _error("HA_BAD_RESPONSE", "Expected list from /api/services")
    return {"ok": True, "count": len(raw), "services": raw}


async def handle_ha_list_entity_registry(_params: dict[str, Any]) -> dict[str, Any]:
    """Return all entity-registry entries from Home Assistant.

    Returns:
        ``{ok: True, count, entities}`` or an error dict.
    """
    try:
        result = await ha_ws_call("config/entity_registry/list")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(result, list):
        return _error("HA_BAD_RESPONSE", "Expected list from entity_registry/list")
    return {"ok": True, "count": len(result), "entities": result}


async def handle_ha_logbook(params: dict[str, Any]) -> dict[str, Any]:
    """Return logbook entries from Home Assistant.

    Params:
        start_time (str, optional): ISO-8601 timestamp; default is one day ago.
        end_time (str, optional): ISO-8601 upper bound.
        entity_id (str, optional): Restrict to a single entity.

    Returns:
        ``{ok: True, count, entries}`` or an error dict.
    """
    path = "/api/logbook"
    start_time = str(params.get("start_time", ""))
    if start_time:
        path = f"{path}/{start_time}"

    query_parts: list[str] = []
    end_time = str(params.get("end_time", ""))
    if end_time:
        query_parts.append(f"end_time={end_time}")
    entity_id = str(params.get("entity_id", ""))
    if entity_id:
        query_parts.append(f"entity={entity_id}")
    if query_parts:
        path = f"{path}?{'&'.join(query_parts)}"

    try:
        raw = await ha_get(path)
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(raw, list):
        return _error("HA_BAD_RESPONSE", "Expected list from /api/logbook")
    return {"ok": True, "count": len(raw), "entries": raw}


async def handle_ha_history(params: dict[str, Any]) -> dict[str, Any]:
    """Return state history for entities from Home Assistant.

    Params:
        start_time (str, optional): ISO-8601 start; default is one day ago.
        end_time (str, optional): ISO-8601 upper bound.
        entity_ids (list[str], optional): List of entity IDs to filter.
        minimal_response (bool, optional): Reduce payload size (default False).
        no_attributes (bool, optional): Omit attributes (default False).
        significant_changes_only (bool, optional): Only significant changes.

    Returns:
        ``{ok: True, count, history}`` where ``history`` is a list of entity
        history lists, or an error dict.
    """
    path = "/api/history/period"
    start_time = str(params.get("start_time", ""))
    if start_time:
        path = f"{path}/{start_time}"

    query_parts: list[str] = []
    end_time = str(params.get("end_time", ""))
    if end_time:
        query_parts.append(f"end_time={end_time}")
    entity_ids = params.get("entity_ids")
    if entity_ids is not None:
        if not isinstance(entity_ids, list) or not all(isinstance(e, str) for e in entity_ids):
            return _error("INVALID_PARAM", "entity_ids must be a list of strings")
        query_parts.append(f"filter_entity_id={','.join(entity_ids)}")
    for flag in ("minimal_response", "no_attributes", "significant_changes_only"):
        if params.get(flag):
            query_parts.append(flag)
    if query_parts:
        path = f"{path}?{'&'.join(query_parts)}"

    try:
        raw = await ha_get(path)
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(raw, list):
        return _error("HA_BAD_RESPONSE", "Expected list from /api/history/period")
    return {"ok": True, "count": len(raw), "history": raw}


async def handle_ha_reload_config(params: dict[str, Any]) -> dict[str, Any]:
    """Reload the Home Assistant core configuration.

    This is an operator-admin action; the caller must supply a valid
    ``OPENCLAW_ADMIN_TOKEN`` in the environment (same gate as ``system.run``).

    Returns:
        ``{ok: True}`` on success or an error dict.
    """
    import hmac
    import os

    required = os.environ.get("OPENCLAW_ADMIN_TOKEN", "")
    if not required:
        return _error("PERMISSION_DENIED", "ha.reload_config: admin gate not configured")
    caller = str(params.get("admin_token", ""))
    if not hmac.compare_digest(caller.encode(), required.encode()):
        return _error("PERMISSION_DENIED", "ha.reload_config requires operator admin token")

    try:
        await ha_post("/api/services/homeassistant/reload_core_config")
    except HAClientError as exc:
        return _to_error(exc)
    return {"ok": True}


def _build_light_target(params: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Extract and validate a light service target from params.

    Returns:
        ``(target_dict, None)`` on success, or ``(None, error_message)`` on failure.
    """
    entity_id = params.get("entity_id")
    area_id = params.get("area_id")
    device_id = params.get("device_id")
    if entity_id is None and area_id is None and device_id is None:
        return None, "one of entity_id, area_id, or device_id is required"
    target: dict[str, Any] = {}
    if entity_id is not None:
        if not isinstance(entity_id, (str, list)):
            return None, "entity_id must be a string or list of strings"
        target["entity_id"] = entity_id
    if area_id is not None:
        target["area_id"] = area_id
    if device_id is not None:
        target["device_id"] = device_id
    return target, None


async def handle_ha_light_turn_on(params: dict[str, Any]) -> dict[str, Any]:
    """Turn on one or more lights.

    Params:
        entity_id (str | list[str], optional): Target entity or entities.
        area_id (str, optional): Target area.
        device_id (str, optional): Target device.
        brightness (int, optional): 0-255.
        brightness_pct (float, optional): 0-100.
        color_temp_kelvin (int, optional): Colour temperature in Kelvin.
        rgb_color (list[int], optional): [R, G, B] 0-255 each.
        transition (float, optional): Transition time in seconds.

    One of ``entity_id``, ``area_id``, or ``device_id`` is required.

    Returns:
        ``{ok: True, changed_states}`` or an error dict.
    """
    target, err = _build_light_target(params)
    if err is not None:
        return _error("MISSING_PARAM", err)

    data: dict[str, Any] = {}
    for key in ("brightness", "brightness_pct", "color_temp_kelvin", "rgb_color", "transition"):
        if key in params:
            data[key] = params[key]

    body: dict[str, Any] = {}
    if data:
        body.update(data)
    if target:
        body.update(target)

    try:
        result = await ha_post("/api/services/light/turn_on", body or None)
    except HAClientError as exc:
        return _to_error(exc)

    changed: list[dict[str, Any]] = result if isinstance(result, list) else []
    return {"ok": True, "changed_states": changed}


async def handle_ha_light_turn_off(params: dict[str, Any]) -> dict[str, Any]:
    """Turn off one or more lights.

    Params:
        entity_id (str | list[str], optional): Target entity or entities.
        area_id (str, optional): Target area.
        device_id (str, optional): Target device.
        transition (float, optional): Transition time in seconds.

    One of ``entity_id``, ``area_id``, or ``device_id`` is required.

    Returns:
        ``{ok: True, changed_states}`` or an error dict.
    """
    target, err = _build_light_target(params)
    if err is not None:
        return _error("MISSING_PARAM", err)

    body: dict[str, Any] = {}
    if "transition" in params:
        body["transition"] = params["transition"]
    if target:
        body.update(target)

    try:
        result = await ha_post("/api/services/light/turn_off", body or None)
    except HAClientError as exc:
        return _to_error(exc)

    changed: list[dict[str, Any]] = result if isinstance(result, list) else []
    return {"ok": True, "changed_states": changed}


async def handle_ha_list_automations(params: dict[str, Any]) -> dict[str, Any]:
    """List automation entities, optionally including recent traces.

    Params:
        include_traces (bool, optional): If True, attach the most recent trace
            list per automation via WS ``trace/list``. Default False.

    Returns:
        ``{ok: True, count, automations}`` where each automation has
        ``entity_id``, ``state``, ``attributes`` and optionally ``traces``.
    """
    try:
        raw = await ha_get("/api/states")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(raw, list):
        return _error("HA_BAD_RESPONSE", "Expected list from /api/states")

    automations: list[dict[str, Any]] = [
        s
        for s in raw
        if isinstance(s.get("entity_id"), str) and s["entity_id"].startswith("automation.")
    ]

    if params.get("include_traces"):
        for auto in automations:
            entity_id = auto["entity_id"]
            automation_id = (auto.get("attributes") or {}).get("id")
            if not automation_id:
                auto["traces"] = []
                continue
            try:
                traces = await ha_ws_call(
                    "trace/list",
                    {"domain": "automation", "item_id": str(automation_id)},
                )
            except HAClientError as exc:
                _LOG.warning("trace/list failed for %s: %s", entity_id, exc.message)
                auto["traces"] = []
                continue
            auto["traces"] = traces if isinstance(traces, list) else []

    return {"ok": True, "count": len(automations), "automations": automations}


async def handle_ha_check_config(_params: dict[str, Any]) -> dict[str, Any]:
    """Validate the Home Assistant ``configuration.yaml``.

    Hits ``POST /api/config/core/check_config``.  Should be called before any
    reload to surface errors that would otherwise crash HA on restart.

    Returns:
        ``{ok: True, result, errors, warnings}`` where ``result`` is
        ``"valid"`` or ``"invalid"`` as reported by HA, or an error dict.
    """
    try:
        raw = await ha_post("/api/config/core/check_config")
    except HAClientError as exc:
        return _to_error(exc)
    if not isinstance(raw, dict):
        return _error("HA_BAD_RESPONSE", "Expected dict from /api/config/core/check_config")

    return {
        "ok": True,
        "result": raw.get("result", "unknown"),
        "errors": raw.get("errors"),
        "warnings": raw.get("warnings"),
    }


_ADDON_SLUG_MAX_LEN: Final[int] = 128
_ADDON_LOGS_DEFAULT_LINES: Final[int] = 200
_ADDON_LOGS_MAX_LINES: Final[int] = 5000
_ADDON_LOGS_MAX_BYTES: Final[int] = 1_048_576


_ADDON_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _valid_addon_slug(slug: str) -> bool:
    """Accept Supervisor add-on slugs: ASCII lowercase ``[a-z0-9_-]`` only.

    Supervisor slugs are documented as lowercase ``[a-z0-9_-]``. ``str.isalnum``
    accepts uppercase ASCII and arbitrary Unicode letters/digits, which would
    let an attacker craft a slug like ``Self`` or ``addon‮`` that passes
    validation but is not a real slug — and could in theory be used to probe
    Supervisor path handling. We enforce the documented grammar with a regex.
    The literal ``"self"`` is the only allowed alias.
    """
    if not slug or len(slug) > _ADDON_SLUG_MAX_LEN:
        return False
    return _ADDON_SLUG_RE.match(slug) is not None


async def handle_ha_addon_logs(params: dict[str, Any]) -> dict[str, Any]:
    """Return the most recent log lines for a Supervisor-managed add-on.

    Hits ``GET http://supervisor/addons/<slug>/logs``. Read-only by construction
    (the Supervisor endpoint does not mutate state). Requires the add-on to be
    running under Supervisor with ``SUPERVISOR_TOKEN`` available.

    Params:
        slug (str): Required; the Supervisor add-on slug (e.g.
            ``"fcccfbbd_openclaw_hass_node"``, or the special value ``"self"``
            to fetch this node's own logs).
        lines (int, optional): Number of trailing lines to return.
            Defaults to ``200``; clamped to ``[1, 5000]``.

    Returns:
        ``{ok: True, slug, lines, log}`` where ``log`` is trimmed from a
        bounded trailing byte window, or an error dict.
    """
    slug = str(params.get("slug", "")).strip()
    if not slug:
        return _error("MISSING_PARAM", "slug is required")
    if not _valid_addon_slug(slug):
        return _error("INVALID_PARAM", f"invalid addon slug: {slug!r}")

    lines_param = params.get("lines", _ADDON_LOGS_DEFAULT_LINES)
    try:
        lines = int(lines_param)
    except (TypeError, ValueError):
        return _error("INVALID_PARAM", "lines must be an integer")
    lines = max(1, min(lines, _ADDON_LOGS_MAX_LINES))

    try:
        body = await supervisor_get_text(f"/addons/{slug}/logs", max_bytes=_ADDON_LOGS_MAX_BYTES)
    except HAClientError as exc:
        return _to_error(exc)

    log_lines = body.splitlines()
    trimmed = log_lines[-lines:] if len(log_lines) > lines else log_lines
    return {
        "ok": True,
        "slug": slug,
        "lines": len(trimmed),
        "log": "\n".join(trimmed),
    }


_ADDON_FIELDS: Final[tuple[str, ...]] = (
    "slug",
    "name",
    "state",
    "version",
    "version_latest",
    "update_available",
)
# Note: Supervisor's "repository" field is the addon source — for community/
# private addons that is a repo URL which can reveal an operator-private
# hostname or path. Dropped from the read-only surface; if a future caller
# truly needs source attribution, normalise to a label first.


async def handle_ha_list_addons(_params: dict[str, Any]) -> dict[str, Any]:
    """Return the list of Supervisor-managed add-ons (slug + state + version).

    Hits ``GET http://supervisor/addons``. Read-only by construction. Required
    discovery path for :func:`handle_ha_addon_logs` — callers can't fetch logs
    for a slug they can't see. Only a fixed, non-sensitive subset of fields is
    returned (slug, name, state, version, version_latest, update_available);
    the full Supervisor response is dropped. ``repository`` is deliberately
    omitted because for community/private addons it is a repo URL that can
    reveal an operator-private hostname.

    Returns:
        ``{ok: True, count, addons}`` with ``addons`` as a list of dicts, or an
        error dict.
    """
    try:
        raw = await supervisor_get_json("/addons")
    except HAClientError as exc:
        return _to_error(exc)

    if not isinstance(raw, dict):
        return _error("HA_BAD_RESPONSE", "Expected dict from Supervisor /addons")
    data = raw.get("data")
    if not isinstance(data, dict):
        return _error("HA_BAD_RESPONSE", "Supervisor /addons response missing 'data'")
    addons_raw = data.get("addons")
    if not isinstance(addons_raw, list):
        return _error("HA_BAD_RESPONSE", "Supervisor /addons 'data.addons' is not a list")

    addons: list[dict[str, Any]] = []
    for entry in addons_raw:
        if not isinstance(entry, dict):
            continue
        addons.append({field: entry.get(field) for field in _ADDON_FIELDS})

    return {"ok": True, "count": len(addons), "addons": addons}


# Fields kept from Supervisor /addons/<slug>/info.
#
# DELIBERATELY OMITTED — these can leak operator-private state:
#   - "options"        : current option VALUES (passwords, tokens, URLs, paths).
#   - "schema"         : option SCHEMA can reveal field names (e.g. "mqtt_password",
#                        "api_key_for_x"), which alone leak what integrations are
#                        configured. Drop entirely rather than partially exposing.
#   - "hostname"       : container hostname can leak naming conventions.
#   - "ip_address"     : internal Docker network IP.
#   - "host_network"   : whether the addon shares host networking.
#   - "ports"          : configured host port mapping.
#   - "ports_description"
#   - "auth_api" / "homeassistant_api" / "hassio_api" / "hassio_role" / "host_dbus"
#     / "host_ipc" / "host_pid" / "kernel_modules" / "privileged" / "devicetree"
#     / "audio" / "video" / "gpio" / "usb" / "uart" / "stdin" — capability flags
#     reveal what the addon is allowed to touch (security-relevant attack surface
#     mapping).
#   - "logo" / "icon" / "url" raw URLs — keeping name/description is enough; URLs
#     can sometimes point at private repositories.
#   - "discovery" / "services" / "webui" — internal service-discovery wiring.
#   - "long_description" — generally safe but unbounded; included summary only.
#
#   - "repository"     : addon source. "core"/"local" are fine but a community
#                        or private addon stores a repo URL here which can leak
#                        an operator-private hostname. Dropped entirely rather
#                        than allowlisting only the well-known values, because
#                        the well-known set drifts with Supervisor releases.
#
# Kept fields are addon-metadata that are either public (slug/name/version/state
# also visible in the public addon registry) or shape-only (boot, startup,
# stage, arch, machine, update_available, ingress, ingress_port) and don't
# depend on operator-private configuration.
_ADDON_INFO_FIELDS: Final[tuple[str, ...]] = (
    "slug",
    "name",
    "state",
    "description",
    "version",
    "version_latest",
    "update_available",
    "boot",
    "startup",
    "stage",
    "arch",
    "machine",
    "ingress",
    "ingress_port",
)


async def handle_ha_addon_info(params: dict[str, Any]) -> dict[str, Any]:
    """Return public-safe metadata for a single Supervisor add-on.

    Hits ``GET http://supervisor/addons/<slug>/info``. Read-only by
    construction. Returns ONLY a fixed allowlist of fields that cannot leak
    operator-private state — see the ``_ADDON_INFO_FIELDS`` definition above for
    the full kept-vs-dropped policy and the security rationale.

    In particular, the addon's current option VALUES (``options``) and the
    option SCHEMA (``schema``) are dropped at the boundary. Even schema field
    *names* are dropped because they can reveal which integrations are
    configured (e.g. a ``mqtt_password`` schema field implies MQTT auth is
    active). If a future caller needs option-aware introspection, that is a
    separate, admin-gated command — not part of this read-only surface.

    Params:
        slug (str): Required; Supervisor add-on slug (validated against
            ``_valid_addon_slug``). ``"self"`` returns this node's own info.

    Returns:
        ``{ok: True, slug, info}`` where ``info`` is a dict of the allowlisted
        fields (missing source fields surface as ``None``), or an error dict.
    """
    slug = str(params.get("slug", "")).strip()
    if not slug:
        return _error("MISSING_PARAM", "slug is required")
    if not _valid_addon_slug(slug):
        return _error("INVALID_PARAM", f"invalid addon slug: {slug!r}")

    try:
        raw = await supervisor_get_json(f"/addons/{slug}/info")
    except HAClientError as exc:
        return _to_error(exc)

    if not isinstance(raw, dict):
        return _error("HA_BAD_RESPONSE", "Expected dict from Supervisor /addons/<slug>/info")
    data = raw.get("data")
    if not isinstance(data, dict):
        return _error("HA_BAD_RESPONSE", "Supervisor /addons/<slug>/info response missing 'data'")

    info = {field: data.get(field) for field in _ADDON_INFO_FIELDS}
    return {"ok": True, "slug": slug, "info": info}


# Supervisor /addons/<slug>/stats returns numeric utilisation fields. All of
# these are operational metrics, not user-configurable state, so they're safe
# to pass through. We still allowlist explicitly so a future Supervisor
# release that adds a sensitive field (e.g. environment dump) does not leak by
# default.
_ADDON_STATS_FIELDS: Final[tuple[str, ...]] = (
    "cpu_percent",
    "memory_usage",
    "memory_limit",
    "memory_percent",
    "network_rx",
    "network_tx",
    "blk_read",
    "blk_write",
)


async def handle_ha_addon_stats(params: dict[str, Any]) -> dict[str, Any]:
    """Return runtime resource stats for a single Supervisor add-on.

    Hits ``GET http://supervisor/addons/<slug>/stats``. Read-only by
    construction. Returns an allowlisted subset of utilisation metrics
    (``_ADDON_STATS_FIELDS``); any future Supervisor field is dropped by
    default so a release that adds a sensitive field cannot leak through.

    Params:
        slug (str): Required; Supervisor add-on slug. ``"self"`` returns this
            node's own stats.

    Returns:
        ``{ok: True, slug, stats}`` with the allowlisted metric fields, or an
        error dict.
    """
    slug = str(params.get("slug", "")).strip()
    if not slug:
        return _error("MISSING_PARAM", "slug is required")
    if not _valid_addon_slug(slug):
        return _error("INVALID_PARAM", f"invalid addon slug: {slug!r}")

    try:
        raw = await supervisor_get_json(f"/addons/{slug}/stats")
    except HAClientError as exc:
        return _to_error(exc)

    if not isinstance(raw, dict):
        return _error("HA_BAD_RESPONSE", "Expected dict from Supervisor /addons/<slug>/stats")
    data = raw.get("data")
    if not isinstance(data, dict):
        return _error("HA_BAD_RESPONSE", "Supervisor /addons/<slug>/stats response missing 'data'")

    stats = {field: data.get(field) for field in _ADDON_STATS_FIELDS}
    return {"ok": True, "slug": slug, "stats": stats}


# Markdown bodies (changelog + documentation) can be large; cap at a similar
# 1 MiB trailing window to the addon_logs path. Both endpoints return text.
_ADDON_DOC_MAX_BYTES: Final[int] = 1_048_576


async def handle_ha_addon_changelog(params: dict[str, Any]) -> dict[str, Any]:
    """Return the changelog markdown for a single Supervisor add-on.

    Hits ``GET http://supervisor/addons/<slug>/changelog``. Read-only by
    construction. Truncates to a bounded 1 MiB trailing window via
    :func:`supervisor_get_text` so a runaway changelog cannot blow the wire
    response size.

    Params:
        slug (str): Required; Supervisor add-on slug.

    Returns:
        ``{ok: True, slug, changelog}`` with the markdown body as text, or an
        error dict. ``HA_NOT_FOUND`` if the addon doesn't publish a changelog.
    """
    slug = str(params.get("slug", "")).strip()
    if not slug:
        return _error("MISSING_PARAM", "slug is required")
    if not _valid_addon_slug(slug):
        return _error("INVALID_PARAM", f"invalid addon slug: {slug!r}")

    try:
        body = await supervisor_get_text(
            f"/addons/{slug}/changelog", max_bytes=_ADDON_DOC_MAX_BYTES
        )
    except HAClientError as exc:
        return _to_error(exc)

    return {"ok": True, "slug": slug, "changelog": body}


async def handle_ha_addon_documentation(params: dict[str, Any]) -> dict[str, Any]:
    """Return the documentation markdown for a single Supervisor add-on.

    Hits ``GET http://supervisor/addons/<slug>/documentation``. Read-only by
    construction. Bounded at 1 MiB trailing window via
    :func:`supervisor_get_text`.

    Params:
        slug (str): Required; Supervisor add-on slug.

    Returns:
        ``{ok: True, slug, documentation}`` with the markdown body as text,
        or an error dict. ``HA_NOT_FOUND`` if the addon doesn't publish
        documentation.
    """
    slug = str(params.get("slug", "")).strip()
    if not slug:
        return _error("MISSING_PARAM", "slug is required")
    if not _valid_addon_slug(slug):
        return _error("INVALID_PARAM", f"invalid addon slug: {slug!r}")

    try:
        body = await supervisor_get_text(
            f"/addons/{slug}/documentation", max_bytes=_ADDON_DOC_MAX_BYTES
        )
    except HAClientError as exc:
        return _to_error(exc)

    return {"ok": True, "slug": slug, "documentation": body}
