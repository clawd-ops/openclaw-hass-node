# Release Process

> Status: **live.** `.github/workflows/release-on-version-bump.yml`
> auto-cuts a release whenever a push to `main` bumps the version in
> the seven tracked files. `scripts/bump-version.py` updates those version
> sources, and `scripts/mark-commands-shipped.py` stamps current command
> additions locally in the same release PR. PR CI checks the transition, and
> the push-triggered release workflow checks it again before creating a tag or
> release. The manual procedure at the bottom is
> preserved for emergency / out-of-band use only.

The project carries the version string in seven places (`pyproject.toml`,
`app/config.yaml`, `app/build.yaml`, `__init__.py` fallback,
`custom_components/openclaw_hass_node_assist/manifest.json`,
`plugins/openclaw-hass-node-assist-tools/package.json`,
`skills/openclaw-hass-node-skill/SKILL.md`) and ships through
two ecosystems (HA Supervisor add-on, HACS custom integration).
Preparing a release uses two commands, not seven careful edits plus a manual
ledger sweep.

## Goals

1. **One source of truth for "what version is this?"** A maintainer
   never edits a version literal by hand. `scripts/bump-version.py`
   bumps every file together so they can't drift; `Version Sync` CI
   fails the gate on any inconsistency.
2. **Command release history lands with the version bump.** The maintainer runs
   `scripts/mark-commands-shipped.py` locally after the version bump. The
   manual command ledger and both generated artifacts are committed in the
   same PR as all seven version sources and the changelog. This is not tag
   automation. The helper always regenerates the artifacts, including when no
   command needed stamping, because a version-only release still moves
   `latest_release`.
3. **Releases are cut by CI, not by hand.** Pushing a version bump to
   `main` is what triggers the tag + GitHub release. No human runs
   `git tag` in the normal flow.
4. **Pre-release markers are first-class.** Versions use the OpenClaw
   style `YYYY.M.D-beta.N` (also `-alpha.N` and `-rc.N`); pre-1.0 the
   project lives on those markers. Versions carrying any of those
   suffixes are cut as **prereleases**; the stable `YYYY.M.D` is a full
   release.
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

- **Format:** `YYYY.M.D` for a stable release, `YYYY.M.D-alpha.N`,
  `YYYY.M.D-beta.N` or `YYYY.M.D-rc.N` for a pre-release. No leading zeros in
  month or day, and `N` starts at 1. `2026.10.22-beta.1` → `2026.10.22-beta.2`
  → stable `2026.10.22`. The git tag is `v<version>` verbatim
  (`v2026.10.22-beta.1`).
- **Ordering:** `-beta.2` > `-beta.1`, `-rc.1` > `-beta.9`, and the stable
  release sorts after all of its pre-releases. Home Assistant's
  `AwesomeVersion` (what the Supervisor uses to offer add-on updates) and PEP
  440 agree on this; the release gate checks both.
- **Cap:** the counter stops at 9 (`-beta.9`). Past that, roll the date forward
  to today and reset to `-beta.1`. `scripts/bump-version.py` refuses `-beta.10`.
- **Old form:** versions before the switch (`2026.10.15b1`) used
  `YYYY.M.DbN`. That form stays readable as the *previous* version, so the
  transition `2026.10.15b1` → `2026.10.22-beta.1` passes the gates, but it can no
  longer be written as a new version.
- **Per-file storage:** the dash form is stored verbatim everywhere except the
  two Python-side files.

  | File | Stored as | Why |
  | --- | --- | --- |
  | `app/config.yaml` | dash form | Supervisor reads it as a string; it parses as SemVer |
  | `app/build.yaml` (label) | dash form | informational label |
  | `custom_components/.../manifest.json` | dash form | HA/hassfest accept SemVer |
  | `plugins/.../package.json` | dash form | valid npm semver |
  | `skills/.../SKILL.md` | dash form | documentation only |
  | `app/node/pyproject.toml` | PEP 440 normalized (`2026.10.22b1`) | what `uv.lock`, the built wheel and `importlib.metadata` record |
  | `app/node/src/openclaw_node/__init__.py` | PEP 440 normalized | fallback must equal what the installed package reports |

  `scripts/bump-version.py` writes each file in its form and `--check`
  compares the normalized forms, so `2026.10.22b1` in `pyproject.toml` is not
  reported as drift against `2026.10.22-beta.1` in `app/config.yaml`. The node
  therefore reports the normalized form to the gateway while the add-on and
  tag use the dash form.
- **1.0 and beyond:** semver from the same base. Breaking changes
  bump major; new functionality bumps minor; fixes bump patch.
