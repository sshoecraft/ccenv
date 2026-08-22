#!/bin/bash
# install.sh `settings` step — the model-stability knobs it enforces in
# ~/.claude/settings.json.
#
# The step's function is lifted out of install.sh by name and evaluated here,
# so the test runs the real code without running the rest of the installer
# (no pip installs, no MCP registration). Every case runs against a throwaway
# HOME fixture under /tmp — nothing on this box is read or written.
#
#   ./tests/test_settings_step.sh

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0
FAIL=0

ok()  { echo "  PASS  $1"; PASS=$((PASS + 1)); }
bad() { echo "  FAIL  $1"; echo "        $2"; FAIL=$((FAIL + 1)); }

# Stubs for the installer's output helpers, then the real function.
step() { :; }
info() { echo "  $*"; }
eval "$(sed -n '/^install_ccenv_settings() {$/,/^}$/p' "$REPO/install.sh")"
if ! declare -F install_ccenv_settings >/dev/null; then
    echo "could not extract install_ccenv_settings from install.sh" >&2
    exit 1
fi

new_home() {
    local h; h=$(mktemp -d /tmp/ccenv-settings.XXXXXX)
    mkdir -p "$h/.claude"
    echo "$h"
}

# Print one dotted key's JSON value from a fixture's settings.json.
val() {
    CCENV_TEST_SETTINGS="$1/.claude/settings.json" CCENV_TEST_KEY="$2" python3 - <<'PY'
import json, os
from pathlib import Path
data = json.loads(Path(os.environ["CCENV_TEST_SETTINGS"]).read_text())
cur = data
for part in os.environ["CCENV_TEST_KEY"].split("."):
    if isinstance(cur, dict):
        cur = cur.get(part)
    elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
        cur = cur[int(part)]
    else:
        cur = None
    if cur is None:
        break
print(json.dumps(cur))
PY
}

check() {
    local label="$1" home="$2" key="$3" want="$4"
    local got; got=$(val "$home" "$key")
    if [ "$got" = "$want" ]; then
        ok "$label"
    else
        bad "$label" "$key = $got (wanted $want)"
    fi
}

# ---------------------------------------------------------------------------
echo "=== no settings.json at all: the file is created with both knobs ==="
H=$(new_home)
OUT=$(HOME="$H" install_ccenv_settings 2>&1)
check "refusal fallback disabled" "$H" "env.CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK" '"1"'
check "switchModelsOnFlag false"  "$H" "switchModelsOnFlag" 'false'
echo "$OUT" | grep -q "set env.CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1" \
    && ok "reports the env var it set" || bad "reports the env var it set" "$OUT"

# ---------------------------------------------------------------------------
echo "=== unrelated settings survive; existing env keys are merged, not replaced ==="
H=$(new_home)
cat > "$H/.claude/settings.json" <<'JSON'
{
  "model": "opus",
  "env": {"PYTHONUSERBASE": "/home/x/.local"},
  "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "/x/check_sync_status.sh"}]}]}
}
JSON
HOME="$H" install_ccenv_settings >/dev/null 2>&1
check "existing model preserved"      "$H" "model" '"opus"'
check "existing env key preserved"    "$H" "env.PYTHONUSERBASE" '"/home/x/.local"'
check "existing hook preserved"       "$H" "hooks.SessionStart.0.hooks.0.type" '"command"'
check "refusal fallback added"        "$H" "env.CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK" '"1"'
check "switchModelsOnFlag added"      "$H" "switchModelsOnFlag" 'false'

# ---------------------------------------------------------------------------
echo "=== values already present are left alone, whatever they are ==="
H=$(new_home)
cat > "$H/.claude/settings.json" <<'JSON'
{"switchModelsOnFlag": true, "env": {"CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK": "0"}}
JSON
BEFORE=$(cat "$H/.claude/settings.json")
OUT=$(HOME="$H" install_ccenv_settings 2>&1)
check "user's env value kept"          "$H" "env.CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK" '"0"'
check "user's switchModelsOnFlag kept" "$H" "switchModelsOnFlag" 'true'
[ "$(cat "$H/.claude/settings.json")" = "$BEFORE" ] \
    && ok "file not rewritten when both keys present" \
    || bad "file not rewritten when both keys present" "$(cat "$H/.claude/settings.json")"
echo "$OUT" | grep -q 'left alone: switchModelsOnFlag=true' \
    && ok "reports what it left alone" || bad "reports what it left alone" "$OUT"

# ---------------------------------------------------------------------------
echo "=== one key present, one missing: only the missing one is seeded ==="
H=$(new_home)
echo '{"switchModelsOnFlag": true}' > "$H/.claude/settings.json"
HOME="$H" install_ccenv_settings >/dev/null 2>&1
check "present key untouched" "$H" "switchModelsOnFlag" 'true'
check "absent key seeded"     "$H" "env.CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK" '"1"'

# ---------------------------------------------------------------------------
echo "=== idempotent: a second run changes nothing and says so ==="
H=$(new_home)
HOME="$H" install_ccenv_settings >/dev/null 2>&1
BEFORE=$(cat "$H/.claude/settings.json")
MTIME_BEFORE=$(stat -c %Y "$H/.claude/settings.json" 2>/dev/null || stat -f %m "$H/.claude/settings.json")
OUT=$(HOME="$H" install_ccenv_settings 2>&1)
AFTER=$(cat "$H/.claude/settings.json")
MTIME_AFTER=$(stat -c %Y "$H/.claude/settings.json" 2>/dev/null || stat -f %m "$H/.claude/settings.json")
[ "$BEFORE" = "$AFTER" ] && ok "content unchanged on re-run" || bad "content unchanged on re-run" "$AFTER"
[ "$MTIME_BEFORE" = "$MTIME_AFTER" ] && ok "file not rewritten on re-run" || bad "file not rewritten on re-run" "mtime moved"
echo "$OUT" | grep -q "left alone" \
    && ok "reports no-op" || bad "reports no-op" "$OUT"

# ---------------------------------------------------------------------------
echo "=== --skip settings leaves settings.json alone ==="
H=$(new_home)
echo '{"model": "opus"}' > "$H/.claude/settings.json"
BEFORE=$(cat "$H/.claude/settings.json")
# should_install is what the installer gates the step on; exercise it directly.
SKIP=(settings); ONLY=()
eval "$(sed -n '/^should_install() {$/,/^}$/p' "$REPO/install.sh")"
if should_install settings; then
    bad "--skip settings skips the step" "should_install returned true"
else
    ok "--skip settings skips the step"
fi
[ "$(cat "$H/.claude/settings.json")" = "$BEFORE" ] \
    && ok "skipped fixture untouched" || bad "skipped fixture untouched" "file changed"

echo ""
echo "=== $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
