# Command Surface

Commands the node exposes via `node.invoke`. Group prefixes match
OpenClaw conventions where they exist.

## `fs.*` — filesystem

| Command       | Args                                | Notes                     |
|---------------|-------------------------------------|---------------------------|
| `fs.read`     | `path`, `offset?`, `limit?`         | Direct                    |
| `fs.list`     | `path`, `recursive?`, `glob?`       | Direct                    |
| `fs.stat`     | `path`                              | Direct                    |
| `fs.glob`     | `pattern`, `cwd?`                   | Direct                    |
| `fs.write`    | `path`, `content`, `mode?`          | **Proposal-gated** under protected roots |
| `fs.patch`    | `path`, `patch` (unified diff)      | **Proposal-gated** under protected roots |
| `fs.move`     | `src`, `dst`                        | **Proposal-gated** under protected roots |
| `fs.delete`   | `path`                              | **Proposal-gated** under protected roots; uses `trash-cli`, never `rm` |
| `fs.restore`  | `trash_id?` or `path?`              | Restore from trash                     |

Protected roots: `/config`, `/addons`, `/ssl`. Writes there always
generate an agent-bridge `propose_edit` and wait for `resolve_proposal`.

## `system.*` — shell

| Command            | Args                          | Notes                  |
|--------------------|-------------------------------|------------------------|
| `system.run`       | `cmd`, `cwd?`, `env?`, `timeout?` | Requires `operator.admin` |
| `system.which`     | `binary`                      |                        |
| `system.env`       | —                             | Sanitized              |

## `ha.*` — Home Assistant control

| Command                  | Notes                                  |
|--------------------------|----------------------------------------|
| `ha.list_states`         | All entities and current state         |
| `ha.get_state`           | One entity                             |
| `ha.list_services`       | Service catalog                        |
| `ha.call_service`        | domain, service, target, data          |
| `ha.list_areas`          |                                        |
| `ha.list_devices`        |                                        |
| `ha.list_entity_registry`|                                        |
| `ha.list_automations`    | + traces                               |
| `ha.reload_config`       | core.check_config + reload domain      |
| `ha.logbook`             | entity, start, end                     |
| `ha.history`             | entity, start, end                     |

## `ha.config.*` — domain config editing

Detects YAML vs UI storage mode per domain and routes to the right
path. See `HA-CONFIG-EDITING.md` for the per-domain breakdown.

| Command                              | Notes                                           |
|--------------------------------------|-------------------------------------------------|
| `ha.config.automations.list`         |                                                 |
| `ha.config.automations.get`          | id                                              |
| `ha.config.automations.set`          | id, config — proposal-gated                     |
| `ha.config.automations.delete`       | id — proposal-gated                             |
| `ha.config.scripts.*`                | same shape as automations                       |
| `ha.config.scenes.*`                 | same shape                                      |
| `ha.config.lovelace.get`             | dashboard?                                      |
| `ha.config.lovelace.set`             | dashboard?, config — proposal-gated             |
| `ha.config.blueprints.list`          | by domain                                       |
| `ha.config.blueprints.get`           | path                                            |
| `ha.config.blueprints.set`           | path, content — proposal-gated                  |
| `ha.check_config`                    | call before any reload                          |

## `docs.*` — versioned HA docs lookup

| Command           | Args                              | Notes                                  |
|-------------------|-----------------------------------|----------------------------------------|
| `docs.lookup`     | `topic`, `version?`               | Defaults to running core version       |
| `docs.search`     | `query`, `version?`               | Local-cache backed search              |
| `docs.versions`   | —                                 | Available cached versions              |

## `ha.supervisor.*` — Supervisor API (add-on mode only)

| Command                       | Notes                          |
|-------------------------------|--------------------------------|
| `ha.supervisor.info`          |                                |
| `ha.supervisor.addons.list`   |                                |
| `ha.supervisor.addons.info`   |                                |
| `ha.supervisor.addons.restart`| Proposal-gated                 |
| `ha.supervisor.snapshots.*`   | Proposal-gated                 |

## `assist.*` — conversation agent

Shape TBD pending P1 research. Likely:

| Command              | Notes                                                |
|----------------------|------------------------------------------------------|
| `assist.turn`        | Inbound conversation turn from HA → gateway          |
| `assist.reply`       | Outbound reply from gateway → HA                     |

If Plan B (HACS shim) is required, the shim posts to
`http://<node>/assist/turn` and the node forwards via gateway WS.
