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

Everything below is consumption of APIs the gateway already exposes, plus
deletion of the parallel implementation in this repository.

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
startup and classifies every actor as `user`, `admin`, or `super_admin`
(`__main__.py:316-378`, `config.py:80` and `config.py:278`).

Today that classification is advisory only. `authz.py` states its own limit:

> It is prompt-level protection; hard invoke-time enforcement is intentionally
> out of scope until the gateway invoke envelope carries session/actor context.

Promoting this from prompt-level to invoke-time enforcement is the substantive
remaining work. The role model itself does not need to be designed; it needs to
be believed by the dispatcher.

The add-on configuration already states the principle for lifecycle commands:

> Authorization is the pairing-session bearer (`local_api_token`) plus this
> allowlist, no separate admin token.

## Two blast radii, two native gates, one operator workflow

Destructive operations in this project fall into two classes that require
different OpenClaw mechanisms.

### Host and OpenClaw blast radius: `system.run`

`system.run` executes shell on the host. It is exec, and OpenClaw already gates
exec on nodes. This project must stop shadowing the native command and instead
participate in the native flow.

The gateway requires a canonical `systemRunPlan` (`argv`, `cwd`, `rawCommand`,
session metadata) on `exec.approval.request` when `host=node`. After approval,
the forwarded `system.run` reuses that stored plan, and the gateway rejects the
run if `command`, `rawCommand`, `cwd`, `agentId`, or `sessionKey` changed
between prepare and forward.

That property, approval bound to canonical parameters, is a requirement this
project had planned to build. It is obtained by adopting the native path.

Operators manage policy from the gateway:

```bash
openclaw approvals get --node <id|name|ip>
openclaw approvals set --node <id|name|ip>
openclaw approvals pending
openclaw approvals resolve <id> <allow-once|allow-always|deny>
```

`system.run` is retained, not removed. Running scripts on the host is a
supported use case. It is re-gated, not deleted.

### Home Assistant blast radius: service calls and config mutation

`ha.call_service`, the `ha.config.*` family, and add-on lifecycle are structured
API calls, not shell. They do not fit exec approvals and must not be
disguised as shell to borrow that path, because doing so would corrupt the
audit record.

These use OpenClaw plugin permission requests. The Assist plugin is already an
OpenClaw plugin, so it can gate a call from a `before_tool_call` hook that
returns `requireApproval` with `title`, `description`, `severity`,
`allowedDecisions`, `timeoutMs`, and `onResolution`, carried over
`plugin.approval.*`.

These resolve through the same approval surfaces as chat approval buttons and
`/approve`.

### Why this still reads as one workflow

On a general-purpose node every destructive action is a shell command, so exec
approvals cover the whole surface and there is one visible gate. This node is
different only because part of its blast radius is reached through an API
rather than a shell.

The operator still sees one workflow: the same prompt surfaces, the same
`allow-once` / `allow-always` / `deny` decisions, and the same durable record.
Two mechanisms exist underneath because two kinds of thing are being
authorized.

## Implementation gap

The gap is entirely within this repository.

- `commands/dispatcher.py:105` registers `system.which` only. `system.run.prepare`
  and `system.execApprovals.get/set` are unimplemented, so the node cannot
  participate in exec approvals at all.
- `commands/system_run.py` ships a parallel `system.run` that shadows OpenClaw's
  native command (#258) and carries the inert gate.

## Consequences for the roadmap

Phase 2 contracts significantly. The following planned items are native
behavior and will be adopted rather than built:

- durable proposal states and lifecycle
- binding an approval to node, principal, command, and canonical parameters
- an independent authenticated human approval surface
- blocking self-approval
- the add-on ingress approval UI

The interim fail-closed behavior introduced in PR #265 remains in force until
the native path is proven end to end, so there is no window in which unverified
identifiers are accepted.

## Open validation

The gateway approval APIs are confirmed to exist and to be in live production
use, including node-scoped exec approvals resolved by an operator device. What
is **not** yet proven is that this node can drive them end to end, because the
two methods above are unimplemented.

Required evidence before this model is treated as delivered:

- a real approval request originating from this node reaches an operator surface
- an `allow-once` decision permits exactly one execution
- a `deny` decision blocks execution
- a mutated plan between prepare and forward is rejected
- `askFallback` denies when no operator surface is reachable
