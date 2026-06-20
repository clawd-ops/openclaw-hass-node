# 2026-06-20 — Addon command surface (read-only + admin-gated tiers)

Companion to `HANDOFF-2026-06-20-streaming-followups.md` item #11 ("Sunset HA MCP → node-tool path with read-only blast-radius guards"). Captures the tiered plan agreed with Rob on 2026-06-20.

## Why the tiering

Item #11 commits the node tool surface to being **software-blocked** read-only for subagents — not just prompt-instructed. Mixing state-changing commands into the same surface defeats that property. So commands are grouped by risk tier and the subagent allowlist will only ever include Tier A.

## Tier A — read-only, subagent-safe

No gate beyond "must be invokable by the node". Live on the subagent allowlist once #11 ships.

Already implemented:

- `ha.addon_logs` — `GET /addons/<slug>/logs` (merged in PR #132).
- `ha.list_addons` — `GET /addons/<slug>` listing (PR pending review on the same branch; required as the discovery path for `ha.addon_logs`).

Proposed, not yet implemented:

- `ha.addon_info` — `GET /addons/<slug>/info` (state, version, network, ingress URL; **must strip `options` values and any secret/token-shaped fields** at the boundary).
- `ha.addon_stats` — `GET /addons/<slug>/stats` (CPU %, memory MB, network bytes).
- `ha.addon_changelog` — `GET /addons/<slug>/changelog`.
- `ha.addon_documentation` — `GET /addons/<slug>/documentation`.

Each Tier A command must:

- Use `supervisor_get_text` / `supervisor_get_json` (URL is always built as `http://supervisor{path}` — never combined with a user-supplied URL).
- Apply a fixed field allowlist before returning, never `return raw` (`ha.list_addons` is the canonical example).
- Validate the slug against the existing `_valid_addon_slug` rule.
- Cap response size (`supervisor_get_text` keeps a bounded 1 MiB tail; `supervisor_get_json` should likely get a comparable bound — TODO).

## Tier B — lifecycle, admin-gated, NEVER on the subagent allowlist

Reserved for Clawd-as-Rob or Rob himself. Same `OPENCLAW_ADMIN_TOKEN` gate as `ha.reload_config`.

- `ha.addon_start` — `POST /addons/<slug>/start`
- `ha.addon_stop` — `POST /addons/<slug>/stop`
- `ha.addon_restart` — `POST /addons/<slug>/restart`

Additional constraints on top of the admin-token gate:

- **Slug allow/deny list at addon-config level.** Always deny `homeassistant`, `supervisor`, and `core_*` regardless of token. Other slugs default-deny, opt-in.
- **Audit log every invocation** with caller identity once handoff item #1 (user mapping) lands. Until then, log the gateway session key + the admin-token presence boolean.
- **Idempotent shape.** Start-of-already-started and stop-of-already-stopped should return `{ok: True, state: "<current>"}` rather than the underlying Supervisor 400.

## Tier C — install / uninstall / update / rebuild — NOT adding

Easy to brick the HA instance; sequencing requires careful UX (changelog read, options preservation, backup). Out of scope until there is a specific, scoped request. If Rob ever wants them, separate proposal — not just an opportunistic add.

## Open question: read of `options`

Several legitimate Tier A use cases want to *see* addon options (e.g., "what hass_url is this addon configured with?"). The current allowlist drops options entirely to avoid leaking secrets. Two design choices when we get to `ha.addon_info`:

1. **Drop options entirely (status quo from `list_addons`).** Safest. Subagents lose option-introspection.
2. **Return option keys only, never values.** Reveals schema, hides secrets. Likely the right balance.

Decide before implementing `ha.addon_info`.

## Process notes for the next session

- **Codex review path.** Use `sessions_spawn` with `model: openai/gpt-5.3-codex`. Do NOT use `codex exec` — Rob disparaged that 2026-06-20 and the CLI has been OOM-killing on long diffs anyway.
- **PR cadence.** One tier-A command per PR (or one small batch), each individually reviewable. Tier B lands separately, behind its own PR with the allowlist config wired up first. Tier C never lands without a fresh scoped ask.
- **Cross-link with item #11.** The subagent-side allowlist enforcement is a node-side change (likely in `commands/dispatcher.py` or a new policy layer) and must land **before** any subagent path is wired to call these commands. Order: ship the commands → ship the policy → wire the subagent path.
