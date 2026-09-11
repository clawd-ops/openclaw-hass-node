# Authorization model

Status: ratified 2026-09-11. Supersedes the `OPENCLAW_ADMIN_TOKEN` design
described in earlier revisions of `COMMAND-TIERS.md`, `COMPLETION-ROADMAP.md`,
and `INSTALL.md`.

## Decision

There is no add-on-specific admin token. Authorization comes from two
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

## Why the admin token had to go

`OPENCLAW_ADMIN_TOKEN` was not merely undesirable. It was already inert.

The variable appears in neither the `options:` block nor the `schema:` block of
`app/config.yaml`. There is no add-on UI field for it and no supported way to
set it, so the environment variable is empty in every real deployment. The gate
fails closed on empty:

```python
# app/node/src/openclaw_node/commands/ha.py
required = os.environ.get("OPENCLAW_ADMIN_TOKEN", "")
if not required:
    return _error("PERMISSION_DENIED", f"{command}: admin gate not configured")
```

Three commands sat behind that gate and were therefore unreachable:

| Command | Gate site |
| --- | --- |
| `system.run` | `commands/system_run.py:128-136` |
| `ha.reload_config` | `commands/ha.py:440` |
| `ha.update_install` | `commands/ha.py:1103` |

The generated coverage ledger reached the same conclusion independently,
reporting `ha.reload_config` and `ha.update_install` with status `fail`.

PR #270 already moved the four add-on lifecycle commands off the token and onto
paired-session authentication plus slug policy. This document finishes that
direction for the remaining commands rather than preserving a split model.

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

Today that classification is advisory only. `authz.py` states its own limit:

> It is prompt-level protection; hard invoke-time enforcement is intentionally
> out of scope until the gateway invoke envelope carries session/actor context.

Promoting this from prompt-level to invoke-time enforcement is the substantive
remaining work. The role model itself does not need to be designed; it needs to
be believed by the dispatcher.

The add-on configuration already states the principle for lifecycle commands:

> Authorization is the pairing-session bearer (`local_api_token`) plus this
> allowlist, no separate admin token.

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
way this model can inherit. Protected roots return `PROPOSAL_REQUIRED`, but
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
A proposal ID travels with the request as audit metadata; it is never itself
an authorization token, and mutating it does not permit an execution the
canonical plan would not.

The node-side protocol methods that participate in this contract
(`system.run.prepare`, `system.execApprovals.get`, and `system.execApprovals.set`)
were delivered by #274. Their file-backed policy document uses a write lock in
the node's private data directory to serialize cooperating writers; the lock
narrows a rename race but does not itself define trust, because an attacker
able to rename files in that directory can already rewrite the policy
document. The trust boundary is the private data directory, not the lock. The
atomic `os.replace` is the commit point, and the two fallible post-commit
steps (snapshot read, lock teardown) are swallowed rather than raised so a
committed policy can never be reported as `IO_ERROR`. Re-gating `system.run`
itself onto this contract, and removing the inert token gate, is tracked as
#258 and is out of scope for this document.

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

That computed set becomes an enforced allowlist at the dispatcher, refusing the
command before any handler runs. It is not advisory, and it cannot be relaxed by
anything the requesting user says.

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

This document does not claim the cross-surface ceiling is solved. The gap is
recorded so it is not mistaken for a delivered property.

## Implementation gap

The gap is entirely within this repository.

- `commands/dispatcher.py` now registers `system.run.prepare`,
  `system.execApprovals.get`, and `system.execApprovals.set` (delivered by
  #274), so the node can participate in exec approvals. What remains is that
  `system.run` itself has not yet been re-gated onto that contract.
- `commands/system_run.py` still ships a `system.run` that shadows OpenClaw's
  native command (#258) and carries the inert token gate. Re-gating it onto
  exec approvals and deleting `_admin_token_ok` is the follow-up tracked in
  #258.
- No dispatcher-level role gate exists. `forbidden_for_role` has exactly one
  consumer today, the disclaimer.

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
- per-service effect policy for `ha.call_service`
- invoke-time actor propagation and dispatcher enforcement

The interim fail-closed behavior introduced in PR #265 remains in force until
the native path is proven end to end, so there is no window in which unverified
identifiers are accepted.

## Open validation

The gateway approval APIs are confirmed to exist and to be in live production
use, including node-scoped exec approvals resolved by an operator device. The
node-side protocol methods for the exec-approval path
(`system.run.prepare`, `system.execApprovals.get`, `system.execApprovals.set`)
are now implemented (#274). What is **not** yet proven is that this node can
drive them end to end, because `system.run` itself is not yet re-gated onto
the approved plan (#258) and no live operator-surface allow/deny cycle has
been observed against this node.

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
  for every command in the computed forbidden set
- the refusal holds against prompt-injection and role-play override attempts,
  because it is not evaluated by the model
- the disclaimer and the enforced set are derived from the same call and cannot
  disagree
- approval prompts route to operator devices and never to the requesting
  non-operator actor
