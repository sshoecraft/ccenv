#!/bin/bash
# ccenv — uninstall the Claude Code env/harness + everything it injected
#
# The exact inverse of install.sh. Removes, for every component in scope:
#
#   - the pip distribution (--user)
#   - its hook entries in ~/.claude/settings.json (foreign hooks preserved)
#   - its MCP registration in ~/.claude.json (user scope)
#   - its skill directory under ~/.claude/skills/
#   - its managed region in ~/.claude/CLAUDE.md
#   - the marker-fenced blocks the *-integrate skills injected into PROJECT
#     files (ccprospect / ccinsight), across every project Claude Code knows
#   - its per-project state directories
#
# Components:
#   ccproject gitsync ccmemory ccprospect ccinsight ccusage ccloop ccteam
#   ccenvmcp
#
# This list is EVERYTHING ccenv has ever installed, not just what the current
# install.sh ships. Running this script is the documented first step of an
# upgrade: uninstall, then install. A box being upgraded is by definition
# carrying the older set, so every one of these must be removable here — a
# component dropped from this list would strand its hooks, MCP registration,
# skills and state dirs with nothing able to clean them up.
#
# Usage:
#   ./uninstall.sh                       # remove EVERYTHING
#   ./uninstall.sh --only ccprospect     # remove one component (repeatable)
#   ./uninstall.sh --only ccprospect --only ccinsight --only ccteam
#   ./uninstall.sh --skip ccmemory       # remove all but one (repeatable)
#   ./uninstall.sh --dry-run             # print every action, change nothing
#   ./uninstall.sh -y                    # no confirmation prompt
#   ./uninstall.sh --keep-packages       # leave the pip dists installed
#   ./uninstall.sh --keep-project-data   # do not delete per-project state dirs
#   ./uninstall.sh --remove-path         # also strip the ~/.local/bin PATH guard
#   ./uninstall.sh --project DIR         # also clean DIR (repeatable) — for a repo
#                                        # that was integrated but never opened here
#   ./uninstall.sh -h                    # show this help
#
# PROJECT DATA POLICY
#   .ccprospect/  .ccinsight/  .ccteam/   -> ARCHIVED to ~/ccenv-uninstall-<stamp>/
#                                            as a .tar.gz, THEN deleted.
#                                            (opt out entirely: --keep-project-data)
#   .ccmemory/                            -> NEVER touched. It is committed repo
#                                            content and travels with the repo.
#   .ccloop/                              -> left in place (runtime state); listed.
#
# Every file this script rewrites is copied to <file>.uninstall-bak.<timestamp>
# first, and every directory it deletes is tarred first. Nothing is restored
# from git.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GLOBAL_CLAUDE_MD="$HOME/.claude/CLAUDE.md"
SETTINGS_JSON="$HOME/.claude/settings.json"
CLAUDE_JSON="$HOME/.claude.json"
SKILLS_DIR="$HOME/.claude/skills"
STAMP="$(date +%Y%m%d%H%M%S)"
# Every per-project state directory is tarred here before deletion.
STATE_ARCHIVE_DIR="$HOME/ccenv-uninstall-$STAMP"

SKIP=()
ONLY=()
DRY_RUN=0
ASSUME_YES=0
KEEP_PACKAGES=0
KEEP_PROJECT_DATA=0
REMOVE_PATH=0
FAILURES=0
EXTRA_PROJECTS=()

ALL_COMPONENTS=(ccproject gitsync ccmemory ccprospect ccinsight ccusage ccloop ccteam ccenvmcp)

while [ $# -gt 0 ]; do
    case "$1" in
        --only) ONLY+=("$2"); shift 2 ;;
        --skip) SKIP+=("$2"); shift 2 ;;
        --dry-run|-n) DRY_RUN=1; shift ;;
        -y|--yes) ASSUME_YES=1; shift ;;
        --keep-packages) KEEP_PACKAGES=1; shift ;;
        --keep-project-data) KEEP_PROJECT_DATA=1; shift ;;
        --remove-path) REMOVE_PATH=1; shift ;;
        --project) EXTRA_PROJECTS+=("$2"); shift 2 ;;
        -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 1 ;;
    esac
done

step() { echo ""; echo "=== [$1] $2 ==="; }
info() { echo "  $*"; }
warn() { echo "  WARNING: $*" >&2; FAILURES=$((FAILURES + 1)); }
act()  { if [ "$DRY_RUN" = "1" ]; then echo "  [dry-run] $*"; else echo "  $*"; fi; }

