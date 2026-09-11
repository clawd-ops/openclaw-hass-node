# Command Surface Verification — 2026-09-11

**Status: WORKING DOCUMENT. This file, not `STATUS.md` / `TODO.md` / `INSTALL.md` /
`UAT-PLAN.md` / `COMMAND-SURFACE.md`, is the trusted picture until those are
reconciled.**

Why this exists: the existing docs are known to be internally inconsistent and
partly stale. Rather than edit them in place and risk laundering a stale claim
into a fresh one, this document records what was *observed*, with the method, so
each line can be trusted or discarded on its own evidence.

- **Node:** `hass`, add-on version `2026.7.23b1`, slug `fcccfbbd_openclaw_hass_node`
- **Repo tip:** `ab03579` (tracked tree clean at audit start; this document and
  the completion roadmap are the resulting untracked documentation artifacts)
- **Gateway:** `gateway.nodes.commands.allow` = 55 entries
- **Method:** two independent passes, merged.
  1. Live read-only probes against the running node (Clawd). No mutations.
  2. Source-level audit of handlers and docs (Codex, `gpt-5.6-sol`). No repo changes.
- **Conflict rule applied:** live evidence outranks repo inference; repo evidence
  outranks live *absence* of a symptom. Section 2.1 is where that mattered.

| Tag | Meaning |
|---|---|
| `LIVE-PASS` | Probed against the running node, behaved correctly |
| `LIVE-FAIL` | Probed, misbehaved. Reproduction included |
| `LIVE-REFUSED` | Probed, correctly refused. Fail-closed working as designed |
| `LIVE-VALIDATION` | Probed only far enough to confirm input validation; full operation not proven |
| `CODE-FAIL` | Defect proven from source with file:line. Not separately probed |
| `CODE-RECOGNIZED` | Handler reads the named parameter in source; live behavior not proven |
| `NOT-PROBED` | Mutating or out of scope. Status genuinely unknown |

---

## 0. Safety correction: HA-native config writes are not approval-gated

`CODE-FAIL`, critical. The earlier live probe established that protected
`fs.*` writes refuse execution without the missing proposal bridge. That
finding does **not** extend to the nine `ha.config.*` command families.

Their mutating actions call a local `_require_proposal` helper that accepts any
non-empty string except the literal `"direct"`. The node never verifies that
the ID exists, was approved by a human, matches the caller, binds the same
operation, is unexpired, or has not already been consumed. The handler then
performs the HA REST or WebSocket mutation and echoes the caller-supplied string
into the result. For example:

- `ha_config_automation.py:39-59` performs only string-shape checks;
  `:103-126` and `:129-148` then save or delete.
- The same `_require_proposal` pattern exists in the script, scene, Lovelace,
  helper, area, device, entity, and config-entry handlers.

Therefore an arbitrary value such as `proposal_id: "anything"` is currently an
authorization bypass for every advertised HA-native config mutation. These
mutations were not exercised live during this verification because doing so
would alter Home Assistant. Until a real approval verifier exists, they must be
treated as **unsafe and unavailable**, not as correctly fail-closed.

Protected `fs.*` and `ha.config.*` currently implement two different behaviors:

- protected `fs.*`: refuses and waits for infrastructure that does not exist;
- mutating `ha.config.*`: accepts an unverified label and proceeds.

The completion roadmap makes closing this gap the first implementation gate.

---

## 1. The finding that reframes everything

**There are two callers of this node, and they do not get the same behavior.**

- The **Assist plugin path** (`openclaw-hass-node-assist-tools`, the `ha_*`
  tools) translates parameter names before dispatch.
- The **direct path** (`nodes.invoke`, which is what `COMMAND-SURFACE.md`
  documents and what every subagent and workspace script uses) passes the
  caller's dict straight through.

For several commands the documented parameter names are **not** the names the
handler reads. Via the plugin, the call works. Via the documented direct path,
the parameter is silently dropped and the handler falls back to its default —
which for `ha.history` and `ha.logbook` is *all entities, last 24 hours.*

My live sweep initially scored these as passing. It was testing through the
plugin wrappers. That was a false negative, corrected here — this is the
"you ask for a bounded subset and get everything" symptom, and it is worse than
the two issues already filed for it.

