#!/usr/bin/env bash
# generate-release-notes.sh — group Conventional Commit subjects since PREV_TAG.
#
# Usage: generate-release-notes.sh [<prev-tag>] [<end-ref>]
#   prev-tag : last release tag (e.g. v2026.7.1b1); omit for first-ever release
#   end-ref  : defaults to HEAD
#
# Prints grouped Markdown to stdout. Suitable for piping into a GitHub Release
# body or prepending to app/CHANGELOG.md.
set -euo pipefail

PREV_TAG="${1:-}"
END_REF="${2:-HEAD}"

if [[ -z "$PREV_TAG" ]]; then
  echo "_No previous release tag found; this is the first release._"
  exit 0
fi

# Collect subjects, skip merge commits
mapfile -t SUBJECTS < <(
  git log "${PREV_TAG}..${END_REF}" --pretty="%s" --no-merges 2>/dev/null || true
)

feat=()
fix=()
refactor=()
perf=()
docs=()
other=()

for subject in "${SUBJECTS[@]-}"; do
  [[ -z "$subject" ]] && continue

  # Skip release-bump chore commits (e.g. "chore(release): 2026.7.1b2")
  # Use variables to avoid bash [[ ]] bracket-parsing conflicts with char classes.
  _chore_ver_re='^chore(\([^)]*\))?:[[:space:]]*[0-9]'
  _chore_rel_re='^chore\(release\):[[:space:]]'
  if [[ "$subject" =~ $_chore_ver_re ]] || [[ "$subject" =~ $_chore_rel_re ]]; then
    continue
  fi

  # Extract conventional commit type and description
  _cc_re='^([a-zA-Z]+)(\([^)]*\))?!?:[[:space:]]+(.*)'
  if [[ "$subject" =~ $_cc_re ]]; then
    type="${BASH_REMATCH[1]}"
    desc="${BASH_REMATCH[3]}"
    case "$type" in
      feat|feature)         feat+=("$desc") ;;
      fix)                  fix+=("$desc") ;;
      refactor)             refactor+=("$desc") ;;
      perf|performance)     perf+=("$desc") ;;
      docs|doc)             docs+=("$desc") ;;
      ci|test|build|chore)  ;;  # infra-only: skip
      *)                    other+=("$subject") ;;
    esac
  else
    other+=("$subject")
  fi
done

if [[ ${#feat[@]} -gt 0 ]]; then
  printf '### Features\n'
  for item in "${feat[@]}"; do printf -- '- %s\n' "$item"; done
  printf '\n'
fi
if [[ ${#fix[@]} -gt 0 ]]; then
  printf '### Fixes\n'
  for item in "${fix[@]}"; do printf -- '- %s\n' "$item"; done
  printf '\n'
fi
if [[ ${#refactor[@]} -gt 0 ]]; then
  printf '### Refactor\n'
  for item in "${refactor[@]}"; do printf -- '- %s\n' "$item"; done
  printf '\n'
fi
if [[ ${#perf[@]} -gt 0 ]]; then
  printf '### Performance\n'
  for item in "${perf[@]}"; do printf -- '- %s\n' "$item"; done
  printf '\n'
fi
if [[ ${#docs[@]} -gt 0 ]]; then
  printf '### Docs\n'
  for item in "${docs[@]}"; do printf -- '- %s\n' "$item"; done
  printf '\n'
fi
if [[ ${#other[@]} -gt 0 ]]; then
  printf '### Other\n'
  for item in "${other[@]}"; do printf -- '- %s\n' "$item"; done
  printf '\n'
fi
