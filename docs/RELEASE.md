# Release Process

> Status: **proposed, not yet implemented.** A version-sync CI gate
> keeps the five version strings in lock-step today; the Action
> described here will automate the bump itself. Implementation
> waits until the audit-hardening work is done — but every PR opened
> in the meantime should already follow the [commit message convention](#commit-messages)
> so the changelog history is usable when the Action lands. Until then
> use the [manual release procedure](#manual-release-procedure-until-the-action-lands).

The project carries the version string in five places (`pyproject.toml`,
`addon/config.yaml`, `addon/build.yaml`, `__init__.py` fallback,
`custom_components/openclaw_gateway/manifest.json`) and ships through
three ecosystems (Python package, HA Supervisor add-on, HACS custom
integration). Cutting a release should be one command, not five
careful edits.

## Goals

1. **One source of truth for "what version is this?"** A maintainer
   never edits a version literal by hand. The Action bumps every
   file together so they can't drift.
2. **Changelogs are generated, not written.** Every release tag has
   a changelog grouped by intent (features, fixes, breaking changes,
   etc.) derived from the commits that landed since the last tag.
3. **Pre-release markers are first-class.** The project is currently
   on the beta track (`2026.6.20b6` at time of writing); pre-1.0 it
   lives on `aN`/`bN`/`rcN` markers. The release pipeline has to
   understand and preserve that.
4. **No backports, no parallel branches.** `main` is the only branch
   that ships. Hot-fixes are forward-fixes that cut a new release.

## Commit messages: Conventional Commits

Every commit on `main` (squash-merge messages count; PR titles end up
as the squash subject by default) follows
[Conventional Commits](https://www.conventionalcommits.org/) so the
release tool can categorise them deterministically:

| Prefix      | Section in changelog | Bumps                                |
| ----------- | -------------------- | ------------------------------------ |
| `feat:`     | Features             | minor (or prerelease increment pre-1.0) |
| `fix:`      | Bug Fixes            | patch (or prerelease increment pre-1.0) |
| `perf:`     | Performance          | patch                                |
| `refactor:` | Code Refactoring     | patch                                |
| `docs:`     | Documentation        | patch (sometimes excluded; see note) |
| `test:`     | Tests                | patch (often excluded)               |
| `build:`    | Build System         | patch                                |
| `ci:`       | CI                   | patch                                |
| `chore:`    | Chores               | patch (often excluded)               |
| `revert:`   | Reverts              | patch                                |

A breaking change is marked **either** by `feat!:` / `fix!:` syntax
**or** by a `BREAKING CHANGE:` footer in the commit body. Either
flips the section to "⚠ BREAKING CHANGES" and forces a major bump
(once we're 1.0+). Pre-1.0 we treat breaking changes as ordinary
prerelease bumps but still call them out in their own section so consumers
know to read the upgrade notes.

Scopes are **required**, not optional, because they route each commit to
the right changelog file (see [Per-component changelogs](#per-component-changelogs)
below). The full scope vocabulary:

| Scope    | Where the commit lands in the user-visible changelogs                                |
| -------- | ------------------------------------------------------------------------------------ |
| `addon`  | HA add-on `addon/CHANGELOG.md` only                                                  |
| `node`   | HA add-on `addon/CHANGELOG.md` (the node code IS the add-on payload)                 |
| `gateway`| HA add-on `addon/CHANGELOG.md` (gateway-WS wire changes ship in the add-on)          |
| `hacs`   | HACS integration `custom_components/openclaw_gateway/info.md` only                   |
| `both`   | Both `addon/CHANGELOG.md` AND HACS `info.md` (use for shared schema or auth changes) |
| `docs`   | Repo-root `CHANGELOG.md` only (no user-visible component impact)                     |
| `ci`     | Repo-root `CHANGELOG.md` only                                                        |
| `repo`   | Repo-root `CHANGELOG.md` only (release plumbing, tooling, contributor docs)          |

A commit with **no scope** is a config error and the release Action will
fail the bump. `feat: do thing` is ambiguous; `feat(addon): do thing` or
`feat(both): do thing` is not. The CI lint can catch this on PR rather
than at release time.

Existing audit-fix commits already loosely follow this pattern (`fix:`
prefixes for the bundles); the scope-required policy starts in earnest on
the next commit after this doc lands.

## The mechanism: release-please

[release-please](https://github.com/googleapis/release-please) is the
right fit here. Why it over the alternatives:

- **`semantic-release`** is the most-popular tool but is Node-first
  and assumes a fairly opinionated CI shape. It can be coerced to
  manage Python + YAML + JSON, but each non-default file is a custom
  plugin.
- **`release-please`** has first-class support for "a manifest of
  arbitrary files I want bumped together," which is *exactly* the
  shape of this repo. Its config takes a list of `extra-files` with
  per-file glob patterns and the Action does the rest.
- **Hand-rolled bump script + tag-on-merge Action** is the third
  option. It works for repos with one or two version files but
  scales poorly here, and we'd have to write the changelog
  categorisation ourselves.

### How release-please works

1. After any push to `main`, the Action scans commits since the last
   release tag.
2. It groups them by Conventional Commit type, picks the next version
   per the bump rules, and opens (or updates) a single open
   "release-please: chore: release vX.Y.Z" PR.
3. That PR's diff:
   - Bumps the version in every configured file.
   - Prepends a new section to `CHANGELOG.md` with the categorised
     commit log.
   - Updates a `.release-please-manifest.json` that records the last
     released version.
4. When the maintainer merges the release PR, the Action creates a
   GitHub release with the tag (`v2026.6.20b6` or whatever was
   chosen), the changelog body, and a tarball attachment.
5. Optional follow-ups on the release event: publish to PyPI, push
   the add-on image to GHCR, post to the OpenClaw channel.

### Repo configuration sketch

Two files in the repo root:

`release-please-config.json`:

```json
{
  "release-type": "python",
  "packages": {
    ".": {
      "package-name": "openclaw-node",
      "release-type": "python",
      "include-component-in-tag": false,
      "prerelease": true,
      "prerelease-type": "beta",
      "changelog-sections": [
        { "type": "feat",     "section": "Features" },
        { "type": "fix",      "section": "Bug Fixes" },
        { "type": "perf",     "section": "Performance" },
        { "type": "refactor", "section": "Refactor" },
        { "type": "build",    "section": "Build" },
        { "type": "ci",       "section": "CI" },
        { "type": "docs",     "section": "Documentation" }
      ],
      "extra-files": [
        "addon/config.yaml",
        "addon/build.yaml",
        "addon/node/src/openclaw_node/__init__.py",
        "custom_components/openclaw_gateway/manifest.json"
      ]
    }
  }
}
```

`.release-please-manifest.json`:

```json
{ ".": "2026.6.20b6" }
```

For each `extra-files` entry, release-please looks for a
`x-release-please-version` comment near the version literal and
updates the value next to it. We'd add markers like:

```yaml
# addon/config.yaml
version: "2026.6.20b6"  # x-release-please-version
```

```json
// custom_components/openclaw_gateway/manifest.json — JSON has no
// comments, so we use the JSON variant: a key named
// "x-release-please-version" sibling to "version" tells the tool
// which field to update.
{
  "version": "2026.6.20b6"
}
```

(release-please supports JSON via path-based config — `extra-files`
takes either a string or `{type: "json", path, jsonpath}`.)

`__init__.py` carries the fallback literal only; release-please can
update it the same way via a regex-based extra-file entry.

`pyproject.toml` is updated by the built-in `python` release-type;
no extra configuration needed.

`addon/node/uv.lock` is regenerated on the next `uv sync`; the
release PR can either include the regen or leave it to CI to
materialise. Either works.

### The workflow file

`.github/workflows/release-please.yaml` (sketch):

```yaml
name: release-please

on:
  push:
    branches: [main]

permissions:
  contents: write
  pull-requests: write
  issues: write

jobs:
  release-please:
    runs-on: ubuntu-latest
    steps:
      - uses: googleapis/release-please-action@v4
        with:
          config-file: release-please-config.json
          manifest-file: .release-please-manifest.json
```

Subsequent `release-created` hooks can build & push the add-on image,
upload the Python wheel, etc. — but those are orthogonal to the
versioning concern and can be added one at a time.

## Per-component changelogs

This repo ships *two* user-visible artifacts that each have their own
changelog surface inside Home Assistant:

- **The HA add-on.** HA Supervisor renders `addon/CHANGELOG.md` (or a
  URL pointed to via `addon/config.yaml`'s `changelog` key) inside the
  add-on's **Documentation** / **Changelog** tab. The user opens this
  tab from the add-on page in Settings → Add-ons.
- **The HACS integration.** HACS renders
  `custom_components/openclaw_gateway/info.md` on the integration's
  detail page (the "Open in HACS" view in Settings → Devices &
  Integrations → HACS → OpenClaw Gateway). HACS also surfaces the
  GitHub *release notes* for the most recent tag below `info.md`, but
  `info.md` is the canonical "what is this" page and the right home for
  the integration changelog.

A combined `CHANGELOG.md` at the repo root would dump add-on-only
plumbing into the HACS user's face and HACS-shim quirks into the
add-on user's face. Neither audience wants the other half. So the
release Action writes **three** files from the same commit history,
filtered by Conventional Commit scope:

| File                                                  | Audience              | Scopes included                            |
| ----------------------------------------------------- | --------------------- | ------------------------------------------ |
| `addon/CHANGELOG.md`                                  | HA add-on operators   | `addon`, `node`, `gateway`, `both`         |
| `custom_components/openclaw_gateway/info.md`          | HACS integration users| `hacs`, `both`                             |
| `CHANGELOG.md` (repo root)                            | GitHub / maintainers  | all scopes (combined, for the release body)|

The repo-root `CHANGELOG.md` is also what the GitHub release body
shows. That's fine — it's the maintainer view. The HA user never has
to look at it.

### How the Action filters

release-please's stock output is a single combined `CHANGELOG.md`. To
get per-component files, the workflow has a post-processing step:

1. release-please opens (or updates) its "release PR" with the
   combined `CHANGELOG.md` + the version bumps. This is the standard
   release-please behaviour.
2. A follow-up step in the same workflow runs a small script that:
   - Reads the commits since the previous release tag.
   - Groups them by Conventional Commit scope.
   - Renders `addon/CHANGELOG.md` with the addon-eligible scopes.
   - Renders `info.md` with the hacs-eligible scopes (preserving the
     fixed "About OpenClaw Gateway" header that explains what the
     integration is).
   - Stages the changes so they end up in the release PR alongside the
     release-please-generated combined changelog.
3. When the maintainer merges the release PR, all three changelog
   files land at once and the GitHub release is cut.

The script is ~50 lines of Python — it already has the commit history
(release-please writes it into `.release-please-manifest.json`), and
the per-scope grouping is a `groupby` over Conventional Commit type +
scope. Implementation lands with the workflow itself; the design
above is the contract.

### What HA Supervisor and HACS actually read

- **HA Supervisor (add-on)** reads `addon/CHANGELOG.md` if the
  `changelog` key is absent from `addon/config.yaml`, OR fetches a URL
  if `changelog` is set. We use the file path (no URL) so HA renders
  the in-repo content directly.
- **HACS (integration)** reads `info.md` for the "About / Changelog"
  pane and renders the GitHub release body for the "Release notes"
  pane. With the combined `CHANGELOG.md` powering the release body,
  HACS users still see the full picture if they want it — but the
  default `info.md` pane is HACS-specific.

If we ever want HACS to show ONLY hacs-scoped commits in the release
notes pane too, we'd need separate release tags per component
(release-please can do this via its monorepo mode). That's a heavier
configuration and we don't need it today; the per-`info.md` filtering
already serves the same purpose for the audience that cares.

## Versioning policy

- **Pre-1.0:** the project stays on a single prerelease-channel CalVer
  (`YYYY.M.Pb<N>` during the current beta track; the prior alpha
  track used `YYYY.M.Pa<N>`). Every release bumps the prerelease
  increment (`b1` → `b2` → `b3` …) unless a breaking change forces a
  `Y.M.P` rev. Date-based bumps (`2026.6.20b6` → `2026.7.0b1`) happen
  when there's a deliberate cut, not because the calendar rolled over.
- **1.0 and beyond:** semver from the same base. Breaking changes
  bump major; new functionality bumps minor; fixes bump patch.
  Pre-1.0 the project moved from alphas to betas; that full history
  is preserved in the CHANGELOG.
- **Hot-fixes** are forward-fixes off `main`; we don't maintain
  long-lived release branches. If a stable user is on `2026.6.20b6`
  and we ship `2026.6.20b7` with a regression, the fix is `b8` not
  `b7.1`. This keeps the release pipeline single-tracked.

## Manual release procedure (until the Action lands)

Merging a release PR ("bump version strings to X") does **not** cut a
release on its own. HA Supervisor's "Update" prompt reads from
published GitHub releases, not from `main`, so the version bump on
`main` is invisible to end users until a tag + GitHub release exist
for it.

### Step 1: bump the five version files (one command)

The version literal lives in five files:

- `addon/config.yaml`
- `addon/build.yaml`
- `addon/node/pyproject.toml`
- `addon/node/src/openclaw_node/__init__.py`
- `custom_components/openclaw_gateway/manifest.json`

Hand-editing them is how drift happens (PR #148 bumped only
`addon/config.yaml`, the other four stayed on the prior version).
Use the bump script instead — it touches all five from one input:

```sh
scripts/bump-version.py 2026.6.20b7
```

The script enforces a PEP 440 shape and exits non-zero if the regex
can't find the literal in any file (so a renamed key surfaces loudly
instead of silently skipping). Verify with:

```sh
scripts/bump-version.py --check 2026.6.20b7
```

### Step 2: open + merge the release PR

Title it `release: 2026.6.20b7 — <one-line summary>`. Body should be
a short paragraph plus the changelog excerpt.

### Step 3: tag + cut the GitHub release

```sh
SHA=$(git rev-parse main)
git tag v2026.6.20b7 $SHA
git push origin v2026.6.20b7
gh release create v2026.6.20b7 \
  --title "2026.6.20b7 — <line from CHANGELOG>" \
  --prerelease \
  --notes "<short body; link to full CHANGELOG>"
```

If you bump versions in a PR titled `release: <version>` and you do
NOT also create a GitHub release for it, the addon is not released.
Either tag immediately after merge, or update the addon manually via
Rebuild and tell users that's the path.

## What this replaces

- The `test_version_sync.py` CI gate stays as the belt-and-braces
  check: if release-please ever misses a file or a hand-edit slips
  in, CI catches the drift.
- Manual version edits are forbidden. If a PR carries a version-file
  edit that isn't from release-please, the reviewer should ask the
  author to remove it.

## When to implement

Not yet. The audit-hardening work has items left and is more valuable
to land than the release plumbing. Once the audit checklists are
clean we cut a baseline release and turn on the Action.

Until then, **every PR that lands on `main` should already use
Conventional Commit subjects** so the first auto-generated changelog
isn't blind to half the history. The squash-merge subject becomes
the canonical commit on `main`, so PR titles are what counts.