| Command | Documented (`COMMAND-SURFACE.md`) | Handler actually reads | Direct-path result |
|---|---|---|---|
| `ha.history` | `entity_id`, `start`, `end` | `entity_ids` (list), `start_time`, `end_time` | **all entities, 24h default** |
| `ha.logbook` | `start`, `end` | `start_time`, `end_time` | **24h default window** |
| `ha.list_states` | `entity_filter` | `domain` only | **all 7,175 entities** |
| `system.which` | `binary` | `name` | call fails |
| `fs.read` | `encoding: "base64"` | only `"binary"` yields base64 | `DECODE_ERROR` |
| `ha.reload_config` | `domain` | *(ignored entirely)* | **always reloads core config** |

Source: `commands/ha.py:73-101` (`domain_filter = params.get("domain")`, no
`entity_filter`), `commands/ha.py:327-351`, `commands/ha.py:362-395`,
`commands/ha.py:406-423`, `commands/system.py:36-49`,
`docs/reference/COMMAND-SURFACE.md:59-63,86-88`.

`ha.reload_config` is the worst of these: the Assist plugin *explicitly forwards*
`domain` (`ha-admin-tools.ts:108-114`), so a caller asking to reload only the
`automation` domain silently gets a full core-config reload instead.

Note `fs.read` fails *loudly* — `encoding: "base64"` reaches
`data.decode("base64")`, raises `LookupError`, and returns `DECODE_ERROR`
(`commands/fs.py:208-221`). That is the good version of this bug. The `ha.*`
mismatches are the bad version: they fall back to a permissive default and
return more data than asked for, with no error.

### 1.1 This exact fix has been done once already