should_remove() {
    local name="$1"
    if [ ${#ONLY[@]} -gt 0 ]; then
        for x in "${ONLY[@]}"; do [ "$x" = "$name" ] && return 0; done
        return 1
    fi
    for x in "${SKIP[@]}"; do [ "$x" = "$name" ] && return 1; done
    return 0
}

# True only for a completely unscoped run — the global artifacts (the
# [CCENV MANAGED] CLAUDE.md region, ~/.config/ccenv, the shell env exports)
# belong to the bundle as a whole, not to any one component, so a partial
# uninstall must leave them alone.
is_full_uninstall() {
    [ ${#ONLY[@]} -eq 0 ] && [ ${#SKIP[@]} -eq 0 ]
}

# The snapshot path for a file this run may rewrite.
backup_path() { printf '%s.uninstall-bak.%s\n' "$1" "$STAMP"; }

# Snapshot a file before the first modification of this run.
#
# Two properties, both learned the hard way:
#
# - A no-op once the backup exists. Several files are rewritten more than once
#   in a single run (~/.claude/CLAUDE.md loses the component's section, then
#   the [CCENV MANAGED] region; settings.json and ~/.claude.json are touched
#   per component). Re-copying would leave a "backup" holding the INTERMEDIATE
#   state, which is worse than useless — the point is the state before the
#   uninstall started.
# - Callers must invoke it only when a change is actually about to happen.
#   Backing up on the mere ATTEMPT means every idempotent re-run litters the
#   home directory with identical copies. The shell callers grep-gate first;
#   the Python writers call bak() themselves, immediately before os.replace.
backup_file() {
    local f="$1"
    [ -f "$f" ] || return 0
    [ "$DRY_RUN" = "1" ] && return 0
    local bak; bak=$(backup_path "$f")
    [ -f "$bak" ] && return 0
    cp -p "$f" "$bak" 2>/dev/null || warn "could not back up $f"
}

# The embedded Python writers define their own bak() against this stamp — they
# know whether they are about to change anything, and the shell does not.
export CCENV_STAMP="$STAMP"

rm_path() {
    local p="$1" what="$2"
    if [ ! -e "$p" ]; then
        return 1
    fi
    act "remove $what: $p"
    if [ "$DRY_RUN" != "1" ]; then
        rm -rf "$p" || { warn "failed to remove $p"; return 1; }
    fi
    return 0
}

# ----------------------------------------------------------------------------
# Environment — mirror install.sh so pip and the console scripts resolve the
# same way they did at install time.
# ----------------------------------------------------------------------------
echo "=== ccenv uninstaller ==="
command -v python3 >/dev/null || { echo "ERROR: python3 required"; exit 1; }

export PYTHONUSERBASE="${PYTHONUSERBASE:-$HOME/.local}"
USER_BIN="$PYTHONUSERBASE/bin"
case ":$PATH:" in
    *":$USER_BIN:"*) ;;
    *) export PATH="$USER_BIN:$PATH" ;;
esac

# PEP 668 boxes need the same override pip install used, or `pip uninstall`
# of a --user dist refuses to run.
EXTERNALLY_MANAGED=$(python3 -c 'import os, sysconfig; print(os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED"))' 2>/dev/null || true)
if [ -n "$EXTERNALLY_MANAGED" ] && [ -f "$EXTERNALLY_MANAGED" ] \
   && python3 -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
    export PIP_BREAK_SYSTEM_PACKAGES=1
fi

HAS_CLAUDE=0
command -v claude >/dev/null && HAS_CLAUDE=1

IN_SCOPE=()
for c in "${ALL_COMPONENTS[@]}"; do
    should_remove "$c" && IN_SCOPE+=("$c")
done
if [ ${#IN_SCOPE[@]} -eq 0 ]; then
    echo "nothing in scope — check your --only/--skip flags"; exit 1
fi

info "components in scope: ${IN_SCOPE[*]}"
if is_full_uninstall; then
    info "scope: FULL — global artifacts (CLAUDE.md managed region, ~/.config/ccenv, shell exports) included"
else
    info "scope: PARTIAL — global artifacts left in place"
fi
[ "$DRY_RUN" = "1" ] && info "DRY RUN — nothing will be modified"

# ----------------------------------------------------------------------------
# Discover every project Claude Code knows about.
#
# ~/.claude.json's "projects" map is the authoritative, bounded list of
# directories the user has actually opened a session in. We use it instead of
# walking the filesystem: a root-anchored scan is both banned by this repo's
# rules and useless here, since an injected block can only exist somewhere a
# session ran.
# ----------------------------------------------------------------------------
PROJECT_LIST="$(mktemp "/tmp/ccenv-uninstall-projects.XXXXXX")"
trap 'rm -f "$PROJECT_LIST"' EXIT

CCENV_SRC="$SCRIPT_DIR" CCENV_EXTRA="$(printf '%s\n' "${EXTRA_PROJECTS[@]+"${EXTRA_PROJECTS[@]}"}")" \
python3 - "$CLAUDE_JSON" > "$PROJECT_LIST" <<'PY'
import json, os, sys

seen, out = set(), []

def add(p):
    if not p:
        return
    p = os.path.realpath(os.path.expanduser(p))
    if p in seen or not os.path.isdir(p):
        return
    seen.add(p)
    out.append(p)

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
except (OSError, ValueError):
    data = {}

for p in (data.get("projects") or {}):
    add(p)

# The checkout running this script may never have been opened as a session.
add(os.environ.get("CCENV_SRC"))

# Anything the user named explicitly with --project (a repo that was
# integrated but never opened as a Claude Code session here).
for p in (os.environ.get("CCENV_EXTRA") or "").splitlines():
    add(p.strip())

for p in out:
    print(p)
PY

PROJECT_COUNT=$(wc -l < "$PROJECT_LIST" | tr -d ' ')
info "known project directories: $PROJECT_COUNT"

# ----------------------------------------------------------------------------
# Confirmation — the project-level work (deleting state dirs, rewriting files
# inside the user's repos) is the irreversible part, so gate on it explicitly.
# ----------------------------------------------------------------------------
if [ "$DRY_RUN" != "1" ] && [ "$ASSUME_YES" != "1" ]; then
    echo ""
    echo "This will remove ccenv components (${IN_SCOPE[*]}) from this machine,"
    echo "strip their hooks/MCP/skills, and rewrite files under $PROJECT_COUNT project"
    echo "directories to delete injected integration blocks."
    if [ "$KEEP_PROJECT_DATA" != "1" ]; then
        echo "It will also DELETE .ccprospect/, .ccinsight/ and .ccteam/ state dirs"
        echo "(each tarred to $STATE_ARCHIVE_DIR/ first)."
    fi
    echo ".ccmemory/ is never touched. Backups: <file>.uninstall-bak.$STAMP"
    echo ""
    printf "Proceed? [y/N] "
    read -r reply
    case "$reply" in
        y|Y|yes|YES) ;;
        *) echo "aborted"; exit 1 ;;
    esac
fi

# ----------------------------------------------------------------------------
# settings.json — strip every hook entry owned by an in-scope component.
#
# Matching is by EXECUTABLE, not substring: the first token of the command must
# be the component's console script (or, for ccproject, awareness_hooks.py must
# be the script argument). A foreign hook that merely mentions the word in an
# argument is never touched, and neither is a foreign hook sharing an entry
# with one of ours — only the individual hook object is dropped.
# ----------------------------------------------------------------------------
strip_settings_hooks() {
    step "settings.json" "removing hook entries for: ${IN_SCOPE[*]}"
    if [ ! -f "$SETTINGS_JSON" ]; then
        info "$SETTINGS_JSON does not exist — nothing to strip"
        return
    fi
    CCENV_SCOPE="${IN_SCOPE[*]}" CCENV_DRY="$DRY_RUN" \
    python3 - "$SETTINGS_JSON" <<'PY'
import json, os, shutil, sys

def bak(path):
    """Snapshot path, once per run. Called only when about to write."""
    dest = "%s.uninstall-bak.%s" % (path, os.environ["CCENV_STAMP"])
    if not os.path.exists(dest):
        try:
            shutil.copy2(path, dest)
        except OSError:
            print("  WARNING: could not back up %s" % path, file=sys.stderr)

path = sys.argv[1]
scope = set(os.environ["CCENV_SCOPE"].split())
dry = os.environ["CCENV_DRY"] == "1"

# component -> (console-script basenames, allowed trailing subcommands)
OWNED = {
    "ccmemory":   ({"ccmemory"},              {"session", "stop", "guard", "inject"}),
    "ccprospect": ({"ccprospect"},            {"session", "stop", "guard"}),
    "ccinsight":  ({"ccinsight"},             {"session", "stop", "guard", "posttool"}),
    "ccloop":     ({"ccloop"},                {"guard", "keepgoing"}),
    "ccteam":     ({"ccteam", "ccteam-mcp"},  {"session-start"}),
}

def owner(command):
    """Return the component that owns this hook command, or None."""
    if not isinstance(command, str) or not command.strip():
        return None
    parts = command.split()
    exe = os.path.basename(parts[0])

    # ccproject: "<python> <.../awareness_hooks.py> <sub>"
    if any(os.path.basename(p) == "awareness_hooks.py" for p in parts):
        return "ccproject"
    # gitsync: "<.../check_sync_status.sh>"
    if exe == "check_sync_status.sh":
        return "gitsync"

    for comp, (exes, subs) in OWNED.items():
        if exe in exes and len(parts) >= 2 and parts[-1] in subs:
            return comp
    return None

try:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    data = json.loads(text) if text.strip() else {}
except (OSError, ValueError) as exc:
    print("  WARNING: cannot parse %s (%s) — skipping hook strip" % (path, exc),
          file=sys.stderr)
    sys.exit(1)

hooks = data.get("hooks") or {}
removed = []
changed = False

for event in list(hooks):
    entries = hooks.get(event) or []
    rebuilt = []
    for entry in entries:
        if not isinstance(entry, dict):
            rebuilt.append(entry)
            continue
        kept = []
        for h in (entry.get("hooks") or []):
            comp = owner(h.get("command") if isinstance(h, dict) else None)
            if comp and comp in scope:
                removed.append("%s: %s  [%s]" % (event, h.get("command"), comp))
                changed = True
            else:
                kept.append(h)
        if kept:
            e = dict(entry)
            e["hooks"] = kept
            rebuilt.append(e)
        elif not (entry.get("hooks") or []):
            rebuilt.append(entry)   # an entry that had no hooks to begin with
    if rebuilt:
        hooks[event] = rebuilt
    else:
        hooks.pop(event, None)
        changed = True

if hooks:
    data["hooks"] = hooks
else:
    data.pop("hooks", None)

# ccusage owns the statusLine.
if "ccusage" in scope:
    sl = data.get("statusLine")
    if isinstance(sl, dict) and "ccusage-statusline" in str(sl.get("command", "")):
        removed.append("statusLine: %s  [ccusage]" % sl.get("command"))
        data.pop("statusLine", None)
        changed = True
    elif sl is not None:
        print("  statusLine points elsewhere (%s) — left alone" % sl)

for r in removed:
    print("  %sremoved hook %s" % ("[dry-run] " if dry else "", r))
if not removed:
    print("  no matching hook entries found")

if changed and not dry:
    bak(path)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    print("  rewrote %s" % path)
PY
    [ $? -eq 0 ] || warn "hook strip reported a problem"
}

# ----------------------------------------------------------------------------
# MCP registrations — drop the user-scope entries in ~/.claude.json.
#
# `claude mcp remove` is the supported path and is safe under concurrent
# sessions, so it goes first. We then verify against ~/.claude.json and fall
# back to a direct atomic edit if the CLI is missing or left the entry behind.
# ----------------------------------------------------------------------------
unregister_mcp() {
    local name="$1"
    if [ "$DRY_RUN" = "1" ]; then
        if python3 -c "
import json,sys
try: d=json.load(open('$CLAUDE_JSON'))
except Exception: sys.exit(1)
sys.exit(0 if '$name' in (d.get('mcpServers') or {}) else 1)
" 2>/dev/null; then
            act "unregister MCP server '$name'"
        else
            info "MCP server '$name' not registered"
        fi
        return
    fi

    if [ "$HAS_CLAUDE" = "1" ]; then
        claude mcp remove -s user "$name" >/dev/null 2>&1
    fi

    CCENV_MCP_NAME="$name" python3 - "$CLAUDE_JSON" <<'PY'
import json, os, shutil, sys

def bak(path):
    """Snapshot path, once per run. Called only when about to write."""
    dest = "%s.uninstall-bak.%s" % (path, os.environ["CCENV_STAMP"])
    if not os.path.exists(dest):
        try:
            shutil.copy2(path, dest)
        except OSError:
            print("  WARNING: could not back up %s" % path, file=sys.stderr)

name, path = os.environ["CCENV_MCP_NAME"], sys.argv[1]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except (OSError, ValueError):
    print("  WARNING: cannot read %s — verify MCP '%s' by hand" % (path, name),
          file=sys.stderr)
    sys.exit(0)

servers = data.get("mcpServers")
if not isinstance(servers, dict) or name not in servers:
    print("  MCP server '%s' not registered" % name)
    sys.exit(0)

servers.pop(name, None)
bak(path)
tmp = "%s.tmp.%d" % (path, os.getpid())
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
os.replace(tmp, path)
print("  unregistered MCP server '%s'" % name)
PY
}

# ----------------------------------------------------------------------------
# pip uninstall — a --user dist. `pip uninstall -y` is a no-op-with-message
# when the dist is absent, so this is safe to call unconditionally.
# ----------------------------------------------------------------------------
pip_uninstall() {
    local dist="$1"
    if [ "$KEEP_PACKAGES" = "1" ]; then
        info "--keep-packages: leaving pip dist '$dist' installed"
        return
    fi
    if ! python3 -m pip show "$dist" >/dev/null 2>&1; then
        info "pip dist '$dist' not installed"
        return
    fi
    act "pip uninstall $dist"
    [ "$DRY_RUN" = "1" ] && return
    python3 -m pip uninstall -y "$dist" 2>&1 | sed 's/^/    /' \
        || warn "pip uninstall $dist failed"
}

# ----------------------------------------------------------------------------
# Project-file surgery — remove a marker-fenced integration block.
#
# The *-integrate skills fence their block with an exact HTML comment pair, so
# removal is deterministic: drop everything from the opening marker through the
# closing marker, collapsing the blank lines the block left behind.
# ----------------------------------------------------------------------------
strip_marker_block() {
    local file="$1" open="$2" close="$3"
    CCENV_OPEN="$open" CCENV_CLOSE="$close" CCENV_DRY="$DRY_RUN" \
    python3 - "$file" <<'PY'
import os, sys

path = sys.argv[1]
open_m, close_m = os.environ["CCENV_OPEN"], os.environ["CCENV_CLOSE"]
dry = os.environ["CCENV_DRY"] == "1"

try:
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
except (OSError, UnicodeDecodeError):
    sys.exit(2)

# Markers inside a fenced code block are DOCUMENTATION, not an injection.
# This file's own docs quote both opening markers in a ``` fence; an earlier
# version treated them as real and ate the rest of the document.
out, skipping, found, fenced = [], False, False, False
for line in lines:
    stripped = line.lstrip()
    if not skipping and (stripped.startswith("```") or stripped.startswith("~~~")):
        fenced = not fenced
        out.append(line)
        continue
    if not skipping and open_m in line and not fenced:
        skipping, found = True, True
        continue
    if skipping:
        if close_m in line:
            skipping = False
        continue
    out.append(line)

if skipping:
    # An opening marker with no close. Removing to EOF here would destroy
    # everything below it — which is exactly how this script once truncated
    # its own documentation. Never guess at the extent: leave the file
    # untouched and make the caller report it for a human to look at.
    sys.exit(3)

if not found:
    sys.exit(1)

# Collapse a run of blank lines left where the block used to be.
collapsed, blanks = [], 0
for line in out:
    if line.strip() == "":
        blanks += 1
        if blanks > 2:
            continue
    else:
        blanks = 0
    collapsed.append(line)
while collapsed and collapsed[-1].strip() == "":
    collapsed.pop()

if not dry:
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(collapsed)
        if collapsed and not collapsed[-1].endswith("\n"):
            fh.write("\n")
    os.replace(tmp, path)

sys.exit(0)
PY
    return $?
}

# Find, and clean, every project file carrying a component's integration block.
#
# Candidate files are bounded on purpose: markdown at depth <= 2 in the project
# (covers CLAUDE.md, docs/*.md, .claude/*.md, criteria files) plus the exact
# binding_file the skill recorded in integration.json, which may sit deeper
# (a custom loop's constitution fragment).
clean_project_injections() {
    local comp="$1" open="$2" close="$3" statedir="$4"
    step "$comp" "removing injected blocks from project files"

    local hits=0 proj bfile f
    while IFS= read -r proj; do
        [ -d "$proj" ] || continue

        # Files the recorded integration points at (any depth).
        local extra=""
        if [ -f "$proj/$statedir/integration.json" ]; then
            extra=$(python3 -c "
import json,sys,os
try:
    d=json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
b=d.get('binding_file')
if b:
    p=os.path.join(sys.argv[2], b)
    if os.path.isfile(p): print(p)
" "$proj/$statedir/integration.json" "$proj" 2>/dev/null)
        fi

        # Process substitution, not a pipe: the loop must run in THIS shell so
        # warn()'s failure count survives it.
        while IFS= read -r f; do
            [ -f "$f" ] || continue
            grep -qF "$open" "$f" 2>/dev/null || continue
            backup_file "$f"
            strip_marker_block "$f" "$open" "$close"
            case "$?" in
                0) act "stripped $comp block from $f" ;;
                1) rm -f "$(backup_path "$f")" ;;   # only fenced/doc mentions
                3) warn "$f has an OPENING $comp marker with no closing marker — left untouched, fix it by hand"
                   rm -f "$(backup_path "$f")" ;;
                *) warn "failed to strip $comp block from $f" ;;
            esac
        done < <( { find "$proj" -maxdepth 2 -type f -name '*.md' 2>/dev/null
                    [ -n "$extra" ] && printf '%s\n' "$extra"; } | sort -u )

        if [ -f "$proj/$statedir/integration.json" ]; then
            hits=$((hits + 1))
        fi
    done < "$PROJECT_LIST"

    [ "$hits" -gt 0 ] && info "$hits project(s) had a recorded $statedir/integration.json"
    return 0
}

