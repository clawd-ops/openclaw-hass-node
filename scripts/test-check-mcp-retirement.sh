#!/usr/bin/env bash
#
# Smoke test for scripts/check-mcp-retirement-readiness.sh.
# Feeds canned log lines on stdin and asserts the verdict + exit code.

set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")" && pwd)/check-mcp-retirement-readiness.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

pass=0
fail=0

assert() {
    local name="$1" expected="$2" actual="$3"
    if [[ "$actual" == "$expected" ]]; then
        printf '  ok   %s\n' "$name"
        pass=$((pass + 1))
    else
        printf '  FAIL %s (expected=%q got=%q)\n' "$name" "$expected" "$actual"
        fail=$((fail + 1))
    fi
}

# --- Scenario 1: clean input → OK with 0-day streak ---
set +e
out="$(printf '' | "$SCRIPT" 2>&1)"
ec=$?
set -e
assert "clean exit=0" "0" "$ec"
[[ "$out" == *"MCP_READINESS_OK"* ]] && { pass=$((pass+1)); echo "  ok   clean line"; } \
    || { fail=$((fail+1)); echo "  FAIL clean line: $out"; }

# --- Scenario 2: input contains a homeassistant mcp call → exit 1 ---
set +e
out="$(printf 'INFO routing tool=mcp__homeassistant__ha_get_state\n' | "$SCRIPT" 2>&1)"
ec=$?
set -e
assert "bad exit=1" "1" "$ec"
[[ "$out" == *"NOT_READY"* ]] && { pass=$((pass+1)); echo "  ok   not-ready line"; } \
    || { fail=$((fail+1)); echo "  FAIL not-ready: $out"; }
[[ "$out" == *"mcp__homeassistant__ha_get_state"* ]] \
    && { pass=$((pass+1)); echo "  ok   tool name surfaced"; } \
    || { fail=$((fail+1)); echo "  FAIL tool not surfaced: $out"; }

# --- Scenario 3: readonly variant also matched ---
set +e
out="$(printf 'tool=mcp__homeassistant-readonly__ha_list_states\n' | "$SCRIPT" 2>&1)"
ec=$?
set -e
assert "readonly bad exit=1" "1" "$ec"
[[ "$out" == *"mcp__homeassistant-readonly__ha_list_states"* ]] \
    && { pass=$((pass+1)); echo "  ok   readonly tool surfaced"; } \
    || { fail=$((fail+1)); echo "  FAIL readonly tool not surfaced: $out"; }

# --- Scenario 4: 7 clean days with state file → RETIREMENT_READY ---
STATE="$WORK/state"
for i in 1 2 3 4 5 6 7; do
    out="$(printf '' | "$SCRIPT" --state-file "$STATE" 2>&1)"
done
[[ "$out" == *"RETIREMENT_READY"* ]] \
    && { pass=$((pass+1)); echo "  ok   retirement_ready after 7 days"; } \
    || { fail=$((fail+1)); echo "  FAIL retirement_ready: $out"; }

# --- Scenario 5: bad call mid-streak → streak resets ---
set +e
printf 'mcp__homeassistant__ha_get_state\n' | "$SCRIPT" --state-file "$STATE" >/dev/null 2>&1
set -e
streak=$(<"$STATE.streak")
assert "streak reset to 0" "0" "$streak"

# --- Scenario 6: label is reflected in the verdict line ---
out="$(printf '' | "$SCRIPT" --label "production-cluster" 2>&1)"
[[ "$out" == *"label=production-cluster"* ]] \
    && { pass=$((pass+1)); echo "  ok   label surfaced"; } \
    || { fail=$((fail+1)); echo "  FAIL label not surfaced: $out"; }

# --- Scenario 7: unknown arg → exit 2 ---
set +e
"$SCRIPT" --no-such-flag </dev/null >/dev/null 2>&1
ec=$?
set -e
assert "unknown arg exit=2" "2" "$ec"

# --- Scenario 8: repo/docs text gets a misuse warning ---
set +e
out="$(printf 'docs/research/MIGRATION.md: `mcp__homeassistant__ha_get_state`\n' | "$SCRIPT" 2>&1)"
ec=$?
set -e
assert "repo text exit=1" "1" "$ec"
[[ "$out" == *"INPUT_WARNING"* ]] \
    && { pass=$((pass+1)); echo "  ok   repo text warning"; } \
    || { fail=$((fail+1)); echo "  FAIL repo text warning: $out"; }

echo
echo "passed=$pass failed=$fail"
[[ "$fail" -eq 0 ]]