Issue [#240](https://github.com/clawd-ops/openclaw-hass-node/issues/240)
(closed COMPLETED 2026-07-22) fixed the identical doc/param drift for the `fs.*`
family — `fs.glob root`, `fs.restore version|proposal_id|at`, `fs.diff
from_version/to_version` are all correctly documented today
(`COMMAND-SURFACE.md:42,47,49`), along with a real `fs.write` sha256
(`fs_write.py:306`) and an in-process `fs.patch` with no binary dependency.

**The same fix was never applied to the `ha.*` family.** That is the whole
explanation for §1. There is a known-good template in the repo's own history;
the work is to finish applying it, and then to make the dispatcher enforce it so
it cannot drift a third time.

### 1.2 Root cause

`commands/dispatcher.py:182-207` passes arbitrary dicts to handlers with **no
schema and no unknown-parameter rejection.** Every unsupported or misspelled
parameter is a silent no-op. Every finding in §1 and §2 is a symptom of this one
design gap.

**The fix that retires the whole class** is per-command parameter schemas with
strict rejection of unknown keys. Fixing `fs.read` and `ha.list_automations`
individually — the two filed issues — leaves the other five live and guarantees
the sixth gets written next time someone adds a command.

---

## 2. Confirmed broken

### 2.1 Unbounded collections — no command supports `limit`

`CODE-FAIL`. These return the complete collection and silently ignore any bound:

`ha.list_areas`, `ha.list_devices`, `ha.list_services`, `ha.list_events`,
`ha.list_config_entries`, `ha.list_entity_registry`, `ha.list_addons`
(`commands/ha.py:171-250,312-324,757-792`); `fs.history`, which consumes only
`path` (`commands/fs_write.py:449-487`); and the collection actions of
`ha.config.lovelace`, `ha.config.helpers`, and the area/device/entity registries
(`ha_config_lovelace.py:128-140`, `ha_config_helpers.py:105-109`,
`ha_config_area_registry.py:74-81`, `ha_config_device_registry.py:55-62`,
`ha_config_entity_registry.py:64-73`).

Codex's framing is correct and worth preserving: these bounds were never
advertised, so they are *unsupported* rather than *broken*. The defect is that
an unsupported bound is accepted silently instead of rejected.

### 2.2 `fs.read` ignores `offset` and `length` — [#257](https://github.com/clawd-ops/openclaw-hass-node/issues/257)

`LIVE-FAIL` + `CODE-FAIL` (`commands/fs.py:153-198` — consumes only `path`,
`encoding`, `max_bytes`).

```
fs.read { path: /config/configuration.yaml, offset: 0, length: 32 }
→ ok: true, size: 4486, entire file returned
```

**Severity is higher than "parameter ignored."** The body included
`recorder.db_url` with the MySQL username and password in cleartext, plus a
commented-out Telegram bot API key. A caller that bounded its read to 32 bytes
received the whole credential-bearing file.

### 2.3 `ha.list_automations` ignores filters — [#259](https://github.com/clawd-ops/openclaw-hass-node/issues/259)

`LIVE-FAIL` + `CODE-FAIL` (`commands/ha.py:524-566` — narrows only to the
`automation.` domain; no caller filter is read).

```
ha.list_automations { entity_filter: "automation.zzz_no_match_at_all" }
→ 122,698 characters across 3,496 lines — the full automation set
```

Aggravating factor: with `include_traces`, trace lookups run for **every**
automation because no narrowing exists (`commands/ha.py:542-566`).

### 2.4 `system.run` advertised but unreachable — [#258](https://github.com/clawd-ops/openclaw-hass-node/issues/258)

`LIVE-FAIL` at the original 2026-09-11 audit; the surface change landed
subsequently.

```
nodes.invoke { command: system.run }
→ invokeCommand "system.run" is reserved for shell execution;
  use exec with host=node instead
```

Direct `nodes.invoke system.run` remains rejected by the Gateway by design.
The advertised path is now correct: `system.run` and `system.run.prepare` are
reached through the OpenClaw exec tool with `host=node`, which prepares a
canonical `systemRunPlan`, prompts an operator, and forwards the approved
plan to the node. The `_admin_token_ok` gate has been removed from
`commands/system_run.py`; the node re-validates the forwarded argv,
`rawCommand`, `cwd` (bound to the allowed roots), and env keys before
execution, and rejects credential-shaped env keys. A live operator
allow/deny cycle observation against this node remains a UAT gate rather
than a source change.

### 2.5 `ha.addon_update` / `ha.update_install` unreachable — [#260](https://github.com/clawd-ops/openclaw-hass-node/issues/260)

`LIVE-FAIL` + `CODE-FAIL`. Dispatcher registers **53** (`dispatcher.py:91`);
connect frame advertises **51** (`gateway_ws.py:74`). The two missing are exactly
`ha.addon_update` (`ha.py:1040`) and `ha.update_install` (`ha.py:1053`). No
advertised-but-unregistered commands exist.

```
nodes.invoke { command: ha.addon_update, slug: core_mosquitto }
→ node command not allowed: the node does not support "ha.addon_update"
```

**Consequence worth stating plainly:** both are present in
`gateway.nodes.commands.allow`. Rob completed that configuration step in July.
The chain is plugin exposes → gateway permits → **node never advertises** →
invoke rejects. The add-on self-update path has never been callable, and the July
task marked done produced no working capability.

### 2.6 NEW — query parameters are interpolated without percent-encoding

`LIVE-FAIL`, root cause now confirmed in source.

```
ha.history { start: ...T01:00:00+00:00, end: ...T01:05:00+00:00 }
→ HA_HTTP_ERROR: HA returned 400: {"message":"Invalid end_time"}

ha.history { start: ...T01:00:00Z,      end: ...T01:05:00Z }
→ ok: true, 5 points
```

`commands/ha.py:377-395` builds the URL by raw f-string interpolation —
`query_parts.append(f"end_time={end_time}")` then `f"{path}?{'&'.join(...)}"` —
with no `urlencode`. The `+` in a `+HH:MM` offset is parsed by HA as a space.
Same pattern in `handle_ha_logbook` (`ha.py:338-351`), and `start_time` is
interpolated into the *path* segment with the same exposure. Any entity ID or
timestamp containing a URL-reserved character is affected, not just offsets.

### 2.7 NEW — silent empty for unknown entities

`LIVE-FAIL`, minor. `ha.history` for a nonexistent entity returns
`ok: true, count: 0`. A typo is indistinguishable from a genuinely quiet entity.
Same family as the above: the caller cannot tell a correct narrow result from a
silently wrong one.

### 2.8 Tier B lifecycle authorization contradicts itself — [#262](https://github.com/clawd-ops/openclaw-hass-node/issues/262)

`CODE-FAIL`, now with precise evidence. The node's lifecycle handler
(`commands/ha.py:661-694`) checks **only** the slug allow/deny policy and does
**not** consult `admin_token`. `ha.update_install` is different and *does* check
`OPENCLAW_ADMIN_TOKEN` (`commands/ha.py:1078-1103`). Meanwhile the plugin policy,
`COMMAND-SURFACE.md:99-102`, `INSTALL.md:166-170`, and `STATUS.md:37-43` all
state that lifecycle calls require a matching node admin token. They do not.

### 2.9 NEW — Assist `ha.call_service` silently drops service data

`CODE-FAIL`. The Assist tool accepts and forwards `service_data`
(`ha-call-service-tool.ts:22,48-51`), while the Python handler reads only
`data` (`commands/ha.py:148-160`). The policy forwards the object unchanged.
The service can therefore execute and report success while ignoring requested
values such as brightness, transition, temperature, or notification content.

### 2.10 NEW — handler failures can be presented as successful tool calls

`CODE-FAIL`. `gateway_ws.py:1183-1188` wraps every returned handler dict in a
transport-level `ok: true`, including payloads shaped `{ok: false, error: ...}`.
The plugin helper checks only the outer transport flag
(`node-tool-invoke.ts:65-72`), and service tools render unconditional success
language such as `Called ...` (`ha-call-service-tool.ts:65`). A refusal or HA
error can therefore be delivered as a successful-looking tool result.

Completion requires separate, consistently propagated states for transport
delivery, command outcome, and any verified postcondition.

### 2.11 HISTORICAL — the retired MCP checker can certify seven fake days

`CODE-FAIL`, but not a current release blocker. The legacy Home Assistant MCP
path was permanently retired before this audit, so this checker is obsolete and
must not be used to reopen the cutover. The script accepts empty stdin as
a clean result (`:42`), increments the streak once per invocation rather than
once per covered date (`:60-67`), and labels the count as days (`:70-73`). Seven
immediate empty invocations produce `RETIREMENT_READY`. Its matcher (`:45`)
also recognizes only the hyphenated readonly spelling and can miss underscore
forms present in some tool naming paths.

Disposition: the checker and its smoke test were removed; migration references
remain as historical documentation. Add-on rollout, caller authorization,
subagent policy, and command parity remain separate node concerns.

### 2.12 NEW — backup and trash storage are not isolated

`CODE-FAIL`. `backup_store.py:14-17,28-34` explicitly defers retention
eviction, garbage collection, and pinning, while `BACKUPS.md:8-15,107-116`
claims bounded 500 MiB retention and pinning. The backing directories
`/share/openclaw-backups` and `/share/openclaw-trash` are also beneath generic
directly writable `/share`; that root is not protected by the filesystem
proposal gate (`app/config.yaml:76-84`). A caller can therefore alter the data
the recovery story depends on.

### 2.13 NEW — several read and process paths remain memory-unbounded

`CODE-FAIL`. Ordinary HA REST and WS results are decoded without a response cap
(`ha_client.py:210-264,453-463`). Bulk states, services, devices, registries,
history, logbook, calendars, automation traces, and config lists have no
pagination or encoded-byte ceiling. `system.run` truncates returned output only
after `subprocess.run(capture_output=True)` has buffered all of it
(`system_run.py:172-191`). This is a resource-exhaustion problem separate from
whether a caller asked for a logical item limit.

### 2.14 NEW — health, packaging, and release evidence can overstate readiness

`CODE-FAIL`:

- `/health` always reports `ok: true` and omits HA reachability and independent
  node/chat connection readiness (`http_api.py:267-287`).
- The plugin is private TypeScript with no build artifact, and its standard
  install is documented as broken (`plugins/.../package.json:1-27`,
  `known-workarounds.md:9-60`).
- Docker uses floating base tags and lower-bound dependency installation rather
  than the committed lockfile (`app/Dockerfile:13,22-28`).
- CI exercises one amd64 image and separate Python/TypeScript suites, but not a
  HACS runtime, published multi-architecture artifacts, or the cross-language
  command path (`.github/workflows/ci.yaml`, `ts.yml`).
- Release tagging is not gated on a successful CI artifact
  (`release-on-version-bump.yml:30-47,171-211`).

These are blockers to claiming an installed release is healthy and reproducible
even after handler-level bugs are fixed.

---

## 3. Protected filesystem fail-closed behavior confirmed working

`LIVE-REFUSED`:

```
fs.write { path: /config/openclaw_probe_should_refuse.txt, content: "probe" }
→ PROPOSAL_REQUIRED: Path requires a proposal;
  gateway-side proposal bridge ships in P3.3
```

The protected-filesystem refusal is correct. Its error message names the
missing piece itself: *the gateway-side proposal bridge.* That is TODO #20.
Do not generalize this result to the `ha.config.*` families; section 0 records
their unverified-ID bypass.

Two clarifications from the source read that matter operationally:

- **Proposal gating is conditional, not universal.** It applies to protected
  roots or `agent_bridge=true`. Unprotected files can be written directly
  (`fs_write.py:228-250,339-365`, `fs_move_delete.py:191-228`,
  `fs_patch.py:303-325`).
- **`.storage` is safer than documented.** `STATUS.md:110-112` claims writes are
  permitted with `unsafe_storage=true` plus a proposal. No such parameter exists;
  `fs_write.py:228-232` refuses unconditionally. The hard rule holds; the doc
  describes an escape hatch that was never built.

### 3.1 The approval flow has no human endpoint

Verified against the live agent-bridge:

- **22 proposals pending**, ids 9–30. Oldest **2026-07-23**, newest
  **2026-09-07**. All have `resolved_at: null`, `resolved_by: null`.
- **14 target peer `hass-node`** — never present in the peer registry.
- **1 targets `admin`** — last seen 2026-05-31.
- **7 (ids 24–30) are addressed from `clawd-…` to `clawd-…`** — the agent
  addressed them to itself.
- **There is no peer representing Rob.** The only live peer is the agent.

The only proposals ever resolved are ids 1–8, from May, all recording the same
agent peer as resolver. The agent was author, counterparty, and approver. That
is not an approval flow; it is a self-signed changelog.

Stranded work: #25 (`light_adaptive_apply_v2`, 13 duplicated blocks collapsed to
a data-driven `repeat.for_each`, Codex-reviewed twice), #24 (Zigbee OTA v4),
#30 (Zigbee offline-notify fix).