# Delete a per-project state directory across every known project.
#
# Every directory is archived to $STATE_ARCHIVE_DIR before it is removed.
# A state dir can hold months of accumulated contracts/observations and there
# is no other copy of it — a plain `rm -rf` here would be unrecoverable for
# anything the project never committed.
purge_project_state() {
    local comp="$1" statedir="$2"
    if [ "$KEEP_PROJECT_DATA" = "1" ]; then
        info "--keep-project-data: leaving $statedir/ directories in place"
        return
    fi
    local n=0 proj arc
    while IFS= read -r proj; do
        [ -d "$proj/$statedir" ] || continue

        # Mangle the project path into a flat, collision-free archive name.
        arc="$STATE_ARCHIVE_DIR/$(printf '%s' "${proj#/}" | tr '/' '-')${statedir}.tar.gz"
        if [ "$DRY_RUN" != "1" ]; then
            mkdir -p "$STATE_ARCHIVE_DIR"
            if tar czf "$arc" -C "$proj" "$statedir" 2>/dev/null; then
                info "archived $proj/$statedir -> $arc"
            else
                warn "could NOT archive $proj/$statedir — leaving it in place"
                continue
            fi
        else
            act "archive $proj/$statedir -> $arc"
        fi

        if rm_path "$proj/$statedir" "$comp state"; then
            n=$((n + 1))
        fi
    done < "$PROJECT_LIST"
    if [ "$n" -eq 0 ]; then
        info "no $statedir/ directories found"
    else
        info "removed $n $statedir/ director$([ "$n" = 1 ] && echo y || echo ies)"
    fi
}

# ============================================================================
# Run it
# ============================================================================

strip_settings_hooks

# ----------------------------------------------------------------------------
# ccproject — skill dir + its [AWARENESS PROTOCOL] section in the global
# CLAUDE.md. ccproject appends that section at EOF and its own installer
# treats "marker to EOF" as the section extent, so removal mirrors that.
# ----------------------------------------------------------------------------
if should_remove ccproject; then
    step ccproject "removing awareness skill + global CLAUDE.md section"
    rm_path "$SKILLS_DIR/project-awareness" "skill" \
        || info "skill dir not present"
    if [ -f "$GLOBAL_CLAUDE_MD" ] && grep -q '^# \[AWARENESS PROTOCOL\]' "$GLOBAL_CLAUDE_MD"; then
        backup_file "$GLOBAL_CLAUDE_MD"
        act "remove [AWARENESS PROTOCOL] section from $GLOBAL_CLAUDE_MD"
        if [ "$DRY_RUN" != "1" ]; then
            tmp=$(mktemp)
            sed '/^# \[AWARENESS PROTOCOL\]/,$d' "$GLOBAL_CLAUDE_MD" > "$tmp" \
                && mv "$tmp" "$GLOBAL_CLAUDE_MD" \
                || warn "failed to strip [AWARENESS PROTOCOL]"
        fi
    else
        info "no [AWARENESS PROTOCOL] section in $GLOBAL_CLAUDE_MD"
    fi
    info "per-project .claude/awareness/ docs are project content — left in place"
fi

# ----------------------------------------------------------------------------
# gitsync — the SessionStart repo-sync hook script and its source-path marker.
# (Its settings.json entry was already removed above.)
# ----------------------------------------------------------------------------
if should_remove gitsync; then
    step gitsync "removing SessionStart sync hook"
    rm_path "$HOME/.claude/hooks/check_sync_status.sh" "hook script" \
        || info "hook script not present"
    # ccenv created ~/.claude/hooks; take it back only if we left it empty.
    if [ "$DRY_RUN" != "1" ] && [ -d "$HOME/.claude/hooks" ]; then
        rmdir "$HOME/.claude/hooks" 2>/dev/null \
            && info "removed now-empty $HOME/.claude/hooks"
    fi
    rm_path "$HOME/.config/ccenv/source.path" "source marker" \
        || info "source marker not present"
fi

# ----------------------------------------------------------------------------
# ccmemory — MCP + compile-memories skill + package.
# Project .ccmemory/ stores are NEVER touched: they are committed repo content.
# ----------------------------------------------------------------------------
if should_remove ccmemory; then
    step ccmemory "unregistering MCP + removing skill + package"
    unregister_mcp ccmemory
    rm_path "$SKILLS_DIR/compile-memories" "skill" || info "skill dir not present"
    pip_uninstall ccmemory

    n=0
    while IFS= read -r proj; do
        [ -d "$proj/.ccmemory" ] && n=$((n + 1))
    done < "$PROJECT_LIST"
    info "PRESERVED: $n project(s) still have .ccmemory/ (committed repo content — not touched)"
fi

# ----------------------------------------------------------------------------
# ccprospect — MCP + skill + package + injected blocks + state dirs.
# ----------------------------------------------------------------------------
if should_remove ccprospect; then
    step ccprospect "unregistering MCP + removing skill + package"
    unregister_mcp ccprospect
    rm_path "$SKILLS_DIR/prospect-integrate" "skill" || info "skill dir not present"
    pip_uninstall ccprospect

    clean_project_injections ccprospect \
        "<!-- [CCPROSPECT INTEGRATION]" \
        "<!-- [/CCPROSPECT INTEGRATION] -->" \
        ".ccprospect"
    step ccprospect "purging per-project state"
    purge_project_state ccprospect ".ccprospect"
fi

# ----------------------------------------------------------------------------
# ccinsight — MCP + skill + package + injected blocks + state dirs.
# ----------------------------------------------------------------------------
if should_remove ccinsight; then
    step ccinsight "unregistering MCP + removing skill + package"
    unregister_mcp ccinsight
    rm_path "$SKILLS_DIR/ccinsight-integrate" "skill" || info "skill dir not present"
    pip_uninstall ccinsight

    clean_project_injections ccinsight \
        "<!-- [CCINSIGHT INTEGRATION]" \
        "<!-- [/CCINSIGHT INTEGRATION] -->" \
        ".ccinsight"
    step ccinsight "purging per-project state"
    purge_project_state ccinsight ".ccinsight"
fi

# ----------------------------------------------------------------------------
# ccusage — MCP + package. The statusLine entry went out with the hook strip.
# A root-scope install also writes /etc/claude-code/*.json.
# ----------------------------------------------------------------------------
if should_remove ccusage; then
    step ccusage "unregistering MCP + removing statusline + package"
    unregister_mcp ccusage
    pip_uninstall ccusage-mcp

    for f in /etc/claude-code/managed-settings.json /etc/claude-code/managed-mcp.json; do
        [ -f "$f" ] || continue
        if grep -q 'ccusage' "$f" 2>/dev/null; then
            if [ -w "$f" ]; then
                backup_file "$f"
                act "strip ccusage entries from $f"
                if [ "$DRY_RUN" != "1" ]; then
                    CCENV_F="$f" python3 - <<'PY'
import json, os
p = os.environ["CCENV_F"]
try:
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
except (OSError, ValueError):
    raise SystemExit(0)
ch = False
if isinstance(d.get("mcpServers"), dict) and d["mcpServers"].pop("ccusage", None) is not None:
    ch = True
sl = d.get("statusLine")
if isinstance(sl, dict) and "ccusage-statusline" in str(sl.get("command", "")):
    d.pop("statusLine", None); ch = True
if ch:
    tmp = "%s.tmp.%d" % (p, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2); fh.write("\n")
    os.replace(tmp, p)
    print("  cleaned %s" % p)
PY
                fi
            else
                warn "$f mentions ccusage but is not writable — re-run as root to clean it"
            fi
        fi
    done
fi

# ----------------------------------------------------------------------------
# ccloop — package only; its hooks went out with the settings strip. Per-project
# .ccloop/ run state is left alone (it is loop scratch, not harness config).
# ----------------------------------------------------------------------------
if should_remove ccloop; then
    step ccloop "removing package"
    pip_uninstall ccloop

    n=0
    while IFS= read -r proj; do
        [ -d "$proj/.ccloop" ] && { n=$((n + 1)); info "  found run state: $proj/.ccloop"; }
    done < "$PROJECT_LIST"
    [ "$n" -eq 0 ] && info "no .ccloop/ run-state directories found" \
                   || info "$n .ccloop/ run-state director$([ "$n" = 1 ] && echo y || echo ies) left in place (delete by hand if unwanted)"
fi

# ----------------------------------------------------------------------------
# ccteam — MCP + package + per-project coordination state.
# ----------------------------------------------------------------------------
if should_remove ccteam; then
    step ccteam "unregistering MCP + removing package"
    unregister_mcp ccteam
    pip_uninstall ccteam

    step ccteam "purging per-project state"
    purge_project_state ccteam ".ccteam"
fi

# ----------------------------------------------------------------------------
# ccenvmcp — the shared MCP shim. Removed LAST: every other component imports
# it, so pulling it earlier would leave the others broken mid-uninstall.
# ----------------------------------------------------------------------------
if should_remove ccenvmcp; then
    step ccenvmcp "removing shared MCP shim package"
    # Check real on-disk state — that correctly catches --keep-packages and a
    # failed pip uninstall. The one exception is a dry run, where nothing was
    # actually removed: there, assume the in-scope dependents would be gone.
    remaining=""
    for d in ccmemory ccprospect ccinsight ccusage-mcp ccteam; do
        case "$d" in
            ccusage-mcp) comp=ccusage ;;
            *)           comp="$d" ;;
        esac
        if [ "$DRY_RUN" = "1" ] && should_remove "$comp"; then
            continue
        fi
        python3 -m pip show "$d" >/dev/null 2>&1 && remaining="$remaining $d"
    done
    if [ -n "$remaining" ]; then
        warn "still installed and dependent on ccenvmcp:$remaining — leaving the shim in place"
        info "re-run with --only ccenvmcp once those are gone"
    else
        pip_uninstall ccenvmcp
    fi
