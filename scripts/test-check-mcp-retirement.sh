#!/usr/bin/env bash
#
# Smoke test for scripts/check-mcp-retirement-readiness.sh.
# Stubs kubectl with a temp file that emits canned log lines and asserts the
# verdict / exit code for each scenario.

set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")" && pwd)/check-mcp-retirement-readiness.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir "$WORK/bin"
cat > "$WORK/bin/kubectl" <<'EOF'
#!/usr/bin/env bash
# Stub: echoes whatever is in $FAKE_LOG (empty by default).
cat "${FAKE_LOG:-/dev/null}"
EOF
chmod +x "$WORK/bin/kubectl"
export PATH="$WORK/bin:$PATH"

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

# --- Scenario 1: clean logs, no state file → OK with 0-day streak ---
: > "$WORK/empty.log"
export FAKE_LOG="$WORK/empty.log"
out="$("$SCRIPT" --since 1h 2>&1)"
ec=$?
assert "clean exit=0" "0" "$ec"
[[ "$out" == *"MCP_READINESS_OK"* ]] && { pass=$((pass+1)); echo "  ok   clean line"; } \
    || { fail=$((fail+1)); echo "  FAIL clean line: $out"; }

# --- Scenario 2: log contains a homeassistant mcp call → exit 1 ---
cat > "$WORK/bad.log" <<'LOG'
2026-06-06T16:00:00 INFO routing tool=mcp__homeassistant__ha_get_state
LOG
export FAKE_LOG="$WORK/bad.log"
set +e
out="$("$SCRIPT" --since 1h 2>&1)"
ec=$?
set -e
assert "bad exit=1" "1" "$ec"
[[ "$out" == *"NOT_READY"* ]] && { pass=$((pass+1)); echo "  ok   not-ready line"; } \
    || { fail=$((fail+1)); echo "  FAIL not-ready: $out"; }
[[ "$out" == *"mcp__homeassistant__ha_get_state"* ]] \
    && { pass=$((pass+1)); echo "  ok   tool name surfaced"; } \
    || { fail=$((fail+1)); echo "  FAIL tool not surfaced: $out"; }

# --- Scenario 3: 7 clean days with state file → RETIREMENT_READY ---
STATE="$WORK/state"
: > "$WORK/empty.log"
export FAKE_LOG="$WORK/empty.log"
for i in 1 2 3 4 5 6 7; do
    out="$("$SCRIPT" --since 1h --state-file "$STATE" 2>&1)"
done
[[ "$out" == *"RETIREMENT_READY"* ]] \
    && { pass=$((pass+1)); echo "  ok   retirement_ready after 7 days"; } \
    || { fail=$((fail+1)); echo "  FAIL retirement_ready: $out"; }

# --- Scenario 4: bad call mid-streak → streak resets ---
export FAKE_LOG="$WORK/bad.log"
set +e
"$SCRIPT" --since 1h --state-file "$STATE" >/dev/null 2>&1
set -e
streak=$(<"$STATE.streak")
assert "streak reset to 0" "0" "$streak"

# --- Scenario 5: kubectl missing → exit 2 ---
cat > "$WORK/bin/kubectl" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod +x "$WORK/bin/kubectl"
set +e
"$SCRIPT" --since 1h >/dev/null 2>&1
ec=$?
set -e
assert "kubectl failure exit=2" "2" "$ec"

echo
echo "passed=$pass failed=$fail"
[[ "$fail" -eq 0 ]]
