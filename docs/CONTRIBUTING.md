# Contributing

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/) for
every commit that lands on `main` (the squash-merge subject counts, so
your PR title is what matters). Common prefixes: `feat:`, `fix:`,
`perf:`, `refactor:`, `docs:`, `test:`, `build:`, `ci:`, `chore:`,
`revert:`. Mark a breaking change with `feat!:` / `fix!:` or a
`BREAKING CHANGE:` footer. Optional scopes: `addon`, `node`, `hacs`,
`gateway`, `docs`.

The release Action is live
(`.github/workflows/release-on-version-bump.yml`) and auto-cuts a
GitHub release whenever a push to `main` bumps the version in the
five tracked files. Release notes come from the hand-written
`app/CHANGELOG.md` section for that version — the workflow
extracts it, it does not generate it. Follow the commit convention
so the changelog you write groups cleanly. See
[`docs/operations/RELEASE.md`](operations/RELEASE.md) for the full flow.

## Version policy

The project carries the version string in five places (`pyproject.toml`,
`__init__.py` fallback, `app/config.yaml`, `app/build.yaml`,
`custom_components/openclaw_hass_node_assist/manifest.json`). Use
`scripts/bump-version.py <new-version>` — it updates all five together.
Then run `scripts/mark-commands-shipped.py <new-version>` locally. Commit its
manual-ledger and generated-artifact changes in the same PR as all five
version sources and the matching `app/CHANGELOG.md` entry. This release-prep
stamp is not tag automation. The Command Coverage Ledger PR check validates the
base-to-head shipment transition, and the push-triggered release workflow runs
the same check before creating a tag or release. These are executable checks;
whether the PR check is required for merge depends on repository settings.
`test_version_sync.py` keeps them honest in CI; a drift in any of the
five fails the Version Sync gate on the PR. The version stays on a
pre-release marker (`a`/`b`/`rc`/`.dev`) until the project ships a 1.0
— enforced by CI (`test_prerelease_tag_present`).

**Why the bump matters at all:** `app/config.yaml`'s `version:` is
the *only* signal HA Supervisor watches to decide whether the add-on
needs a new build. Without a bump:

- Users won't see an **Update** button in the add-on store. They'll have
  to **Uninstall → Refresh repo → Reinstall**, which wipes `/data` and
  destroys the pairing identity. They have to re-pair every release.
- With a bump, HA shows **Update**, which preserves `/data`. The
  persisted `device-token` and Ed25519 identity stick around. Pairing
  survives. No user action beyond clicking Update.

See [`docs/operations/RELEASE.md`](operations/RELEASE.md) for the full versioning + release
plan.

### Release checklist for any user-visible PR

- [ ] PR title is a Conventional Commit (`feat:`, `fix:`, …).
- [ ] Bump every version source if and only if you need a Supervisor
      Update prompt. `test_version_sync.py` will refuse to let you
      bump some-but-not-all.
- [ ] For a release bump, run `scripts/mark-commands-shipped.py <new-version>`
      locally and include the manual ledger, generated artifacts, all five
      version sources, and changelog in the same PR.
- [ ] If the change touches the connect frame, the auth payload, or the
      command surface — add a `docs/operations/LESSONS.md` entry so the agent (future maintainer)
      doesn't relitigate the gotcha.
- [ ] If the change requires gateway-side config (e.g. a new entry in
      `gateway.nodes.commands.allow`) — document it in `docs/INSTALL.md`
      so operators see it.

## PR review

Cross-provider review per `docs/CONTRIBUTING.md`: Claude generates, Codex
reviews. Merge only on Codex APPROVE or after addressing findings.

## Doc-only changes

Per the OC-repo autonomy rule, doc-only changes (`docs/`, `README.md`,
`LICENSE`) can be merged direct to main without the Codex review pass.

## Documentation markup

Only the Markdown extensions listed under `markdown_extensions` in `mkdocs.yml`
are available. Syntax from an extension that is not enabled is **not a build
error**: Python-Markdown does not recognise it, so it passes through and ships
to the reader as literal text while `mkdocs build --strict` stays green. That is
how 100 task-list lines across `COMPLETION-ROADMAP.md` and this file rendered as
literal `[x]` and `[ ]` for as long as they did.

**Every extension whose syntax these docs use is enabled**, including
`pymdownx.tasklist`, `pymdownx.tilde` (`~~strikethrough~~`) and `pymdownx.mark`
(`==highlight==`). Enabling the last two changed no rendered page, because
nothing used them. They are on so the first author who reaches for either gets
what they wrote.

`pymdownx.tilde` is configured with `subscript: false`. It would otherwise also
consume single tildes, so `about ~5~ minutes` would silently render as
`about <sub>5</sub> minutes`. Deletion is the syntax these docs want; subscript
is a side effect nobody asked for.

If you need syntax from an extension that is not enabled, enable it in the same
PR. Do not work around it, and do not read a green build as proof it rendered.