fi

# ============================================================================
# Global artifacts — only on a full, unscoped uninstall.
# ============================================================================
if is_full_uninstall; then

    # --- ~/.claude/CLAUDE.md: the [CCENV MANAGED] region --------------------
    step "global CLAUDE.md" "removing the [CCENV MANAGED] region"
    if [ -f "$GLOBAL_CLAUDE_MD" ] && grep -q '^# \[CCENV MANAGED\]' "$GLOBAL_CLAUDE_MD"; then
        backup_file "$GLOBAL_CLAUDE_MD"
        act "remove [CCENV MANAGED] region (content outside it is preserved)"
        if [ "$DRY_RUN" != "1" ]; then
            tmp=$(mktemp)
            awk '
                /^# \[CCENV MANAGED\]/   { skip=1; next }
                /^# \[\/CCENV MANAGED\]/ { skip=0; next }
                !skip { print }
            ' "$GLOBAL_CLAUDE_MD" > "$tmp" && mv "$tmp" "$GLOBAL_CLAUDE_MD" \
                || warn "failed to strip [CCENV MANAGED] region"
        fi
        if [ "$DRY_RUN" != "1" ] && [ ! -s "$GLOBAL_CLAUDE_MD" ]; then
            info "$GLOBAL_CLAUDE_MD is now empty — removing it"
            rm -f "$GLOBAL_CLAUDE_MD"
        elif [ -f "$GLOBAL_CLAUDE_MD" ]; then
            info "kept your own sections in $GLOBAL_CLAUDE_MD"
        fi
    else
        info "no [CCENV MANAGED] region found"
    fi

    # --- shell env file: the `# [ccenv]` export blocks ----------------------
    case "${SHELL##*/}" in
        zsh)  CCENV_ENV_FILE="$HOME/.zshenv" ;;
        bash) CCENV_ENV_FILE="$HOME/.bashrc" ;;
        *)    CCENV_ENV_FILE="$HOME/.profile" ;;
    esac
    step env "removing ccenv exports from $CCENV_ENV_FILE"
    if [ -f "$CCENV_ENV_FILE" ] && grep -q '^# \[ccenv\]' "$CCENV_ENV_FILE"; then
        CCENV_REMOVE_PATH="$REMOVE_PATH" CCENV_DRY="$DRY_RUN" \
        python3 - "$CCENV_ENV_FILE" <<'PY'
