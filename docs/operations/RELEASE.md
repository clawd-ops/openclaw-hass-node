# Release Process

> Status: **live.** `.github/workflows/release-on-version-bump.yml`
> auto-cuts a release whenever a push to `main` bumps the version in
> the five tracked files. `scripts/bump-version.py` updates those version
> sources, and `scripts/mark-commands-shipped.py` stamps current command
> additions locally in the same release PR. PR CI checks the transition, and
> the push-triggered release workflow checks it again before creating a tag or
> release. The manual procedure at the bottom is
> preserved for emergency / out-of-band use only.

The project carries the version string in five places (`pyproject.toml`,
`app/config.yaml`, `app/build.yaml`, `__init__.py` fallback,
`custom_components/openclaw_hass_node_assist/manifest.json`) and ships through
two ecosystems (HA Supervisor add-on, HACS custom integration).
Preparing a release uses two commands, not five careful edits plus a manual
ledger sweep.

## Goals

1. **One source of truth for "what version is this?"** A maintainer
   never edits a version literal by hand. `scripts/bump-version.py`
   bumps every file together so they can't drift; `Version Sync` CI
   fails the gate on any inconsistency.
2. **Command release history lands with the version bump.** The maintainer runs
   `scripts/mark-commands-shipped.py` locally after the version bump. The
   manual command ledger and both generated artifacts are committed in the
   same PR as all five version sources and the changelog. This is not tag
   automation. The helper prepares every candidate before replacement and uses
   best-effort rollback if a replacement fails or is interrupted.
3. **Releases are cut by CI, not by hand.** Pushing a version bump to
   `main` is what triggers the tag + GitHub release. No human runs
   `git tag` in the normal flow.
4. **Pre-release markers are first-class.** The project is currently
   on the beta track (`2026.6.20b7` at time of writing); pre-1.0 it
   lives on `aN`/`bN`/`rcN` markers. Versions carrying any of those
   suffixes are cut as **prereleases**; final tags (`1.0.0`,
   `2026.7.0`) are full releases.
5. **No backports, no parallel branches.** `main` is the only branch
   that ships. Hot-fixes are forward-fixes that cut a new release.

## Commit messages: Conventional Commits

Every commit on `main` (squash-merge subjects count; PR titles end up
as the squash subject by default) follows
[Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<optional-scope>): <subject>

<optional body>

<optional BREAKING CHANGE: footer or other footers>
```

Common types: `feat`, `fix`, `perf`, `refactor`, `docs`, `test`,
`build`, `ci`, `chore`, `revert`. Common scopes: `addon`, `node`,
`hacs`, `gateway`, `docs`, `ci`.

A breaking change is marked either by `feat!:` / `fix!:` syntax or by
a `BREAKING CHANGE:` footer in the commit body. Pre-1.0 we treat
breaking changes as ordinary prerelease bumps but call them out in the
`app/CHANGELOG.md` entry so users know to read the upgrade notes.

The current release workflow does **not** auto-generate a changelog
from these commits — it extracts notes from a hand-written
`app/CHANGELOG.md` section. Conventional Commits is still the policy
because it keeps the history machine-readable for future tooling and
makes manual changelog drafting fast.

## Versioning policy

- **Pre-1.0:** the project stays on a single prerelease-channel CalVer
  (`YYYY.M.Pb<N>` during the current beta track; the prior alpha track
  used `YYYY.M.Pa<N>`). Every release bumps the prerelease increment
  (`b1` → `b2` → `b3` …) unless a breaking change forces a `Y.M.P`
  rev. Date-based bumps (`2026.6.20b7` → `2026.7.0b1`) happen when
  there's a deliberate cut, not because the calendar rolled over.
- **1.0 and beyond:** semver from the same base. Breaking changes
  bump major; new functionality bumps minor; fixes bump patch.
- **Hot-fixes** are forward-fixes off `main`; we don't maintain
  long-lived release branches. If a stable user is on `2026.6.20b7`
  and we ship `2026.6.20b8` with a regression, the fix is `b9` not
  `b8.1`. This keeps the release pipeline single-tracked.

## Release procedure (automated)

Three-step preparation: bump version, stamp commands, write the changelog,
then merge and let CI do
the rest.

### Step 1 — bump the five version files

The five files are:

- `app/config.yaml`
- `app/build.yaml`
- `app/node/pyproject.toml`
- `app/node/src/openclaw_node/__init__.py`
- `custom_components/openclaw_hass_node_assist/manifest.json`

Hand-editing them is how drift happens. Use the script:

```sh
scripts/bump-version.py 2026.6.20b8
```

It enforces a PEP 440 shape and exits non-zero if any regex misses
(so a renamed key surfaces loudly instead of silently skipping).

Verify locally:

```sh
scripts/bump-version.py --check     # confirm sources agree
scripts/bump-version.py --get       # print the current version
```

The CI `Version Sync` job runs `--check` on every PR and reports drift as a
failed executable check. Repository settings determine whether that check is a
required merge gate.

### Step 2 — stamp command additions and regenerate the ledger

Run the command stamp locally with the same version:

```sh
scripts/mark-commands-shipped.py 2026.6.20b8
python scripts/generate-command-coverage.py --check
```

The helper preflights the complete manual ledger and prepares every replacement
before mutating a tracked path. It then applies the files in sequence, catches
ordinary failures and interruptions, and makes a best-effort rollback that
restores original bytes and file modes. This is not filesystem-level atomicity:
a hard process kill, host power loss, or rollback failure can leave a partial
update. In that case the helper reports incomplete recovery and names the paths
that require inspection. Candidate and rollback temporary files are cleaned on
handled paths.

Shipment values accept the same canonical forms as `scripts/bump-version.py`:
historical alpha and beta versions (`aN` and `bN`), release candidates (`rcN`),
development releases (`.devN`), and final releases. If a release has no new
commands, the helper still refreshes the generated `latest_release` from the
synchronized version sources.

The `Command Coverage Ledger` PR check compares the base and head ledgers when
the synchronized version changes. Existing released history must remain exact;
commands that were unreleased at the base or are new in the head must equal the
head version. This is an executable PR check, not a claim about branch
protection. The push-triggered release workflow reruns the same transition check
against the pre-push commit before any tag or GitHub release is created, which
provides tag-time protection even if repository merge settings do not require
the PR check. Commit the ledger changes with all five version sources and the
changelog in the same version-bump PR.

### Step 3 — add the CHANGELOG entry, open + merge the release PR

Add a section to `app/CHANGELOG.md` for the new version. The heading
format matters because the release workflow extracts notes by parsing
this file:

```md
## 2026.6.20b8 (YYYY-MM-DD) — short one-line title