---

## 4. Live evidence

`LIVE-PASS`, probed 2026-09-11 01:20–01:30 UTC. **Read the §1 caveat first** —
rows marked ⚠ passed only through the Assist plugin wrappers and fail on the
documented direct path.

| Command | Verified | Result |
|---|---|---|
| `ping` | round trip | `pong: true` |
| `ha.addon_logs` | `lines=5` | exactly 5 lines |
| `ha.core_logs` | `lines=5` | exactly 5 lines |
| `ha.list_addons` | enumeration | 29 add-ons with `update_available` |
| `ha.addon_info` | correct slug | add-on metadata |
| `ha.check_config` | validation without reload | `result: valid` |
| `ha.calendar_get_events` | 7-day range | 1 event, correctly bounded |
| `fs.read` | content integrity | content, `size`, `sha256` correct |
| ⚠ `ha.list_states` | `entity_filter` | `3/7175` match, `0/7175` no-match |
| ⚠ `ha.history` | `start`/`end` | 5 points, correctly windowed |
| ⚠ `ha.logbook` | `start`/`end`, `entity_id` | 122 then 5 entries, correctly windowed |

`LIVE-VALIDATION`, not a full command proof: `fs.glob` without its required
`root` returned `ROOT_REQUIRED`.

`CODE-RECOGNIZED`, not separately live-proven: `fs.read.max_bytes` (clamped 1B–16MiB,
returns `TOO_LARGE` rather than truncating), `fs.list.hidden`/`max_entries`,
`fs.glob.root`/`pattern`/`hidden`/`max_matches`, `fs.restore.version`/
`proposal_id`/`at`, `fs.diff.from_version`/`to_version`, `system.run.cwd`/`env`/
`timeout` (256 KiB per stream), `ha.list_states.domain`, `ha.core_logs.lines`
and `ha.addon_logs.lines` (both clamped 1–5000), `ha.calendar_get_events.*`,
`ha.update_install.backup`/`version`, and the node-shape names for
`ha.logbook` / `ha.history`.

