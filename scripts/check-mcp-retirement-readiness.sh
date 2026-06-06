#!/usr/bin/env bash
#
# Validation harness for retiring the `homeassistant` / `homeassistant-readonly`
# MCP servers from the OpenClaw gateway (per PLAN.md §3 / RESEARCH-MIGRATION.md).
#
# Reads the gateway pod's logs over a window, counts any remaining
# `mcp__homeassistant*` tool invocations, and prints a one-line readiness
# verdict suitable for a cron heartbeat.
#
# Usage:
#   scripts/check-mcp-retirement-readiness.sh [--since 24h] [--pod openclaw-0]
#                                              [--namespace ai] [--state-file PATH]
#
# Exit code:
#   0  – no calls in the window. If --state-file is given, the file is touched
#        to mark the day clean; the script also reports the running clean-day
#        streak. Once that streak hits 7, the verdict line prints "RETIREMENT_READY".
#   1  – at least one `mcp__homeassistant*` call observed. The clean-day streak
#        resets and the offending tool names are printed.
#   2  – kubectl call failed (cluster unreachable, pod not found, etc.).

set -euo pipefail

POD="openclaw-0"
NAMESPACE="ai"
SINCE="24h"
STATE_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pod)         POD="$2"; shift 2 ;;
        --namespace)   NAMESPACE="$2"; shift 2 ;;
        --since)       SINCE="$2"; shift 2 ;;
        --state-file)  STATE_FILE="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)
            echo "Unknown arg: $1" >&2
            exit 2 ;;
    esac
done

if ! command -v kubectl >/dev/null 2>&1; then
    echo "MCP_READINESS error=kubectl_missing" >&2
    exit 2
fi

if ! logs="$(kubectl -n "$NAMESPACE" logs "$POD" --since="$SINCE" 2>&1)"; then
    echo "MCP_READINESS error=kubectl_failed pod=$POD ns=$NAMESPACE since=$SINCE" >&2
    echo "$logs" >&2
    exit 2
fi

# Match the canonical MCP tool prefix the gateway logs when it forwards a
# tool call. Adjust the regex if the gateway log format changes.
matches="$(printf '%s\n' "$logs" | grep -oE 'mcp__homeassistant(-readonly)?__[a-zA-Z_]+' | sort -u || true)"

if [[ -n "$matches" ]]; then
    count="$(printf '%s\n' "$matches" | wc -l | tr -d ' ')"
    if [[ -n "$STATE_FILE" ]]; then
        printf '0' > "$STATE_FILE.streak"
    fi
    echo "MCP_READINESS_NOT_READY since=$SINCE pod=$POD unique_tools=$count"
    printf '  %s\n' $matches
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
    echo "RETIREMENT_READY clean_streak=${streak}d since=$SINCE pod=$POD"
else
    echo "MCP_READINESS_OK clean_streak=${streak}d since=$SINCE pod=$POD"
fi