### Features
- ...

### Fixes
- ...
```

PR title: `release: 2026.6.20b8 — <one-line summary>`. Merge it.

### Step 4 — CI cuts the release (no human action)

`.github/workflows/release-on-version-bump.yml` triggers on push to
`main` when any of the five version files (or the workflow file
itself) changes. It:

1. Reads the current synced version via `scripts/bump-version.py --get`.
2. Validates the base-to-head command shipment transition before tagging.
3. Skips if a matching `v<version>` git tag already exists
   (idempotent — safe to re-trigger).
4. Extracts the `app/CHANGELOG.md` section matching this version
   (heading line like `## 2026.6.20b8 (...)`). Falls back to a stub
   if no matching section is found.
5. Creates the tag and a GitHub release with those notes. Versions
   carrying a PEP 440 prerelease marker (`aN`/`bN`/`rcN`/`.devN`) are
   cut as prereleases; final releases are full.

HA Supervisor's Update prompt reads from published GitHub releases,
not from `main`, so this is what users actually see.

## What HA Supervisor and HACS read

- **HA Supervisor (add-on)** reads `app/CHANGELOG.md` directly
  (`app/config.yaml` does not set a `changelog` URL key, so the
  in-repo file is rendered). The user opens the add-on's
  Documentation / Changelog tab from Settings → Add-ons.
- **HACS (integration)** renders the repo `README.md` on the
  integration's detail page (the "Open in HACS" view in Settings →
  Devices & Integrations → HACS → OpenClaw HA Node — Assist) and surfaces the
  GitHub *release notes* (the body of the most recent tag) below it.
  There is no separate `custom_components/openclaw_hass_node_assist/info.md`
  today; if one is ever added, HACS will pick it up automatically.

## Manual fallback (when the workflow is down)

This recipe is preserved for the rare emergency case (Action failure,
retroactive tag, out-of-band release). The normal flow above does not
need it.

```sh
SHA=$(git rev-parse main)
git tag v2026.6.20b8 $SHA
git push origin v2026.6.20b8
gh release create v2026.6.20b8 \
  --title "2026.6.20b8 — <line from CHANGELOG>" \
  --prerelease \
  --notes "$(awk '/^## 2026\.6\.20b8/{flag=1;next}/^## /{flag=0}flag' app/CHANGELOG.md)"
```

If you find yourself running this regularly, fix the workflow instead.