**False positive worth recording so nobody re-files it:** an early `ha.logbook`
probe with a measurement sensor returned 0 entries and looked like a filter bug.
Home Assistant itself excludes `state_class: measurement` sensors from the
logbook. Re-probed with a binary sensor: correct.

---

## 5. Not probed — status genuinely unknown

Excluded as mutating: `ha.call_service`, `ha.light_turn_on`, `ha.light_turn_off`,
`ha.reload_config`, `ha.addon_start`, `ha.addon_stop`, `ha.addon_restart`,
`ha.update_install`, `fs.delete`, `fs.move`, `fs.patch`, `fs.restore`, and the
write actions of the nine `ha.config.*` commands.

Worth flagging from the source read: `ha.call_service`, `ha.light_turn_on`, and
`ha.light_turn_off` are mutations with no proposal gate and no admin-token gate
in the node handler (`commands/ha.py:128-168,449-533`).

**This is not straightforwardly a defect, and an earlier draft of this document
wrongly implied it was.** Rob's direction (2026-09-11): turning on a light is a
simple intended action and should not be guarded. Other services are genuinely
destructive. `ha.call_service` is therefore a **case-by-case decision — auto-allow
or require-approval per service**, not a single gate on the whole command.

The real defect is granularity: today the command is uniformly ungated, which is
right for `light.turn_on` and wrong for `hassio.host_reboot`. Design tracked in
the completion roadmap.

