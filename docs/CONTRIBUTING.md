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
`addon/CHANGELOG.md` section for that version — the workflow
extracts it, it does not generate it. Follow the commit convention
so the changelog you write groups cleanly. See
[`docs/RELEASE.md`](RELEASE.md) for the full flow.

## Version policy

The project carries the version string in five places (`pyproject.toml`,
`__init__.py` fallback, `addon/config.yaml`, `addon/build.yaml`,
`custom_components/openclaw_gateway/manifest.json`). Use
`scripts/bump-version.py <new-version>` — it updates all five together.
`test_version_sync.py` keeps them honest in CI; a drift in any of the
five fails the Version Sync gate on the PR. The version stays on a
pre-release marker (`a`/`b`/`rc`/`.dev`) until the project ships a 1.0
— enforced by CI (`test_prerelease_tag_present`).

**Why the bump matters at all:** `addon/config.yaml`'s `version:` is
the *only* signal HA Supervisor watches to decide whether the add-on
needs a new build. Without a bump:

- Users won't see an **Update** button in the add-on store. They'll have
  to **Uninstall → Refresh repo → Reinstall**, which wipes `/data` and
  destroys the pairing identity. They have to re-pair every release.
- With a bump, HA shows **Update**, which preserves `/data`. The
  persisted `device-token` and Ed25519 identity stick around. Pairing
  survives. No user action beyond clicking Update.

See [`docs/RELEASE.md`](RELEASE.md) for the full versioning + release
plan.

### Release checklist for any user-visible PR

- [ ] PR title is a Conventional Commit (`feat:`, `fix:`, …).
- [ ] Bump every version source if and only if you need a Supervisor
      Update prompt. `test_version_sync.py` will refuse to let you
      bump some-but-not-all.
- [ ] If the change touches the connect frame, the auth payload, or the
      command surface — add a `docs/LESSONS.md` entry so future-Clawd
      doesn't relitigate the gotcha.
- [ ] If the change requires gateway-side config (e.g. a new entry in
      `gateway.nodes.allowCommands`) — document it in `docs/INSTALL.md`
      so operators see it.

## PR review

Cross-provider review per `docs/PROCESS.md`: Claude generates, Codex
reviews. Merge only on Codex APPROVE or after addressing findings.

## Doc-only changes

Per the OC-repo autonomy rule, doc-only changes (`docs/`, `README.md`,
`LICENSE`) can be merged direct to main without the Codex review pass.
