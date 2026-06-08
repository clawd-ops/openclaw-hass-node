# Contributing

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/) for
every commit that lands on `main` (the squash-merge subject counts, so
your PR title is what matters). Common prefixes: `feat:`, `fix:`,
`perf:`, `refactor:`, `docs:`, `test:`, `build:`, `ci:`, `chore:`,
`revert:`. Mark a breaking change with `feat!:` / `fix!:` or a
`BREAKING CHANGE:` footer. Optional scopes: `addon`, `node`, `hacs`,
`gateway`, `docs`.

This is what feeds the auto-generated changelog described in
[`docs/RELEASE.md`](RELEASE.md). The release Action isn't wired yet —
audit-hardening work comes first — but the commit history starting now
needs to be Action-ready so the first auto-cut release isn't blind.

## Version policy

The project carries the version string in five places (`pyproject.toml`,
`__init__.py` fallback, `addon/config.yaml`, `addon/build.yaml`,
`custom_components/openclaw_gateway/manifest.json`). Today they're
bumped by hand and kept honest by `test_version_sync.py`; once the
release Action lands they'll be bumped together by tooling.

**Until the Action lands, the manual rule is:** never edit just one.
The CI gate fails if any of the five drift. The version stays on a
pre-release marker (`a`/`b`/`rc`/`.dev`) until the project ships a 1.0
— that's also enforced by CI (`test_alpha_tag_present`).

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