Separately, `UAT-PLAN.md:146-150` claims `light.turn_on` goes through the
proposal flow and backup engine. It does not — it posts directly to HA and
records no backup. That doc claim is wrong on the facts *and* now wrong on the
intent, since gating it was never the right design.

Not probed for output shape: `ha.list_areas`, `ha.list_devices`,
`ha.list_services`, `ha.list_events`, `ha.list_config_entries`,
`ha.list_entity_registry`, `ha.addon_changelog`, `ha.addon_documentation`,
`ha.addon_stats`, `ha.get_config`, `ha.get_state`, `fs.list`, `fs.stat`,
`fs.diff`, `fs.history`, `system.which`, and `ha.config.*` read actions.

`ha.list_entity_registry` deserves a dedicated probe — observed failing in a log
health check on 2026-09-04, and `ha.list_addons` failed the same day with
`mod[factoryName] is not a function`. Neither reproduced today.

---

## 6. TODO.md work-status and accuracy pass

**Result: the listed work mostly remains open, but several descriptions are
stale or overstate existing enforcement.** Of 15 open items, 14 describe work
that is genuinely unfinished and none are secretly done. That does not make the
text itself reliable: item 11 still describes service/entity enforcement the
current routing-only plugin does not provide; item 20 generalizes the protected
filesystem refusal to HA-native config mutations, whose arbitrary-ID bypass is
documented in section 0; and item 17 is stale issue inventory.

| Item | Verdict | Evidence |
|---|---|---|
| 7 | Genuinely open | `AGENTS.md:10-17,54-62` |
| 11 | Open, description stale | No subagent identity in dispatch; `authz.py:1-7` says invoke-time enforcement is out of scope and the plugin is routing-only (`dispatcher.py:182-207`) |
| 12 | Genuinely open | Release workflow cuts tags only (`release-on-version-bump.yml:41-55`) |
| 13 | Genuinely open, partly external | `check_suite` absent from recognized events (`AGENTS.md:23-35`) |
| **17** | **STALE** | Claims only #1 is live; the 53-vs-51 mismatch alone disproves it (`dispatcher.py:134-135`, `gateway_ws.py:114-126`) |
| 20 | Open, description unsafe | Protected `fs.*` returns `PROPOSAL_REQUIRED` but never emits or awaits a proposal (`fs_write.py:234-250`); `ha.config.*` instead accepts arbitrary nonempty IDs (section 0) |
| 21 | Genuinely open, needs external probe | HACS brands PR state not knowable from repo |
| 22 | Genuinely open | No `image:` key, no publish job (`app/config.yaml:202-206`) |
| 23 | Genuinely open | No `docs.*` handler registered (`dispatcher.py:91-145`) |
| 27 | Genuinely open | Ingress disabled, no UI (`app/config.yaml:198-203`) |
| 32 | Genuinely open | No `show_tool_progress` in schema (`chat_relay.py:1112-1160`) |
| 35 | Genuinely open, needs live probe | Structured frames deliberately swallowed — HA ChatLog has no ephemeral hook (`http_api.py:645-660`) |
| 36 | Genuinely open, needs live probe | Handlers exist; acceptance explicitly requires live install verification |
| 37 | Genuinely open | No build script, no tracked `dist/` (`plugins/…/package.json:7-21`) |
| 38 | Genuinely open | Transcript state is in-memory, cleared on disconnect (`chat_relay.py:250-284,1445-1473`) |

