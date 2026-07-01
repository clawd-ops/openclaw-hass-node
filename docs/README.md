# openclaw-hass-node documentation

Navigation hub for everything under `docs/`. New here? Start with
[Orientation](#orientation). Resuming a session post-compaction? Start
with [`MEMORY.md`](MEMORY.md).

## Orientation

- [`MEMORY.md`](MEMORY.md) — durable build memory for agents resuming
  after context compaction. Architecture snapshot, what's live, where
  to find everything.
- [`STATUS.md`](STATUS.md) — canonical "where we are right now."
  Current release, what works end-to-end, what's broken, what's next.
- [`TODO.md`](TODO.md) — open work plus the most-recently merged PRs
  in ascending order.
- [`INSTALL.md`](INSTALL.md) — user-facing step-by-step install
  walkthrough (gateway allowlist, addon config, HACS install,
  verification).
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — commit policy, version bump
  rules, cross-provider review workflow, doc-only PR shortcuts.

## Design (`design/`)

The "why" reference. Architecture, identity model, blast-radius policy.

- [`design/PLAN.md`](design/PLAN.md) — full design doc: goals,
  architecture, surfaces in detail, decision log, packaging + repo
  layout.
- [`design/IDENTITY-AND-SCOPES.md`](design/IDENTITY-AND-SCOPES.md) —
  canonical identity + role model. Actor signing (HMAC subkey derived
  from `local_api_token`), role resolution
  (`user`/`admin`/`super_admin`), per-role forbidden-command tables,
  per-user agent routing.
- [`design/COMMAND-TIERS.md`](design/COMMAND-TIERS.md) — Tier A/B/C
  blast-radius policy. What each tier means, what's in scope per
  tier, audit/guard requirements.

## Reference (`reference/`)

Live registries and domain maps that change when shipped code changes.

- [`reference/COMMAND-SURFACE.md`](reference/COMMAND-SURFACE.md) —
  canonical live command registry. Every command the node exposes,
  per-tier constraints. If counts change, this file owns the change.
- [`reference/HA-CONFIG-EDITING.md`](reference/HA-CONFIG-EDITING.md) —
  domain API map: HA-native API first, `fs.patch` fallback,
  `.storage` read-only hard rule.
- [`reference/BACKUPS.md`](reference/BACKUPS.md) — per-file versioning
  design: content-addressed store, index format, restore, retention.

## Operations (`operations/`)

How the project ships, validates, and remembers its own mistakes.

- [`operations/RELEASE.md`](operations/RELEASE.md) — canonical release
  procedure. Auto-cut via
  `.github/workflows/release-on-version-bump.yml` on version bump.
  Manual recipe preserved as emergency fallback.
- [`operations/QUALITY.md`](operations/QUALITY.md) — CI gates (ruff,
  ruff format, mypy strict, pytest coverage, bandit, pip-audit).
- [`operations/UAT-PLAN.md`](operations/UAT-PLAN.md) — user acceptance
  testing checklist.
- [`operations/LESSONS.md`](operations/LESSONS.md) — postmortems and
  "I burned a day on this" recipes. Append-only. Read before changing
  the connect frame, Dockerfile, or addon config.

## Research (`research/`)

Historical design rationale. Read for "why is it this way?" — not for
current state.

- [`research/AGENT-BRIDGE-CONNECTIVITY.md`](research/AGENT-BRIDGE-CONNECTIVITY.md)
  — why the node goes through the gateway, not direct to agent-bridge.
- [`research/CONVERSATION-AGENT.md`](research/CONVERSATION-AGENT.md) —
  why the HACS integration is required (HA core constraint on
  conversation-agent registration).
- [`research/OPENCLAW-INTEGRATION.md`](research/OPENCLAW-INTEGRATION.md)
  — P5.11 postmortem that killed the parallel-brain direction.
- [`research/MIGRATION.md`](research/MIGRATION.md) — MCP server
  retirement readiness tracking.

---

## Source of truth — when a fact changes, update *one* file

| Fact that changes | Canonical doc |
|---|---|
| Current shipped version | `app/config.yaml` + `app/build.yaml` (code is the source); [`STATUS.md`](STATUS.md) references it. |
| Current state (what works end-to-end) | [`STATUS.md`](STATUS.md) |
| Command list / counts | [`reference/COMMAND-SURFACE.md`](reference/COMMAND-SURFACE.md) |
| Tier A/B/C policy | [`design/COMMAND-TIERS.md`](design/COMMAND-TIERS.md) |
| Identity / actor signing model | [`design/IDENTITY-AND-SCOPES.md`](design/IDENTITY-AND-SCOPES.md) |
| Open work / what's next | [`TODO.md`](TODO.md) |
| Recently merged PRs | [`TODO.md`](TODO.md) "Recently done" |
| Release procedure | [`operations/RELEASE.md`](operations/RELEASE.md) |
| Architecture snapshot / agent context | [`MEMORY.md`](MEMORY.md) |
| Postmortems / gotchas | [`operations/LESSONS.md`](operations/LESSONS.md) |

If two docs disagree, fix the non-canonical one to match the canonical
one. Don't update both copies.

## Files outside `docs/` that also need updates

"Doc cleanup" is broader than `docs/`. These repo-level files render
to users in places `docs/` cannot reach and have to stay in sync.

| File | What it controls | When it needs updating |
|---|---|---|
| `README.md` (repo root) | GitHub landing page; HACS surfaces it on the integration detail page. | Whenever a user-facing fact changes. Keep it self-contained. |
| `app/config.yaml` `description:` | Text HA Supervisor renders in the addon list and detail page. | When the addon's user-facing pitch changes. No internal jargon. |
| `app/CHANGELOG.md` | Per-release notes the release workflow extracts; HA Supervisor renders in the addon's Changelog tab. | Add a `## <version> (date) — title` section as part of every release PR. |
| Five version sources (`app/config.yaml`, `app/build.yaml`, `app/node/pyproject.toml`, `app/node/src/openclaw_node/__init__.py`, `custom_components/openclaw_hass_node_assist/manifest.json`) | The version string. Drift fails CI. | Always together via `scripts/bump-version.py <version>`. Never hand-edited. |
| `custom_components/openclaw_hass_node_assist/manifest.json` `name` | Integration name in HA's Integrations list. | When you rename the integration. |
| `custom_components/openclaw_hass_node_assist/strings.json` | Config-flow UI copy. | When you change a config-flow field. |
| `hacs.json` `name` | Title in the HACS catalog. | When the HACS-listed title changes. |
| GitHub repo description | One-line shown on github.com. | When the elevator pitch changes (`gh repo view ... --json description`). |

## Cheat sheet — "I just did X, which docs do I touch?"

| You just did… | Update |
|---|---|
| Finished a working session of any size | Refresh [`MEMORY.md`](MEMORY.md) so a fresh session can pick up. |
| Merged a non-release PR | Add a one-liner to [`TODO.md`](TODO.md) "Recently done"; close any numbered TODO item it satisfies. Sync `MEMORY.md` if architecture changed. |
| Merged a release PR (version bump) | Auto-cut workflow handles tag + GitHub release. Update [`STATUS.md`](STATUS.md) if behaviour changed. |
| Registered a new command | [`reference/COMMAND-SURFACE.md`](reference/COMMAND-SURFACE.md) (count + tier). Add to [`design/IDENTITY-AND-SCOPES.md`](design/IDENTITY-AND-SCOPES.md) forbidden-command tables if user/admin shouldn't reach it. |
| Changed a Tier boundary | [`design/COMMAND-TIERS.md`](design/COMMAND-TIERS.md). Then audit [`design/IDENTITY-AND-SCOPES.md`](design/IDENTITY-AND-SCOPES.md) rules. |
| Shipped a feature that closes a TODO item | Mark the item in [`TODO.md`](TODO.md); update [`STATUS.md`](STATUS.md) and [`INSTALL.md`](INSTALL.md) if user-visible. |
| Wrote a useful postmortem | [`operations/LESSONS.md`](operations/LESSONS.md) (append). |
| Hit a question that needs Rob | Open a TODO item in [`TODO.md`](TODO.md). |
| Changed CI / quality gates | [`operations/QUALITY.md`](operations/QUALITY.md) (gates) + [`CONTRIBUTING.md`](CONTRIBUTING.md) (if workflow changes). |
| Changed the release pipeline | [`operations/RELEASE.md`](operations/RELEASE.md). |
| Changed the addon `map:` or volume layout | [`design/PLAN.md`](design/PLAN.md). |

## Maintenance

- If you update a fact and notice another doc still has the old
  version, **don't** also update the copy. Fix the copy by linking to
  the canonical doc instead.
- A CI guard against hardcoded command counts outside
  [`reference/COMMAND-SURFACE.md`](reference/COMMAND-SURFACE.md) is on
  the roadmap.
- This file is the canonical "what does each doc do" — if a new doc
  is added, add a link here in the same commit.