- **Hot-fixes** are forward-fixes off `main`; we don't maintain
  long-lived release branches. If a stable user is on `2026.10.22-beta.7`
  and we ship `2026.10.22-beta.8` with a regression, the fix is `-beta.9`
  not `-beta.8.1`. This keeps the release pipeline single-tracked.

## Release procedure (automated)

Three-step preparation: bump version, stamp commands, write the changelog,
then merge and let CI do
the rest.

### Step 1 — bump the seven version files

The seven files are:

- `app/config.yaml`
- `app/build.yaml`
- `app/node/pyproject.toml`
- `app/node/src/openclaw_node/__init__.py`
- `custom_components/openclaw_hass_node_assist/manifest.json`
- `plugins/openclaw-hass-node-assist-tools/package.json`
- `skills/openclaw-hass-node-skill/SKILL.md`

Hand-editing them is how drift happens. Use the script:

```sh
scripts/bump-version.py 2026.10.22-beta.2
```

It enforces the format above and exits non-zero if any regex misses
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
scripts/mark-commands-shipped.py 2026.10.22-beta.2
python scripts/generate-command-coverage.py --check
```

The helper preflights the complete manual ledger, rewrites it when any command
is still `unreleased`, and regenerates both artifacts. It has no rollback
machinery, deliberately: every path it writes is tracked, so a partial run is
undone with `git checkout -- .` followed by a retry. Git already provides the
recovery that custom rollback code could only approximate, and the previous
implementation of that code carried a real defect of its own. If regeneration
fails after the ledger was rewritten, the helper says so and tells you to reset
and retry.

Shipment values accept the dash form (`-alpha.N`, `-beta.N`, `-rc.N`), final
releases, and the historical `aN`/`bN`/`rcN`/`.devN` forms already recorded in
the ledger. If a release has no new
commands, the helper still refreshes the generated `latest_release` from the
synchronized version sources.

The `Command Coverage Ledger` PR check always compares the base and head
ledgers. Without a version change, existing shipment values must remain exact
and new commands must stay `unreleased`. With a version change, existing
released history must remain exact, every previously-unreleased or newly-added
command must equal the head version, and the version must advance under both
PEP 440 and `AwesomeVersion` ordering (the head must use the dash form; CI
installs `awesomeversion==25.8.0`, Home Assistant's pin). This is an
executable PR check, not a claim about branch protection. The push-triggered
release workflow reruns the same transition check
against the pre-push commit before any tag or GitHub release is created, which
provides tag-time protection even if repository merge settings do not require
the PR check. Commit the ledger changes with all seven version sources and the
changelog in the same version-bump PR.

### Step 3 — add the CHANGELOG entry, open + merge the release PR

Add a section to `app/CHANGELOG.md` for the new version. The heading
format matters because the release workflow extracts notes by parsing
this file:

```md
## 2026.10.22-beta.2 (YYYY-MM-DD) — short one-line title

### Features
- ...

### Fixes
- ...
```

PR title: `release: 2026.10.22-beta.2 — <one-line summary>`. Merge it.

### Step 4 — CI cuts the release (no human action)

`.github/workflows/release-on-version-bump.yml` triggers on push to
`main` when any of the seven version sources changes: `app/config.yaml`,
`app/build.yaml`, `app/node/pyproject.toml`,
`app/node/src/openclaw_node/__init__.py`,
`custom_components/openclaw_hass_node_assist/manifest.json`,
`plugins/openclaw-hass-node-assist-tools/package.json`,
`skills/openclaw-hass-node-skill/SKILL.md` (or the workflow file itself).
`scripts/bump-version.py` updates all seven together. It:

1. Reads the current synced version via `scripts/bump-version.py --get`.
2. Validates the base-to-head command shipment transition before tagging.
3. Skips if a matching `v<version>` git tag already exists
   (idempotent — safe to re-trigger).
4. Extracts the `app/CHANGELOG.md` section matching this version
   (heading line like `## 2026.10.22-beta.2 (...)`). Falls back to a stub
   if no matching section is found.
5. Creates the tag and a GitHub release with those notes. Versions
   carrying a pre-release marker (`-alpha.N`/`-beta.N`/`-rc.N`, or the
   old `aN`/`bN`/`rcN`/`.devN`) are cut as prereleases; final releases
   are full.

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
git tag v2026.10.22-beta.2 $SHA
git push origin v2026.10.22-beta.2
gh release create v2026.10.22-beta.2 \
  --title "2026.10.22-beta.2 — <line from CHANGELOG>" \
  --prerelease \
  --notes "$(awk '/^## 2026\.10\.22-beta\.2/{flag=1;next}/^## /{flag=0}flag' app/CHANGELOG.md)"
```

If you find yourself running this regularly, fix the workflow instead.