---

## 7. Stale documentation — corrections to apply

Abbreviated; full file:line list is in the Codex pass. Every entry is
wrong-value → correct-value.

**`STATUS.md`** — version `2026.6.20b7`→`2026.7.23b1`; "ships 53 commands" → 53
registered but 51 advertised; lifecycle-behind-`OPENCLAW_ADMIN_TOKEN` → no token
consulted; "full surface under operator authorization" → dispatch is node-role,
operator-role advertises nothing (`__main__.py:455-485`); `trash-cli` →
`send2trash`; the `unsafe_storage=true` escape hatch → does not exist.

**`INSTALL.md`** — version; its text and example list 42 commands while the node
advertises 51 and the dispatcher registers 53; allowlist omits
`ha.addon_update`/`ha.update_install`; app name "OpenClaw
Node" → "OpenClaw HA Node — App"; `super_admins` takes HA **usernames**, not
UUIDs (`__main__.py:390-418`); "list of 37" → 51; manual `local_api_token` copy →
auto-generated at startup with bootstrap rotation (`__main__.py:511-525`).

**`design/PLAN.md`** — omits `fs.restore`/`fs.history`/`fs.diff` and both update
commands; "no plugin code" ignores the shipped `assist-tools` plugin; "~150 LOC"
HACS integration is ~855; identity is a SHA-256 digest of the Ed25519 public key,
not `hass-node@<ha-instance-id>`; token paths are `node-key.json` +
`device-token.node` + `device-token.operator`; `armv7` is not built; the embedded
manifest excerpt is obsolete in name, version, architectures, and schema.

**`operations/UAT-PLAN.md`** (note: not `docs/UAT-PLAN.md`) — version;
multi-arch image and `armv7` claims; app name; command count 37 → 51; pairing
described as node-role only when runtime opens two connections; **`light.turn_on`
described as proposal-gated with backups when it is neither**; `.storage` refusal
located at the dispatcher when it is per-handler.

**`reference/COMMAND-SURFACE.md`** — "registered and working" → 2 of 53 are
unreachable; `fs.read` encoding `"base64"` → handler expects `"binary"`;
`system.which` `binary` → `name`; logbook/history `start`/`end`/`entity_id` →
`start_time`/`end_time`/`entity_ids`; `reload_config` `domain` is ignored;
lifecycle `admin_token` is not consulted; Tier C "update out of scope" while the
same doc lists two Tier B update commands.

### 7.1 `openclaw-hass-node-app` is not an add-on slug

`app/config.yaml:22-24` declares `slug: openclaw_hass_node`; Supervisor reports
`fcccfbbd_openclaw_hass_node`. Reproduced live:

```
ha.addon_info { slug: "openclaw-hass-node-app" }
→ HA_NOT_FOUND: Supervisor returned 404 for /addons/openclaw-hass-node-app/info
```

The false claim is narrower than feared — **two sites, one file**:
`plugins/openclaw-hass-node-assist-tools/src/tools/descriptors.ts:22-26` and
`:27-28`, introduced by PR #256. That text is now shipped in the live `ha_*` tool
descriptions. Other occurrences (`README.md:47`, `SKILL.md:18`,
`COMPONENT-NAMING.md:13`) use it as a component label, which is correct naming
policy, not a slug claim. The same wrong fact is in the assistant's memory file
`reference_hass_node_name.md`.

