# Contributing

## Release / version-bump discipline

**Every meaningful change must bump `addon/config.yaml`'s `version:` and
the matching `io.hass.version` label in `addon/build.yaml`.**

This isn't cosmetic — it's the *only* signal HA Supervisor watches to
decide whether the add-on needs a new build. Without a bump:

- Users won't see an **Update** button in the add-on store. They'll have
  to **Uninstall → Refresh repo → Reinstall**, which wipes `/data` and
  destroys the pairing identity. They have to re-pair every release.
- With a bump, HA shows **Update**, which preserves `/data`. The
  persisted `device-token` and Ed25519 identity stick around. Pairing
  survives. No user action beyond clicking Update.

Version scheme: `YYYY.M.PATCH`, per `docs/PLAN.md` P1.4. Bump `PATCH`
within an HA release; bump `YYYY.M` when we re-test against a new HA
release.

### Release checklist for any user-visible PR

- [ ] Bump `addon/config.yaml`'s `version:`.
- [ ] Bump `addon/build.yaml`'s `io.hass.version:` label (must match).
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
