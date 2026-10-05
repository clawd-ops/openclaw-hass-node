# Authorization model

Status: ratified 2026-09-11. Supersedes the add-on-specific secret gate
described in earlier revisions of `COMMAND-TIERS.md`, `COMPLETION-ROADMAP.md`,
and `INSTALL.md`.

## Decision

Authorization uses no add-on-specific secret. It comes from two
identities that already exist:

1. **OpenClaw operator identity** — the paired session and its operator scopes,
   which is how every other OpenClaw node authorizes privileged work.
2. **Home Assistant admin identity** — `identity.super_admins`, resolved from HA
   usernames to HA user IDs at startup.

Human approval for privileged operations uses **OpenClaw's existing approval
APIs**. This project does not build its own approval lifecycle, approval store,
or approval UI.

### Constraints this decision operates under

- No changes to the OpenClaw gateway.
- No pull requests against the OpenClaw repository.

Everything in the node-side sections below is consumption of APIs the gateway
already exposes, plus deletion of the parallel implementation in this
repository. The cross-surface ceiling described in
[Principal ceiling](#principal-ceiling) has a known limit under these
constraints, stated explicitly there.

## What already exists and becomes load-bearing

`identity.super_admins` resolves configured HA usernames to HA user IDs during
startup (`__main__.py:316-378`), from configuration defined and parsed at
`config.py:80` and `config.py:278`. Actor classification into `user`, `admin`,
and `super_admin` happens in `resolve_role` (`authz.py:205-213`) and requires
**both** identities for the highest role:

```python
if actor.is_admin and actor.user_id in identity.super_admins:
    return "super_admin"
if actor.is_admin:
    return "admin"
return "user"
```

Home Assistant admin alone is not sufficient. The actor must also appear on the
OpenClaw-side allowlist.

The dispatcher now enforces the role at invoke time for a caller that carries a
non-operator principal (see [Layer 1](#layer-1-hard-allowlist-enforcement)). What
is missing is the principal itself: the Gateway invoke envelope does not carry
session or actor context, so a Gateway-forwarded invoke reaches the dispatcher
as an operator call. The role model does not need further design; it needs a
trusted way to reach the dispatcher on the invoke path. For Assist turns relayed
by this node that path now exists (see
[Assist caller resolution](#assist-caller-resolution-and-the-host-session-key-hint)).

The add-on configuration states the principle for lifecycle commands:

> Authorization is the pairing-session bearer (`local_api_token`), this
> allowlist, and a native OpenClaw approval for each call.

## Three mutation classes, all on the Home Assistant side

Every destructive command this node registers acts on the Home Assistant
instance. None of them execute on the OpenClaw gateway host. The classes below
differ by mechanism, not by which machine is at risk.

### Class 1: Home Assistant API mutations

`ha.call_service`, the `ha.config.*` family, add-on lifecycle,
`ha.reload_config`, and `ha.update_install`.

These are structured API calls. They use OpenClaw plugin permission requests:
the Assist plugin is already an OpenClaw plugin, so a `before_tool_call` hook
returns `requireApproval` with `title`, `description`, `severity`,
`allowedDecisions`, `timeoutMs`, and `onResolution`, carried over
`plugin.approval.*`. These resolve through the same approval surfaces as chat
approval buttons and `/approve`.

### Class 2: Home Assistant filesystem mutations

`fs.write`, `fs.restore`, `fs.move`, `fs.delete`, and `fs.patch`.

These are neither shell nor HA API calls, and they are not currently gated in a
way this model can inherit. Protected roots return `PROPOSAL_REQUIRED` without an approval marker, but
allowed unprotected roots mutate immediately when the caller-supplied
`agent_bridge` parameter is false, which is its default
(`fs_write.py:224-250`, `fs_move_delete.py:191-228,381-414`,
`fs_patch.py:303-326`).

A caller-supplied boolean is not an authorization decision. This class takes the
same plugin permission request gate as Class 1, and the `agent_bridge`
self-authorization path is removed rather than preserved. Protected roots keep
their existing refusal until an approval is genuinely verified, so removing
self-authorization does not open them.

### Class 3: Home Assistant shell

`system.run` executes a command inside the add-on container. Its working
directory must resolve within the node's allowed roots or the Home Assistant
`/config` hierarchy, and the subprocess inherits only a minimal environment
with credential-shaped keys rejected.

It is exec, so it uses OpenClaw's node exec approvals with a canonical
`systemRunPlan`. The gateway rejects the run if `command`, `rawCommand`, `cwd`,
`agentId`, or `sessionKey` changed between prepare and the approved forward,
which supplies approval-bound-to-canonical-parameters without building it here.
A `proposalId` is never authorization. It is not part of the Gateway's native
`system.run` forward whitelist, so the transported request does not carry it
by default; a caller may set it as audit metadata, but mutating it cannot
permit an execution the canonical plan would not.

The node-side protocol methods that participate in this contract
(`system.run.prepare`, `system.execApprovals.get`, and `system.execApprovals.set`)
were delivered by #274. Their file-backed policy document uses a write lock in
the node's private data directory to serialize cooperating writers; the lock
narrows a rename race but does not itself define trust, because an attacker
able to rename files in that directory can already rewrite the policy
document. The trust boundary is the private data directory, not the lock. The
atomic `os.replace` is the commit point, and the two fallible post-commit
steps (snapshot read, lock teardown) are swallowed rather than raised so a
committed policy can never be reported as `IO_ERROR`.

`system.run` itself was re-gated onto this contract in #258. The handler now
accepts the Gateway-forwarded canonical plan (`command` argv, `systemRunPlan`,
`runId`, an approval signal — `approved=true`, `approvalDecision` in
`{allow-once, allow-always}`, or a non-empty `approvalSource` — plus optional
`cwd`/`rawCommand`/`env`/`timeoutMs`/`agentId`/`sessionKey`/`proposalId`) and
fails closed at the node entry unless every required envelope field is
present. It reuses the same argv/rawCommand/env/cwd validators the prepare
handler used, so a malformed forward is refused at the node for the same
reasons it would have been refused at prepare time, and it cross-checks the
forwarded argv / cwd / commandText / agentId / sessionKey against the stored
`systemRunPlan`: any mismatch is refused rather than trusted. The `cwd` is
re-resolved against the node's own allowed roots before the subprocess is
spawned; the subprocess inherits only a sanitised base environment (`PATH`,
`HOME`, `LANG`, `TZ`, `USER`, `TERM`, `LOGNAME`) plus caller-supplied entries
whose keys do not match `TOKEN`, `SECRET`, `KEY`, `PASS`, `CREDENTIAL`,
`AUTH`, or `PWD`. Timeout is read from `timeoutMs` (native wire, milliseconds),
and the successful payload returns the native exec wire contract
(`success`/`exitCode`/`timedOut`/`stdout`/`stderr`) so the exec tool parser
sees a well-formed result. The `proposalId` is accepted as audit metadata
only; it is not part of the Gateway's forward whitelist and travels only when
a caller supplies it.

`system.run` is retained and re-gated, not removed. Running scripts from the
Home Assistant directory is a supported use case.

Operators manage policy from the gateway:

```bash
openclaw approvals get --node <id|name|ip>
openclaw approvals set --node <id|name|ip>
openclaw approvals pending
openclaw approvals resolve <id> <allow-once|allow-always|deny>
```

## Principal ceiling

The three classes above govern what the agent may do *to* Home Assistant. They
do not govern **who is allowed to ask**.

Home Assistant Assist is a general entry point into OpenClaw. A person speaking
to Assist reaches the full agent, not only this node's commands. Household
members who are not operators can reach that entry point. The authorization
question is therefore about the principal driving the turn, not about the
command name.

The ceiling has two layers, deliberately redundant.

### Layer 1: hard allowlist enforcement

`forbidden_for_role` (`authz.py:216-223`) already computes the effective
forbidden set for a role from `_DEFAULT_FORBIDDEN` plus configured per-role
`add`/`remove` patches. For the `user` role that set already contains every
`fs.*` mutation, `system.run`, `ha.reload_config`, add-on lifecycle, and
`ha.call_service:*`.

That computed set is enforced at the dispatcher, which refuses the command
before any handler runs. It is not advisory, and it cannot be relaxed by
anything the requesting user says. Every dispatch takes a caller principal, and a
call site that supplies none is treated as the untrusted household `user`. The
local HTTP API and the Gateway WebSocket invoke path construct an operator
caller explicitly, because the bearer token and the paired session authenticate
an operator.

The `user` role is default-deny over the whole registry: only an explicit allowed
set is reachable (read-only commands plus the service-bearing commands the effect
policy governs), so a newly registered command is refused until it is classified.
The effect policy applies to `ha.call_service` and the light wrappers alike:

| Service class | Household `user` | HA `admin` / `super_admin` | Operator |
| --- | --- | --- | --- |
| Everyday control (table below) | allowed | allowed | allowed |
| Deny class (lifecycle, update, reload, host, shell, shutdown) | `SERVICE_DENIED` | `SERVICE_DENIED` | `SERVICE_DENIED` |
| Any service outside the table | `PERMISSION_DENIED` | `APPROVAL_REQUIRED` (no approval path exists yet) | allowed, logged (temporary) |

The everyday-control table (`HOUSEHOLD_ALLOWED_SERVICES` in `authz.py`) is the
one source read by the gate, the effect policy and the printed disclaimer. A
household member is an adult, so the user tier allows what an adult in the house
reasonably does; the block list is for system-level things only. Admin is never
stricter than user for any service.

There are no open domains: every allowed service is listed by name, taken from
Home Assistant's own service definitions, so a service HA adds later, and any
service that edits configuration (`input_select.set_options`, `scene.create`,
every `reload`), is refused until it is reviewed and listed.

| Domain | Allowed services |
| --- | --- |
| light, switch, input_boolean | `turn_on`, `turn_off`, `toggle` |
| media_player | `turn_on`, `turn_off`, `toggle`, `media_play`, `media_pause`, `media_play_pause`, `media_stop`, `media_next_track`, `media_previous_track`, `volume_up`, `volume_down`, `volume_mute`, `volume_set`, `select_source`, `select_sound_mode` |
| cover (incl. garage and gate) | `open_cover`, `close_cover`, `stop_cover`, `toggle`, `set_cover_position`, and the tilt variants |
| climate | `turn_on`, `turn_off`, `set_temperature`, `set_hvac_mode`, `set_fan_mode`, `set_preset_mode`, `set_humidity` |
| fan | `turn_on`, `turn_off`, `toggle`, `set_percentage`, `set_preset_mode`, `oscillate`, `set_direction` |
| input_select | `select_option`, `select_next`, `select_previous`, `select_first`, `select_last` |
| input_number | `set_value`, `increment`, `decrement` |
| vacuum | `start`, `pause`, `stop`, `return_to_base`, `locate`, `clean_spot` |
| humidifier | `turn_on`, `turn_off`, `toggle`, `set_humidity`, `set_mode` |
| water_heater | `turn_on`, `turn_off`, `set_temperature`, `set_operation_mode` |
| remote | `turn_on`, `turn_off`, `toggle`, `send_command` |
| scene, script | `turn_on` only |
| button | `press` only |
| lock | `lock`, `unlock`, `open` |
| alarm_control_panel | `alarm_arm_away`, `alarm_arm_home`, `alarm_arm_night`, `alarm_arm_vacation`, `alarm_arm_custom_bypass`, `alarm_disarm` |

The printed exception lists exactly the `domain.service` entries that the real
`is_forbidden` check allows for that turn, so a config patch (even a glob such
as `ha.call_service:l*.turn_on`) is reflected as enforced.

Privilege never inverts. A service prohibition added to the `admin` role's
patch also binds `user`, and one added to `super_admin` also binds `admin` and
`user` (fail closed, logged once), so a more privileged role is never stricter
than a less privileged one.

Security devices follow Home Assistant's own model, with no node-side block: a
lock or alarm that has a code requires it in the service call and HA validates
it. The node never stores or guesses a code. For `lock` and `alarm_control_panel`
only, the service's own top-level `code` is sent as text, as HA's `cv.string`
would read it (a boolean, non-finite number, list or object is `INVALID_PARAM` before any HA
request); every other value, including a nested `code` in script variables, is
sent exactly as supplied. The node never logs a supplied code value, and does not return it
in any form it can recognise (its textual forms inside strings, or equal values).
Identifiers the node logs as routing context (command names, HA domain and service
names, entity IDs) are logged as-is, so a code identical to one of those identifiers
is not protected; an integration that transforms the code (e.g. hashes or re-encodes it)
is also outside this guarantee. An HA rejection of a missing or wrong code is returned as a
readable error.

`PERMISSION_DENIED` is a role refusal; `SERVICE_DENIED` is the effect refusal and
applies to every caller. A malformed service name is refused for non-operators.

For the `user` role, the entries of `USER_FORBIDDEN_COMMANDS` and the
`ha.call_service:*` wildcard are non-removable: a config `remove` naming one is
ignored with a warning, so the disclaimer always renders the refused set the
gate enforces (the generic service restriction plus the everyday-control table).
`add`, and removal of other added entries, still work.

**One source of truth, two consumers.** The same `forbidden_for_role` result
feeds both the enforcement gate and the prompt disclaimer. Enforcement and
disclosure cannot drift, which is the usual failure mode when a policy is
written down twice.

### Layer 2: actor-aware prompt context

`build_disclaimer` (`authz.py:240-262`) already injects a per-turn block naming
the actor, the role, and the forbidden list, with explicit anti-echo and
anti-override framing.

This layer is retained, not replaced. Its purpose is behavioral rather than
protective: the agent knows it is not talking to the operator, refuses
coherently, and can explain the refusal instead of failing opaquely. Layer 1 is
what makes the refusal true.

### Known limit under the no-gateway-changes constraint

Layer 1 is enforceable at this node for this node's commands, with no gateway
change.

It does **not** constrain OpenClaw's own tool surface. An Assist turn reaches
the full agent, including gateway exec and every other plugin, and this node
cannot restrict those. Reviewing the documented `chat.send` parameters found no
turn-scoped tool policy, so there is no evident way for the node to carry a
capability ceiling into the gateway for a single turn.

Two options exist, and the choice is the operator's:

1. Route non-operator HA users to a different `agentId` with its own narrower
   tool policy. `resolve_agent_id` (`authz.py:226-232`) already performs this
   mapping, so it is configuration rather than code. It changes which assistant
   a household member talks to.
2. Keep one agent and accept that, until a turn-scoped ceiling exists, the
   enforced guarantee covers this node's command surface and not the gateway's
   full tool surface.

The selected default (D4) is option 2 with a soft prompt-level block: one
agent, with a separate agent configurable per user (option 1). The enforced
guarantee covers this node's command surface on the wrapper path, where Assist
turns carry the resolved caller to the dispatcher. The gateway's full tool
surface is not covered, and this document does not claim it is. The gap is
recorded so it is not mistaken for a delivered property.

### Assist caller resolution and the host session key hint

The plugin carries the host session key of the running turn in the reserved
`_openclaw_caller` field of `node.invoke` params. The node pops that field
before dispatch and uses it only as a lookup hint into its own registry of
in-flight Assist turns; the role always comes from the registry entry, never
from the field.

The hint is not authenticated. A caller who can call `node.invoke` directly
(operator level) could supply a guessed host session key and be resolved as
whichever active turn owns it. This is an **accepted limitation for now**,
tracked on [#275](https://github.com/clawd-ops/openclaw-hass-node/issues/275).
It does not widen access beyond what operator-level callers already have:
a direct `node.invoke` without the field is already an operator call. The
lookup compares the hint, case-insensitively, with each active turn's raw and
gateway-canonical session key. The caller resolves only when exactly one distinct
turn matches. Ambiguous hints are refused: any hint matching more than one active
turn is refused, for example conversation ids differing only by case (Assist
accepts them), or an unqualified household turn's canonical key equal to an admin
turn's raw key. There is no tie-break.

Trade-off: two concurrent Assist conversations whose IDs differ only by letter
case are both refused; HA generates conversation IDs, so this is not expected in
practice.

The default is one agent plus a prompt-level block, with a separate agent
configurable per user (option 1 above).

## Implementation gap

The gap is entirely within this repository.

- `commands/dispatcher.py` registers `system.run.prepare`,
  `system.execApprovals.get`, and `system.execApprovals.set` (delivered by
  #274), so the node participates in exec approvals.
- `commands/system_run.py` is bound to the Gateway-forwarded canonical plan
  (#258). Direct
  `nodes.invoke system.run` remains refused by the Gateway; execution is
  reachable through `exec host=node` after approval, and the node re-runs
  argv/rawCommand/env/cwd validation on the forward before the subprocess is
  spawned.
- The dispatcher-level gate exists and `forbidden_for_role` feeds both it and the
  disclaimer. The Assist principal reaches it only on the wrapper path:
  Gateway-forwarded invokes without the plugin hint and the local HTTP API are
  operator calls, so **direct `node.invoke` is operator-default**. The selected
  D4 default (one agent plus a soft prompt-level block) does not cover the
  gateway's full tool surface, which is not claimed as delivered.

## Consequences for the roadmap

Phase 2 contracts. The following planned items are native behavior and will be
adopted rather than built:

- durable proposal states and lifecycle
- binding an approval to node, principal, command, and canonical parameters
- an independent authenticated human approval surface
- blocking self-approval
- the add-on ingress approval UI

Retained, because this model does not deliver them:

- wiring accepted approvals to protected `fs.*` mutations
- consuming native approvals for the `require_approval` outcome (today it is a
  refusal)
- per-service classification beyond the light exception, with target and data
  constraints
- invoke-time actor propagation, so the dispatcher gate applies on the Gateway
  invoke path

The fail-closed behavior introduced in PR #265 remains the default: without a
valid native approval marker every gated mutation is refused, so there is no
window in which unverified identifiers are accepted.

## Prompt-level versus enforced

Three layers protect a household turn. They guarantee different things.

| Layer | What it guarantees | Strength |
| --- | --- | --- |
| Per-turn authorization block | Tells the agent the caller's role, the HA services that role may call, and that OpenClaw use is limited to conversation, web search and memory search. The block is fenced by fixed markers; any copy of those markers in the user's text is replaced before the block is prepended, so an utterance cannot forge a block or close the real one. | **Soft.** It is an instruction to a model, not a control. |
| Node dispatcher | Refuses a `ha.*` action the role may not perform, before any request reaches Home Assistant. The block's description of the allowed services is generated from the same table the dispatcher enforces. | **Hard**, for HA actions. |
| Separate restricted agent | The agent's own OpenClaw tool policy denies exec, sessions, messaging, file writes, browser and similar tools. Selected per HA user with `identity.user_agent_map`, or per role with `identity.user_role_agent_id` and `identity.admin_role_agent_id`. | **Hard**, for OpenClaw tools, only when configured. |

Consequences:

- With no separate agent (the default), a household user talks to the same
  agent as everyone else. The OpenClaw-side limits rest on the model following
  the block. A determined or lucky prompt can talk a model out of it; the HA
  side stays enforced by the dispatcher regardless.
- A restricted agent closes that gap, at the cost of a second agent to
  configure and keep current, and of that agent not sharing the main agent's
  memory or tools.
- Content returned by tools, entities, web pages or memory is data, not
  instructions. The block says so, but this too is a prompt-level statement.

## Native approvals for node mutations

Scope: every mutating action of the nine `ha.config.*` commands, the protected-path
(or `agent_bridge`) writes of `fs.write`, `fs.restore`, `fs.move`, `fs.delete` and
`fs.patch`, and the Tier B admin commands `ha.reload_config`, `ha.update_install`,
`ha.addon_start`, `ha.addon_stop`, `ha.addon_restart` and `ha.addon_update`. The list lives in `contracts/approval-gated-commands.json`; the plugin
table and the node handlers are each tested against that file. Without a valid
marker these still return `PROPOSAL_REQUIRED`.

Certain-to-fail calls are refused in the hook, before any prompt: an unknown
parameter, a `fs.*` path under `/config/.storage/`, and a malformed, core, or
hard-denied add-on slug. These are the checks the node makes before its approval
gate. `contracts/approval-preflight.json` carries them and a node test asserts it
equals the node's own tables, so they cannot drift. The add-on lifecycle
allowlist (node environment config) and parameter value shapes are known only to
the node, so those refusals still arrive after a prompt
([#403](https://github.com/clawd-ops/openclaw-hass-node/issues/403)).

The Tier B commands are reached two ways, and both are covered: through the core
`nodes` tool, and through the plugin's own `ha_reload_config`, `ha_update_install`,
`ha_addon_start`, `ha_addon_stop`, `ha_addon_restart` and `ha_addon_update` tools.
For the plugin tools the `before_tool_call` hook matches the tool name, builds the
marker over the exact node params the tool will send (one shared function builds
them for both the hook and the tool), and the tool forwards the reserved
`_openclaw_approval` field to the node. Like the `fs.*` commands they have no action
param, so the bound action is the empty string. On the node, the add-on slug
allowlist, the always-deny list, and parameter validation run first; the marker is
checked and consumed only after them, so a policy refusal never uses up an
approval. There is no plugin config for any of this; the plugin's config schema is
empty.

Flow:

1. The agent calls the core `nodes` tool (`action: "invoke"`, `invokeCommand`,
   `invokeParamsJson`). The plugin's `before_tool_call` hook matches
   a gated command and action (table above) and returns a
   `requireApproval` request (`allow-once` or `deny`, ten-minute timeout) plus a
   params override. The hook creates the marker at this point; OpenClaw applies
   it only after approval succeeds. A gated call holding an integer beyond 2^53 is
   blocked, because re-serializing it would change what the operator approved.
2. OpenClaw asks the operator and applies the override only if the approval
   succeeds. The override adds the reserved field `_openclaw_approval`:
   `{id, exp, bind}`, where `id` is a random UUID, `exp` is epoch seconds (the
   ten-minute approval window plus a two-minute dispatch grace, fixed when the
   hook returns), and `bind` is the sha256 hex of the canonical JSON (sorted
   keys ordered by UTF-16 code unit, no whitespace, UTF-8, numbers as `JSON.stringify` prints them) of `{command, action, params}` with reserved
   fields removed. Denial and timeout never reach the node.
3. The node strips the field and runs the mutation only if the marker is
   well-formed, unexpired, bound to exactly this command, action and params, and
   its id has not been used. Anything else is refused with `APPROVAL_INVALID`
   and no HA request; a missing marker is `PROPOSAL_REQUIRED` as before. Used ids
   are kept in memory until they expire.

The `fs.*` commands have no action param, so their bound action is the empty string.
For them the node decides: a protected path (or `agent_bridge`) needs the marker,
checked once even where several paths are checked (`fs.move`); an unprotected path
ignores the marker, so the plugin can require approval for every write command
without a protected-path lookup of its own. An ignored marker is never forwarded.

On every `nodes` invoke the hook also strips any marker the model supplied, and for the plugin's Tier B tools it replaces any model-supplied marker with its own.

The requester never approves its own call. Where approval prompts are delivered
is gateway configuration (`approvals.plugin`) and belongs to the operator; this
repository does not change it. The digest is implemented identically in
TypeScript and Python and checked against a hand-written fixture
(`contracts/approval-bind-fixture.json`) and a generated one of 300 random
values (`contracts/approval-bind-generated.json`).

**Known gap.** The marker is not a secret. An operator-level caller that skips
the tool hook, for example a shell `openclaw nodes invoke`, can compute a valid
marker itself. This is the same trust model as
[#275](https://github.com/clawd-ops/openclaw-hass-node/issues/275): operator
access is not defended against here.

Future work: (A) a shared secret kept in a protected store so only the gateway
can mint markers; (B) an out-of-band approval code delivered by a Home Assistant
actionable notification.

## Open validation

The gateway approval APIs are confirmed to exist and to be in live production
use, including node-scoped exec approvals resolved by an operator device. The
node-side protocol methods for the exec-approval path
(`system.run.prepare`, `system.execApprovals.get`, `system.execApprovals.set`)
are now implemented (#274) and `system.run` itself is bound to the approved
plan (#258). What is **not** yet proven is a live operator-surface
allow/deny cycle observed end to end against this node.

### Exec approval path (Class 3)

- a real approval request originating from this node reaches an operator surface
- an `allow-once` decision permits exactly one execution
- a `deny` decision blocks execution
- a mutated plan between prepare and forward is rejected
- `askFallback` denies when no operator surface is reachable

### Plugin approval path (Classes 1 and 2)

- the approval is bound to the exact command, action, target, parameters, and
  trusted HA actor, and a change to any of them invalidates it
- denial, timeout, absent approval UI, plugin failure, and replay all fail closed
- an `allow-once` decision is consumed exactly once
- direct `node.invoke`, generic-service aliases, and dedicated wrappers all
  converge on the same decision and none can bypass it

### Principal ceiling

- a `user`-role actor is refused at the dispatcher, before the handler runs,
  for every command outside the allowed set and every command in the computed
  forbidden set (covered by tests at source; the live invoke path needs
  principal propagation first)
- the refusal holds against prompt-injection and role-play override attempts,
  because it is not evaluated by the model
- the disclaimer and the enforced set are derived from the same call and cannot
  disagree
- approval prompts route to operator devices and never to the requesting
  non-operator actor