**Unreleased correction:** PR #269 changes the shared descriptor to state that
the literal is neither a node id nor a Supervisor add-on slug. The generated
[command coverage ledger](reference/COMMAND-COVERAGE.md) records the Assist
caller path separately from node advertisement and direct invocation; this does
not change the installed plugin until a reviewed release is deployed.

### 7.2 Published plugin configuration does not validate

`README.md:101-102` instructs users to configure `allowServices`,
`allowReadEntities`, and `allowCalendars`. The shipped plugin manifest permits
only `allowAdminOps` and `adminToken` beneath each node and sets
`additionalProperties: false` (`plugins/.../openclaw.plugin.json:43-65`). The
documented configuration can therefore fail validation, and the current plugin
does not implement the claimed service/entity/calendar authorization.

---

## 8. Working conclusion

The node is in better shape than some docs suggest, and the project is in worse
shape than the issue tracker suggests.

- **Protected filesystem writes are correctly fail-closed and unusable**, because
  the approval bridge they wait on was never built. **HA-native config writes are
  worse: an arbitrary nonempty proposal ID passes and can mutate HA.**
- **The existing proposal queue is not an authorization system.** Twenty-two
  proposals accumulated for seven weeks against peers that cannot answer, and
  the node cannot validate any of them.
- **The silent-ignore class spans multiple commands and both caller paths**, and
  its root cause is the absence of an enforced cross-layer contract.
- **Assist service data is silently discarded**, and inner command failures can
  be presented as successful tool calls.
- **Two commands are permitted by the gateway and unreachable at the node**,
  which is why a completed configuration task produced no capability.
- **Three wrapper read cases behaved correctly in the recorded live probes while
  their documented direct-path aliases do not.** Other plugin reads, bounds, and
  output shapes remain unverified; no broad trust claim is justified.

### Suggested order

This differs from the 2026-09-07 audit's, because that audit predates both the
direct-vs-plugin split and the proposal-queue evidence.

1. **Contain the forged-proposal bypass** — reject every `ha.config.*` mutation
   until a real approval verifier is wired. This is the first safety gate.
2. **Dispatcher parameter schemas and semantic result propagation** — retires
   §1, §2.1, §2.9, and §2.10 wholesale, and
   subsumes #257 and #259 rather than patching them one at a time. Not currently
   an issue; should be.
3. **TODO #20** — build an authenticated human approval bridge and operation-
   bound, single-use approval lifecycle. Ingress panel remains the intended UX
   home, shared with #27 and #38, subject to validating the current Gateway API.
4. **Per-service policy** — node-enforced `auto_allow` / `require_approval` /
   `deny`, with ordinary `light.turn_on` explicitly auto-allowed and generic
   `ha.call_service` unable to bypass dedicated admin/update gates.
5. **#260** — derive advertisement from the contract and unblock the update path.
6. **#257 individually** if schemas slip — credential exposure justifies it.
7. **#262**, **§2.6 URL encoding**, **#258**, **#261**, then final documentation
   reconciliation after behavior is settled.

TODO #23 — `docs.lookup`, `docs.breaking_changes`, and HA core version detection
on connect — can be built alongside the approval bridge, but it is a hard
prerequisite to enabling accepted HA config writes. An approval is invalid if
the version-matched safety evidence or target-version precondition is missing.

---

## 9. Baseline test evidence and its limit

Re-run 2026-09-11 against `ab03579`:

- Python: **1,206 passed**, 75 warnings, reported total coverage 94%.
- TypeScript plugin: **159 passed** across 13 files.
- MCP retirement shell harness: **13 passed**.

The green result does not contradict this document. The Python and TypeScript
suites mock opposite sides of their boundary, so neither detects the
`service_data`/`data` mismatch or inner-error success rendering. HA config tests
use arbitrary proposal strings and expect mutation, thereby encoding the
authorization defect. The MCP harness positively asserts that seven immediate
empty invocations produce retirement readiness. Those are examples of tests
passing without proving the advertised outcome, and the reason the completion
roadmap requires cross-language, disposable-live, and production-canary layers.

---

*Passes: live probe (Clawd, Opus 5) + independent source/roadmap audits using
GPT-6 Astra and GPT-5.6 Sol. The audit agents made no edits. Baseline HEAD was
`ab03579`; the two documentation artifacts are the parent session's work.*
