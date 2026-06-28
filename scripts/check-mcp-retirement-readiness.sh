#!/usr/bin/env bash
#
# Validation harness for retiring upstream `homeassistant` /
# `homeassistant-readonly` MCP servers in favour of the node command surface.
# See docs/research/MIGRATION.md.
#
# Source-agnostic: it does not know where logs come from. The caller pipes
# log lines on stdin and the script reports whether any
# `mcp__homeassistant{,-readonly}__*` invocations remain in the window.
#
# Usage:
#   <log-producer> | scripts/check-mcp-retirement-readiness.sh [--label LABEL] [--state-file PATH]
#
# Examples and deployment notes live in docs/RESEARCH-MIGRATION.md.
#
# Exit code:
#   0  – no calls in the input. If --state-file is given, the run is recorded
#        and the clean-day streak is incremented. Once the streak hits 7 the
#        verdict line prints `RETIREMENT_READY`.
#   1  – at least one matching call observed; streak resets and offending tool
#        names print on stderr.
#   2  – invalid arguments.

set -euo pipefail

LABEL="default"
STATE_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --label)      LABEL="$2"; shift 2 ;;
        --state-file) STATE_FILE="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)
            echo "Unknown arg: $1" >&2
            exit 2 ;;
    esac
done

# Read all log input from stdin. Empty input is a valid "no calls" signal.
input="$(cat)"

matches="$(printf '%s\n' "$input" | grep -oE 'mcp__homeassistant(-readonly)?__[a-zA-Z_]+' | sort -u || true)"

if [[ -n "$matches" ]]; then
    count="$(printf '%s\n' "$matches" | wc -l | tr -d ' ')"
    if [[ -n "$STATE_FILE" ]]; then
        printf '0' > "$STATE_FILE.streak"
    fi
    echo "MCP_READINESS_NOT_READY label=$LABEL unique_tools=$count"
    if printf '%s\n' "$input" | grep -Eq '(^|/)(docs|scripts|addon|custom_components)/|docs/research/MIGRATION|scripts/check-mcp-retirement-readiness'; then
        echo "MCP_READINESS_INPUT_WARNING input_looks_like_repo_text_not_runtime_logs" >&2
    fi
    printf '  %s\n' "$matches" >&2
    exit 1
fi

streak=0
if [[ -n "$STATE_FILE" ]]; then
    if [[ -f "$STATE_FILE.streak" ]]; then
        streak=$(<"$STATE_FILE.streak")
    fi
    streak=$((streak + 1))
    printf '%d' "$streak" > "$STATE_FILE.streak"
    touch "$STATE_FILE"
fi

if (( streak >= 7 )); then
    echo "RETIREMENT_READY label=$LABEL clean_streak=${streak}d"
else
    echo "MCP_READINESS_OK label=$LABEL clean_streak=${streak}d"
fi
