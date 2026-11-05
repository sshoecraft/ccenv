#!/bin/bash
# ccproject's two commands (ccproject_defects.py, ccproject_criteria.py) — where
# ccproject/install.sh puts them and what uninstall.sh takes away.
#
# They are installed as plain copies in the user bin dir. Nothing of them may be
# left in the skill dir, a box that an earlier build linked them on must be
# migrated to copies, and a file of that name the installer did not put there
# is never touched by either script.
#
# Every case runs the real ccproject/install.sh against a throwaway HOME and
# PYTHONUSERBASE under /tmp. uninstall.sh is only ever run with --dry-run: it
# also visits the checkout it runs from, and a dry run is enough to see which
# files it would remove.
#
#   ./tests/test_ccproject_commands.sh

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="ccproject_defects.py ccproject_criteria.py"
PASS=0
FAIL=0

ok()  { echo "  PASS  $1"; PASS=$((PASS + 1)); }
bad() { echo "  FAIL  $1"; echo "        $2"; FAIL=$((FAIL + 1)); }

new_home() {
    mktemp -d /tmp/ccproject-commands.XXXXXX
}

run_install() {
    HOME="$1" PYTHONUSERBASE="$1/.local" bash "$REPO/ccproject/install.sh" 2>&1
}

run_uninstall_dry() {
    HOME="$1" PYTHONUSERBASE="$1/.local" bash "$REPO/uninstall.sh" --dry-run --only ccproject 2>&1
}

skill_scripts() { echo "$1/.claude/skills/project-awareness/scripts"; }

# Every tool is a regular executable file in bin, byte-identical to the source.
check_installed_copies() {
    local label="$1" home="$2" tool dest
    for tool in $TOOLS; do
        dest="$home/.local/bin/$tool"
        if [ -L "$dest" ]; then
            bad "$label: $tool is a copy" "still a link -> $(readlink "$dest")"
        elif [ ! -f "$dest" ]; then
            bad "$label: $tool is a copy" "missing"
        elif ! cmp -s "$dest" "$REPO/ccproject/scripts/$tool"; then
            bad "$label: $tool is a copy" "differs from source"
        elif [ ! -x "$dest" ]; then
            bad "$label: $tool is a copy" "not executable"
        else
            ok "$label: $tool is a copy"
        fi
        if [ -e "$(skill_scripts "$home")/$tool" ]; then
            bad "$label: $tool not in skill dir" "$(skill_scripts "$home")/$tool exists"
        else
            ok "$label: $tool not in skill dir"
        fi
    done
}

# ---------------------------------------------------------------------------
echo "=== fresh install: copies in bin, nothing in the skill dir ==="
H=$(new_home)
OUT=$(run_install "$H"); RC=$?
[ "$RC" -eq 0 ] && ok "install exits 0" || bad "install exits 0" "rc=$RC: $OUT"
check_installed_copies "fresh" "$H"
"$H/.local/bin/ccproject_defects.py" --help >/dev/null 2>&1 \
    && ok "ccproject_defects.py --help runs from bin" \
    || bad "ccproject_defects.py --help runs from bin" "failed"
[ -f "$(skill_scripts "$H")/analyze_project.py" ] \
    && ok "skill's own analysis scripts still installed" \
    || bad "skill's own analysis scripts still installed" "analyze_project.py missing"

# ---------------------------------------------------------------------------
echo "=== reinstall over our own copies: replaced, no warning ==="
OUT=$(run_install "$H"); RC=$?
[ "$RC" -eq 0 ] && ok "second install exits 0" || bad "second install exits 0" "rc=$RC: $OUT"
echo "$OUT" | grep -q "WARNING" \
    && bad "no warning on reinstall" "$(echo "$OUT" | grep WARNING)" \
    || ok "no warning on reinstall"
check_installed_copies "reinstall" "$H"

# ---------------------------------------------------------------------------
echo "=== migration: an earlier build's skill-dir copy + bin link become a bin copy ==="
H=$(new_home)
mkdir -p "$(skill_scripts "$H")" "$H/.local/bin"
for tool in $TOOLS; do
    echo "# old skill-dir copy" > "$(skill_scripts "$H")/$tool"
    ln -s "$(skill_scripts "$H")/$tool" "$H/.local/bin/$tool"
done
OUT=$(run_install "$H"); RC=$?
[ "$RC" -eq 0 ] && ok "install exits 0" || bad "install exits 0" "rc=$RC: $OUT"
check_installed_copies "migrated" "$H"

# ---------------------------------------------------------------------------
echo "=== a user's own file and a user's own link by those names are left alone ==="
H=$(new_home)
mkdir -p "$H/.local/bin" "$H/elsewhere"
printf '#!/bin/sh\necho mine\n' > "$H/.local/bin/ccproject_defects.py"
chmod +x "$H/.local/bin/ccproject_defects.py"
printf '#!/bin/sh\necho also mine\n' > "$H/elsewhere/criteria"
ln -s "$H/elsewhere/criteria" "$H/.local/bin/ccproject_criteria.py"
OUT=$(run_install "$H"); RC=$?
[ "$RC" -eq 0 ] && ok "install exits 0" || bad "install exits 0" "rc=$RC: $OUT"
[ "$(cat "$H/.local/bin/ccproject_defects.py")" = "$(printf '#!/bin/sh\necho mine')" ] \
    && ok "user's file unchanged" || bad "user's file unchanged" "$(cat "$H/.local/bin/ccproject_defects.py")"
[ "$(readlink "$H/.local/bin/ccproject_criteria.py")" = "$H/elsewhere/criteria" ] \
    && ok "user's link unchanged" || bad "user's link unchanged" "$(readlink "$H/.local/bin/ccproject_criteria.py")"
[ "$(cat "$H/elsewhere/criteria")" = "$(printf '#!/bin/sh\necho also mine')" ] \
    && ok "user's link target not written through" || bad "user's link target not written through" "$(cat "$H/elsewhere/criteria")"
[ "$(echo "$OUT" | grep -c 'not installed by ccproject')" -eq 2 ] \
    && ok "both reported as left alone" || bad "both reported as left alone" "$OUT"
UOUT=$(run_uninstall_dry "$H")
echo "$UOUT" | grep -q "remove command:" \
    && bad "uninstall leaves user's files alone" "$(echo "$UOUT" | grep 'remove command:')" \
    || ok "uninstall leaves user's files alone"

# ---------------------------------------------------------------------------
echo "=== uninstall --dry-run: removes our copies and an earlier build's links ==="
H=$(new_home)
run_install "$H" >/dev/null
UOUT=$(run_uninstall_dry "$H")
for tool in $TOOLS; do
    echo "$UOUT" | grep -qF "remove command: $H/.local/bin/$tool" \
        && ok "would remove copy $tool" || bad "would remove copy $tool" "$UOUT"
done
H=$(new_home)
mkdir -p "$(skill_scripts "$H")" "$H/.local/bin"
for tool in $TOOLS; do
    echo "# old skill-dir copy" > "$(skill_scripts "$H")/$tool"
    ln -s "$(skill_scripts "$H")/$tool" "$H/.local/bin/$tool"
done
UOUT=$(run_uninstall_dry "$H")
for tool in $TOOLS; do
    echo "$UOUT" | grep -qF "remove command: $H/.local/bin/$tool" \
        && ok "would remove old link $tool" || bad "would remove old link $tool" "$UOUT"
done

echo ""
echo "=== $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