import os, shutil, sys

def bak(path):
    """Snapshot path, once per run. Called only when about to write."""
    dest = "%s.uninstall-bak.%s" % (path, os.environ["CCENV_STAMP"])
    if not os.path.exists(dest):
        try:
            shutil.copy2(path, dest)
        except OSError:
            print("  WARNING: could not back up %s" % path, file=sys.stderr)

path = sys.argv[1]
remove_path = os.environ["CCENV_REMOVE_PATH"] == "1"
dry = os.environ["CCENV_DRY"] == "1"

with open(path, encoding="utf-8") as fh:
    lines = fh.readlines()

# install.sh writes each block as: blank line, "# [ccenv]", then the block
# body, with no internal blank lines. So a block ends at the first blank line,
# the next marker, or EOF.
out, i, removed, kept = [], 0, 0, 0
while i < len(lines):
    if lines[i].strip() != "# [ccenv]":
        out.append(lines[i]); i += 1; continue

    block, j = [lines[i]], i + 1
    while j < len(lines) and lines[j].strip() != "" and lines[j].strip() != "# [ccenv]":
        block.append(lines[j]); j += 1

    body = "".join(block)
    is_path_guard = 'case ":$PATH:"' in body
    if is_path_guard and not remove_path:
        out.extend(block); kept += 1
    else:
        removed += 1
        # Drop the blank separator install.sh wrote before the block.
        while out and out[-1].strip() == "":
            out.pop()
        print("  %sremoved block: %s" % (
            "[dry-run] " if dry else "",
            " | ".join(l.strip() for l in block[1:]) or "(empty)"))
    i = j

