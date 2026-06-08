# Release Process

> Status: **proposed, not yet implemented.** The version-sync CI gate
> from PR #57 keeps the five version strings in lock-step today; the
> Action described here will automate the bump itself. Implementation
> waits until the audit-hardening work is done — but every PR opened
> in the meantime should already follow the [commit message convention](#commit-messages)
> so the changelog history is usable when the Action lands.

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
3. **Pre-release markers are first-class.** The current alpha
   (`2026.6.8a1`) is not an accident of timing; the project will
   live on `aN`/`bN`/`rcN` until it ships a 1.0. The release pipeline
   has to understand and preserve that.
4. **No backports, no parallel branches.** `main` is the only branch
   that ships. Hot-fixes are forward-fixes that cut a new release.

## Commit messages: Conventional Commits

Every commit on `main` (squash-merge messages count; PR titles end up
as the squash subject by default) follows
[Conventional Commits](https://www.conventionalcommits.org/) so the
release tool can categorise them deterministically:

| Prefix      | Section in changelog | Bumps                                |
| ----------- | -------------------- | ------------------------------------ |
| `feat:`     | Features             | minor (or alpha increment pre-1.0)   |
| `fix:`      | Bug Fixes            | patch (or alpha increment pre-1.0)   |
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
alpha bumps but still call them out in their own section so consumers
know to read the upgrade notes.

Scopes are encouraged but optional. The five we'll likely use:
`addon`, `node`, `hacs`, `gateway`, `docs`. Example:
`fix(node): reject paramsJSON that isn't an object`.

Existing audit-fix commits already loosely follow this pattern (`fix:`
prefixes for the bundles); the policy starts in earnest on the next
commit after this doc lands.

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
   GitHub release with the tag (`v2026.6.8a2` or whatever was
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
      "prerelease-type": "alpha",
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
{ ".": "2026.6.8a1" }
```

For each `extra-files` entry, release-please looks for a
`x-release-please-version` comment near the version literal and
updates the value next to it. We'd add markers like:

```yaml
# addon/config.yaml
version: "2026.6.8a1"  # x-release-please-version
```

```json
// custom_components/openclaw_gateway/manifest.json — JSON has no
// comments, so we use the JSON variant: a key named
// "x-release-please-version" sibling to "version" tells the tool
// which field to update.
{
  "version": "2026.6.8a1"
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

## Versioning policy

- **Pre-1.0:** the project stays on a single alpha-channel CalVer
  (`YYYY.M.Pa<N>`). Every release bumps the alpha increment (`a1` →
  `a2` → `a3` …) unless a breaking change forces a `Y.M.P` rev.
  Date-based bumps (`2026.6.8a1` → `2026.7.0a1`) happen when there's
  a deliberate cut, not because the calendar rolled over.
- **1.0 and beyond:** semver from the same base. Breaking changes
  bump major; new functionality bumps minor; fixes bump patch.
  Pre-1.0 the project has lived on alphas; that history is preserved
  in the CHANGELOG.
- **Hot-fixes** are forward-fixes off `main`; we don't maintain
  long-lived release branches. If a stable user is on `2026.6.8a3`
  and we ship `2026.6.8a4` with a regression, the fix is `a5` not
  `a4.1`. This keeps the release pipeline single-tracked.

## What this replaces

- The `test_version_sync.py` CI gate from PR #57 stays as the
  belt-and-braces check: if release-please ever misses a file or a
  hand-edit slips in, CI catches the drift.
- Manual version edits are forbidden. If a PR carries a version-file
  edit that isn't from release-please, the reviewer should ask the
  author to remove it.

## When to implement

Not yet. The audit-hardening work (issues #48 and #49) has 30+ items
left and is more valuable to land than the release plumbing. Once
the audit checklists are clean we cut a `2026.X.Ya1` baseline and
turn on the Action.

Until then, **every PR that lands on `main` should already use
Conventional Commit subjects** so the first auto-generated changelog
isn't blind to half the history. The squash-merge subject becomes
the canonical commit on `main`, so PR titles are what counts.
