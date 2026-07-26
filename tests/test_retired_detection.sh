#!/bin/bash
# install.sh retired-component detection + scoped cleanup dispatch.
#
# Everything runs against throwaway HOME fixtures under /tmp. The cleanup path
# is exercised with a FAKE uninstall.sh that records its argv — the real
# uninstaller is never invoked, so no test run can remove anything from the
# box it runs on.
#
#   ./tests/test_retired_detection.sh

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0
FAIL=0

ok()   { echo "  PASS  $1"; PASS=$((PASS + 1)); }
bad()  { echo "  FAIL  $1"; echo "        $2"; FAIL=$((FAIL + 1)); }

# A HOME with nothing ccenv-related in it.
make_clean_home() {
    local h; h=$(mktemp -d /tmp/ccenv-retired-clean.XXXXXX)
    mkdir -p "$h/.claude/skills" "$h/.local/bin"
    echo '{}' > "$h/.claude/settings.json"
    echo '{}' > "$h/.claude.json"
    echo "$h"
}

# A HOME carrying one residue signal per retired component, each of a
# different kind, so a detector that only looks at one place fails here.
make_dirty_home() {
    local h; h=$(mktemp -d /tmp/ccenv-retired-dirty.XXXXXX)
    mkdir -p "$h/.claude/skills/ccinsight-integrate" "$h/.local/bin"
    : > "$h/.local/bin/ccprospect"           # ccprospect: leftover binary
    chmod +x "$h/.local/bin/ccprospect"
    cat > "$h/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "/home/x/.local/bin/ccprospect hook session"}]},
      {"hooks": [{"type": "command", "command": "/home/x/.local/bin/ccmemory hook session"}]}
    ]
  }
}
JSON
    cat > "$h/.claude.json" <<'JSON'
{"mcpServers": {"ccteam": {"command": "ccteam-mcp"}, "ccmemory": {"command": "ccmemory"}}}
JSON
    echo "$h"
}

# ---------------------------------------------------------------------------
echo "=== detection: clean HOME reports nothing ==="
H=$(make_clean_home)
OUT=$(HOME="$H" bash "$REPO/install.sh" --check-retired 2>&1)
if echo "$OUT" | grep -q "none — no uninstall needed"; then
    ok "clean box needs no uninstall"
else
    bad "clean box needs no uninstall" "$OUT"
fi
if echo "$OUT" | grep -qE "ccprospect:|ccinsight:|ccteam:"; then
    bad "clean box names no component" "$OUT"
else
    ok "clean box names no component"
fi
rm -rf "$H"

# ---------------------------------------------------------------------------
echo "=== detection: dirty HOME reports each component and why ==="
H=$(make_dirty_home)
OUT=$(HOME="$H" bash "$REPO/install.sh" --check-retired 2>&1)
for pair in "ccprospect:binary" "ccprospect:hook" "ccinsight:skill" "ccteam:MCP"; do
    comp=${pair%%:*}; kind=${pair##*:}
    if echo "$OUT" | grep -E "^  $comp:" | grep -q "$kind"; then
        ok "$comp detected via $kind"
    else
        bad "$comp detected via $kind" "$OUT"
    fi
done
# A component ccenv still ships must never be swept up by this.
if echo "$OUT" | grep -qE "^  ccmemory:"; then
    bad "shipping component untouched" "ccmemory was listed as retired residue"
else
    ok "shipping component untouched (ccmemory not listed)"
fi

# ---------------------------------------------------------------------------
echo "=== dispatch: uninstaller called --only per detected component ==="
# Stage install.sh next to a fake uninstall.sh that records argv instead of
# removing anything.
STAGE=$(mktemp -d /tmp/ccenv-retired-stage.XXXXXX)
cp "$REPO/install.sh" "$STAGE/install.sh"
ARGS_FILE="$STAGE/uninstall-argv.txt"
cat > "$STAGE/uninstall.sh" <<EOF
#!/bin/bash
printf '%s\n' "\$@" > "$ARGS_FILE"
exit 0
EOF
chmod +x "$STAGE/uninstall.sh"

# --only with a name no core section matches: the retired cleanup still runs
# (it is global hygiene), every component install is skipped, and the run
# stops before touching anything real.
HOME="$H" bash "$STAGE/install.sh" --only __none__ --no-overlays >"$STAGE/run.log" 2>&1
if [ -f "$ARGS_FILE" ]; then
    ok "uninstaller was invoked"
    ARGV=$(cat "$ARGS_FILE")
    for comp in ccprospect ccinsight ccteam; do
        if grep -qx -- "$comp" "$ARGS_FILE"; then
            ok "scoped --only $comp"
        else
            bad "scoped --only $comp" "$ARGV"
        fi
    done
    if grep -qx -- "--keep-project-data" "$ARGS_FILE"; then
        ok "project state kept by default"
    else
        bad "project state kept by default" "$ARGV"
    fi
    if grep -qx -- "-y" "$ARGS_FILE"; then
        ok "runs unattended (-y)"
    else
        bad "runs unattended (-y)" "$ARGV"
    fi
    # Nothing ccenv currently ships may be handed to the uninstaller.
    if grep -qxE -- "ccmemory|ccusage|ccloop|ccproject|gitsync|ccenvmcp" "$ARGS_FILE"; then
        bad "never scopes a shipping component" "$ARGV"
    else
        ok "never scopes a shipping component"
    fi
else
    bad "uninstaller was invoked" "$(tail -5 "$STAGE/run.log")"
fi

# ---------------------------------------------------------------------------
echo "=== dispatch: --purge-retired-state drops --keep-project-data ==="
rm -f "$ARGS_FILE"
HOME="$H" bash "$STAGE/install.sh" --only __none__ --no-overlays --purge-retired-state \
    >"$STAGE/run2.log" 2>&1
if [ -f "$ARGS_FILE" ] && ! grep -qx -- "--keep-project-data" "$ARGS_FILE"; then
    ok "--purge-retired-state passes state dirs through to the uninstaller"
else
    bad "--purge-retired-state passes state dirs through" "$(cat "$ARGS_FILE" 2>/dev/null)"
fi

# ---------------------------------------------------------------------------
echo "=== dispatch: --no-retired-cleanup skips it entirely ==="
rm -f "$ARGS_FILE"
HOME="$H" bash "$STAGE/install.sh" --only __none__ --no-overlays --no-retired-cleanup \
    >"$STAGE/run3.log" 2>&1
if [ -f "$ARGS_FILE" ]; then
    bad "--no-retired-cleanup skips the uninstaller" "$(cat "$ARGS_FILE")"
else
    ok "--no-retired-cleanup skips the uninstaller"
fi

# ---------------------------------------------------------------------------
echo "=== dispatch: clean box never calls the uninstaller ==="
rm -f "$ARGS_FILE"
CH=$(make_clean_home)
HOME="$CH" bash "$STAGE/install.sh" --only __none__ --no-overlays >"$STAGE/run4.log" 2>&1
if [ -f "$ARGS_FILE" ]; then
    bad "clean box does no work" "$(cat "$ARGS_FILE")"
else
    ok "clean box does no work"
fi
rm -rf "$CH" "$H" "$STAGE"

echo ""
echo "=== $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