if kept:
    print("  kept the ~/.local/bin PATH guard (other --user tools depend on it; "
          "use --remove-path to drop it)")
if not removed:
    print("  no removable ccenv blocks found")
elif not dry:
    bak(path)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(out)
    os.replace(tmp, path)
    print("  rewrote %s" % path)
PY
    else
        info "no ccenv blocks in $CCENV_ENV_FILE"
    fi

    # --- ~/.config/ccenv markers -------------------------------------------
    step "install markers" "removing ~/.config/ccenv"
    rm_path "$HOME/.config/ccenv" "install markers" \
        || info "~/.config/ccenv not present"
fi

# ============================================================================
# Verify
# ============================================================================
echo ""
echo "=== verifying removal ==="

echo ""
echo "console scripts still on PATH:"
left=0
for cmd in ccmemory ccprospect ccinsight ccusage-mcp ccusage-statusline ccloop ccteam ccteam-mcp; do
    resolved=$(command -v "$cmd" 2>/dev/null)
    if [ -n "$resolved" ]; then
        info "PRESENT  $cmd -> $resolved"
        left=$((left + 1))
    elif [ -x "$USER_BIN/$cmd" ]; then
        info "PRESENT  $cmd -> $USER_BIN/$cmd"
        left=$((left + 1))
    fi