### Why the guard reads the build output

`scripts/assert-tasklists-rendered.py` runs in the Docs workflow after the
strict build and asks one question: **did `pymdownx.tasklist` run?** It looks
for the `task-list-item` class, which the extension emits and nothing else
produces.

Four earlier revisions tried to decide instead whether a given `[ ]` *should*
have been consumed, from the rendered HTML or from the Markdown source. Every
one was wrong, in both directions, because that question needs
Python-Markdown's grammar:

- `- \[ \] literal choice` escapes the brackets deliberately and renders as
  `<li>[ ] literal choice</li>`, byte-identical to an unconsumed marker. No
  check on rendered text can tell a missing extension from an intended literal.
- `-  [ ] item`, `-\t[ ] item`, `> - [ ] item` and `1. [ ] item` all render as
  task controls, while an indented `    - [ ] example` renders as code. A source
  regex that stops short of reimplementing the grammar gets all five wrong.

A version that read `markdown_extensions:` out of `mkdocs.yml` failed for a
third reason: a second such key later in the file wins in MkDocs, so the check
passed while the roadmap rendered zero controls. Reading the build output avoids
the effective-configuration question altogether.

**A diagnostic that names a wrong remedy is worse than none: it spends the
reader's one good attempt.** That is why this asks something it can answer
rather than something it can only guess at.

## Cross-provider code review

> Folded in from the former `docs/PROCESS.md` during the Phase 2 doc
> reshape. Every substantive code change goes through
> generate-then-cross-review before merge. Generator and reviewer must
> be different model providers, so blind spots do not compound.

### Pairing

- **Generator**: Claude (Claude Code subagent).
- **Reviewer**: OpenAI (Codex subagent on the pi runtime).

If the pairing is ever inverted for a specific task (e.g. Codex
generates), the reviewer must be a different provider, Claude in that
case. The rule is "two providers", not "Claude generates".

### Flow

1. **Branch + change.** Claude Code subagent is spawned with the issue
   or task brief. It creates a feature branch, writes the change,
   commits, opens a PR with a clear description of what + why.
2. **Cross-review.** A Codex subagent is spawned against the PR diff
   with a review-only prompt (no write access). It posts:
   - Inline `gh pr comment` lines for specific issues.
   - A final verdict comment: `LGTM` or `CHANGES REQUESTED` with a
     prioritized list.
3. **Iterate.** If `CHANGES REQUESTED`, Claude addresses each item in
   follow-up commits. Codex re-reviews until `LGTM` or human override.
4. **Merge.** Only on `LGTM` or explicit human override. Squash-merge
   keeps history clean.

### Spawning

Both subagents run via OpenClaw `sessions_spawn`. Expectations:

- Generator brief: objective, paths in scope, write-scope, link to
  PLAN/STATUS, requirement to update STATUS.md on completion.
- Reviewer brief: PR number, read-only, must check against
  [`design/PLAN.md`](design/PLAN.md),
  [`reference/HA-CONFIG-EDITING.md`](reference/HA-CONFIG-EDITING.md),
  and the change's own description. Output format must be the verdict
  structure above.

### Codex CLI fallback

OpenClaw 2026.6.5 (deployed 2026-06-10) restored `openai/*`
direct-to-Codex routing, so reviewer pairing runs via `sessions_spawn`
by default. The bare `codex exec --skip-git-repo-check --cd <repo>`
CLI path remains a valid fallback if a future regression breaks
gateway routing again. In that case the verdict comment must state
`via CLI workaround`. The pairing rule always holds: generator and
reviewer must be two different providers.

### Quality gates (mandatory)

Cross-review is only one gate. Every PR must also pass the mechanical
quality gates in [`operations/QUALITY.md`](operations/QUALITY.md):

- Strict type checking (`mypy --strict` + `pyright --strict`).
- Google-style docstrings on every public symbol (enforced by `ruff`'s
  `D` rules and `pydoclint`).
- 100 % branch coverage on shipped code (`pytest` + `coverage.py`).
- Lint/format (`ruff check`, `ruff format --check`).
- Security (`bandit`, `pip-audit`).
- HA add-on smoke build for `amd64`/`aarch64`/`armv7`.

All gates run in GitHub Actions; merge requires all green. The Codex
reviewer pass is gate #9. It does not replace the mechanical gates, it
adds on top of them.

### Why this exists

Smart-home config and HA APIs are easy to get plausible-but-wrong on.
Two independent provider perspectives catch:

- Hallucinated HA service names or entity attributes.
- `.storage/` direct-edit slips.
- Missed reload calls after YAML edits.
- Version-specific behavior drift (paired with `docs.lookup` rule).

### Human escalation

The reviewer flagging something does not require Rob to resolve it
directly. Reviewer comments are addressed by the generator first; Rob
is only pulled in when the two agents loop without converging, or when
either agent flags a scope/safety question.