done
[ "$left" -eq 0 ] && info "(none)"

echo ""
echo "pip distributions still installed:"
found=0
for d in ccenvmcp ccmemory ccprospect ccinsight ccloop ccteam ccusage-mcp; do
    if python3 -m pip show "$d" >/dev/null 2>&1; then
        info "PRESENT  $d"
        found=$((found + 1))
    fi
done
[ "$found" -eq 0 ] && info "(none)"

echo ""
echo "ccenv hooks still in $SETTINGS_JSON:"
if [ -f "$SETTINGS_JSON" ]; then
    grep -oE '"command": "[^"]*(ccmemory|ccprospect|ccinsight|ccloop|ccteam|awareness_hooks|check_sync_status)[^"]*"' \
        "$SETTINGS_JSON" 2>/dev/null | sed 's/^/  /' || info "(none)"
else
    info "(no settings.json)"
fi

if [ "$HAS_CLAUDE" = "1" ] && [ "$DRY_RUN" != "1" ]; then
    echo ""
    echo "Registered MCP servers (claude mcp list):"
    claude mcp list 2>&1 | sed 's/^/  /' || true
fi

echo ""
if [ "$DRY_RUN" = "1" ]; then
    echo "=== dry run complete — nothing was modified ==="
elif [ "$FAILURES" -gt 0 ]; then
    echo "=== ccenv uninstall finished with $FAILURES warning(s) — review the output above ==="
else
    echo "=== ccenv uninstall complete ==="
fi
echo "Backups of every rewritten file: <file>.uninstall-bak.$STAMP"
if [ -d "$STATE_ARCHIVE_DIR" ]; then
    echo "Archived project state dirs: $STATE_ARCHIVE_DIR/"
    ls "$STATE_ARCHIVE_DIR" 2>/dev/null | sed 's/^/  /'
fi
echo "Restart Claude Code to pick up the changes."
