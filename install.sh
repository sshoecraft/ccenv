#!/bin/bash
# ccenv — install the Claude Code env/harness + overlay system
#
# Core components (installed in this order):
#   ccproject   — three-layer project awareness skill + global CLAUDE.md snippet
#   ccmemory    — persistent memory MCP server + hooks  (MCP name: ccmemory)
#   ccusage     — context/rate-limit usage MCP + statusline  (MCP name: ccusage)
#   ccloop     — relay-loop wrapper that hands work between sessions
#   ccteam      — multi-instance coordination via NATS (MCP name: ccteam)
#
# Overlay system — additional MCP servers and CLAUDE.md fragments are picked up
# from these directories (lowest precedence first):
#
#   /usr/local/ccenv        — system-wide overlay
#   ~/.config/ccenv         — per-user overlay
#   <this script's dir>     — bundled (here)
#
# In any overlay dir, the installer looks for:
#   - CLAUDE.md             → appended to ~/.claude/CLAUDE.md inside a marker
#                             block (re-runs replace the block in place)
#   - <subdir>/pyproject.toml → pip-installed (--user) and registered as an
#                             MCP server (user scope). MCP name defaults to
#                             the subdir name; override via .ccenv-mcp.json:
#                                 {"name": "myname", "command": "bin",
#                                  "args": ["arg1"], "scope": "user"}
#
# Usage:
#   ./install.sh                       # install everything (core + overlays)
#   ./install.sh --skip ccteam    # skip a core component (repeatable)
#   ./install.sh --only ccmemory       # install only listed core components
#   ./install.sh --no-overlays         # skip overlay scanning
#   ./install.sh -h                    # show this help
#
# Idempotent — re-running is safe. Components with their own installers
# (ccproject/install.sh, ccusage/install.py) are delegated to.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKIP=()
ONLY=()
DO_OVERLAYS=1

# Dirs scanned for MCP server subdirs (anywhere a subdir with pyproject.toml lives).
MCP_OVERLAY_DIRS=(
    "/usr/local/ccenv"
    "$HOME/.config/ccenv"
    "$SCRIPT_DIR"
)

# Dirs whose CLAUDE.md is merged into ~/.claude/CLAUDE.md as an override.
# Intentionally excludes $SCRIPT_DIR — the bundled CLAUDE.md is the *project*
# doc for working on ccenv itself, not a system-wide directive.
CLAUDE_MD_OVERLAY_DIRS=(
    "/usr/local/ccenv"
    "$HOME/.config/ccenv"
)

# Core subdirs in $SCRIPT_DIR — skipped during overlay scan of the script dir
# (they're already handled explicitly above).
CORE_SUBDIRS=(ccloop ccmemory ccusage ccproject ccteam)

while [ $# -gt 0 ]; do
    case "$1" in
        --skip) SKIP+=("$2"); shift 2 ;;
        --only) ONLY+=("$2"); shift 2 ;;
        --no-overlays) DO_OVERLAYS=0; shift ;;
        -h|--help) sed -n '2,33p' "$0"; exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 1 ;;
    esac
done

should_install() {
    local name="$1"
    if [ ${#ONLY[@]} -gt 0 ]; then
        for x in "${ONLY[@]}"; do [ "$x" = "$name" ] && return 0; done
        return 1
    fi
    for x in "${SKIP[@]}"; do [ "$x" = "$name" ] && return 1; done
    return 0
}

is_core_subdir() {
    local name="$1"
    for c in "${CORE_SUBDIRS[@]}"; do
        [ "$c" = "$name" ] && return 0
    done
    return 1
}

step() { echo ""; echo "=== [$1] $2 ==="; }
info() { echo "  $*"; }
warn() { echo "  WARNING: $*" >&2; }

# Resolve a bare command name to an absolute path.
#
# `command -v` only finds things already on PATH, which fails on macOS with
# Homebrew Python: `pip3 install --user` writes scripts to
# `~/Library/Python/<ver>/bin/` (NOT `~/.local/bin/`), and that path is rarely
# on a default user PATH. We compute pip's actual user-bin directory via
# Python (`site.USER_BASE/bin`) once at startup as $USER_BIN, fall back to
# checking there, and only register a bare name when both fail.
resolve_cmd() {
    local cmd="$1"
    local resolved
    resolved=$(command -v "$cmd" 2>/dev/null)
    if [ -n "$resolved" ]; then
        echo "$resolved"
        return
    fi
    if [ -n "$USER_BIN" ] && [ -x "$USER_BIN/$cmd" ]; then
        echo "$USER_BIN/$cmd"
        return
    fi
    warn "command '$cmd' not on PATH and not found in $USER_BIN — registering bare name"
    echo "$cmd"
}

# Check whether an MCP server is already registered (any scope).
mcp_registered() {
    local name="$1"
    claude mcp get "$name" >/dev/null 2>&1
}

# Return the registered "command + args" line for an MCP server, normalized
# so it can be string-compared against the same shape we'd register now.
# `claude mcp get` prints Command: and Args: on separate lines; we glue them
# back together (skipping a blank Args line) to recover the original cmdline.
mcp_current_command() {
    local name="$1"
    claude mcp get "$name" 2>/dev/null | awk -F': *' '
        tolower($1) ~ /^[[:space:]]*command/ { c = $2 }
        tolower($1) ~ /^[[:space:]]*args/    { a = $2 }
        END {
            if (a == "") print c
            else         print c " " a
        }
    '
}

# Register an MCP server at user scope. If it is already registered with a
# command that DIFFERS from what we want, heal the registration by removing
# and re-adding it — otherwise stale bare-name entries stick forever and the
# server never connects.
# Args: name command [args...]
register_mcp() {
    local name="$1"; shift
    if [ "$HAS_CLAUDE" != "1" ]; then
        warn "skipping MCP register for '$name' (claude CLI not on PATH)"
        return
    fi
    local want="$*"
    if mcp_registered "$name"; then
        local have
        have=$(mcp_current_command "$name")
        if [ -n "$have" ] && [ "$have" = "$want" ]; then
            info "MCP $name already registered (command up to date)"
            return
        fi
        info "MCP $name registered with stale command — re-registering"
        info "  was:  ${have:-<unknown>}"
        info "  now:  $want"
        claude mcp remove -s user "$name" >/dev/null 2>&1 || true
    fi
    if claude mcp add -s user "$name" "$@"; then
        info "registered MCP $name"
    else
        warn "failed to register MCP $name — run manually: claude mcp add -s user $name $*"
    fi
}

# ----------------------------------------------------------------------------
# Prerequisites
# ----------------------------------------------------------------------------
echo "=== ccenv installer ==="
command -v python3 >/dev/null || { echo "ERROR: python3 required"; exit 1; }
command -v pip3    >/dev/null || { echo "ERROR: pip3 required"; exit 1; }
info "python3: $(python3 --version 2>&1)"
info "pip3:    $(pip3 --version 2>&1 | awk '{print $1, $2}')"

# Discover where `pip3 install --user` actually puts console scripts.
# On Linux this is typically ~/.local/bin; on macOS with Homebrew Python it
# is ~/Library/Python/<ver>/bin/. Without this, MCP registrations register
# a bare command name that Claude Code cannot resolve at runtime, and the
# verify step at the bottom misreports installed commands as missing.
USER_BIN=$(python3 -c 'import site, os; print(os.path.join(site.USER_BASE, "bin"))' 2>/dev/null || echo "")
info "user-bin: ${USER_BIN:-<unknown>}"

# Snapshot the user's real PATH before we augment it, so the verify step
# at the bottom can tell the user accurately whether THEIR shell sees the
# bin dir — not the temporarily-augmented one this script is running in.
ORIGINAL_PATH="$PATH"

# Augment PATH for the duration of this script so `command -v` finds the
# scripts we just installed. We do NOT modify the user's shell rc — that is
# their decision. We warn at the end if $USER_BIN is not on their PATH.
if [ -n "$USER_BIN" ]; then
    case ":$PATH:" in
        *":$USER_BIN:"*) ;;
        *) export PATH="$USER_BIN:$PATH" ;;
    esac
fi

HAS_CLAUDE=0
if command -v claude >/dev/null; then
    HAS_CLAUDE=1
    info "claude:  $(command -v claude)"
else
    warn "'claude' CLI not found on PATH — MCP registrations will be skipped"
fi

# ----------------------------------------------------------------------------
# Core: ccproject (its own installer handles the awareness skill + snippet)
# ----------------------------------------------------------------------------
if should_install ccproject; then
    step ccproject "running ccproject/install.sh"
    # The top-level installer owns ~/.claude/CLAUDE.md assembly (bundled +
    # awareness snippet + overlays) so ccproject's per-component CLAUDE.md
    # merge would just be redundant work that we'd overwrite below.
    CCPROJECT_SKIP_GLOBAL_CLAUDE_MD=1 bash "$SCRIPT_DIR/ccproject/install.sh"
fi

# ----------------------------------------------------------------------------
# Core: ccmemory — MCP name: ccmemory
# ----------------------------------------------------------------------------
if should_install ccmemory; then
    step ccmemory "pip install --user + register MCP 'ccmemory'"
    pip3 install --user "$SCRIPT_DIR/ccmemory"
    register_mcp ccmemory "$(resolve_cmd ccmemory)" mcp
fi

# ----------------------------------------------------------------------------
# Core: ccusage — its own installer (UID-aware); MCP name: ccusage
# ----------------------------------------------------------------------------
if should_install ccusage; then
    step ccusage "running ccusage/install.py"
    (cd "$SCRIPT_DIR/ccusage" && python3 install.py)
fi

# ----------------------------------------------------------------------------
# Core: ccloop — pip install only (hooks auto-install on first run)
# ----------------------------------------------------------------------------
if should_install ccloop; then
    step ccloop "pip install --user"
    pip3 install --user "$SCRIPT_DIR/ccloop"
fi

# ----------------------------------------------------------------------------
# Core: ccteam package — MCP name: ccteam (standardized)
# ----------------------------------------------------------------------------
if should_install ccteam; then
    step ccteam "pip install --user + register MCP 'ccteam' + SessionStart hook"
    pip3 install --user "$SCRIPT_DIR/ccteam"
    register_mcp ccteam "$(resolve_cmd ccteam-mcp)"

    # Register a SessionStart hook so users see a notice in the conversation
    # if NATS is unreachable in a ccteam-bootstrapped project (.ccteam/ present).
    # Idempotent — re-running does not duplicate the entry.
    python3 - <<'PY'
import json, os
from pathlib import Path

settings_path = Path.home() / ".claude" / "settings.json"
settings_path.parent.mkdir(parents=True, exist_ok=True)

if settings_path.exists():
    try:
        data = json.loads(settings_path.read_text() or "{}")
    except json.JSONDecodeError:
        data = {}
else:
    data = {}

hooks = data.setdefault("hooks", {})
session_start = hooks.setdefault("SessionStart", [])

entry = {"hooks": [{"type": "command", "command": "ccteam session-start"}]}

# Check whether our command is already wired up anywhere in SessionStart.
def has_cmd(items):
    for item in items:
        for h in item.get("hooks", []):
            if h.get("type") == "command" and h.get("command") == "ccteam session-start":
                return True
    return False

if not has_cmd(session_start):
    session_start.append(entry)
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    print("  registered SessionStart hook 'ccteam session-start' in ~/.claude/settings.json")
else:
    print("  SessionStart hook 'ccteam session-start' already registered")
PY

    warn "ccteam needs a running NATS server at runtime (set CCTEAM_NATS_URL)."
fi

# ----------------------------------------------------------------------------
# Overlay scan: additional MCP subdirs
# ----------------------------------------------------------------------------
GLOBAL_CLAUDE_MD="$HOME/.claude/CLAUDE.md"

install_overlay_mcp_subdir() {
    local subdir="$1"
    local name; name=$(basename "$subdir")
    [ -f "$subdir/pyproject.toml" ] || return 0

    info "overlay MCP package: $name ($subdir)"
    pip3 install --user "$subdir"

    # Sidecar config (optional): .ccenv-mcp.json overrides defaults.
    local mcp_name="$name"
    local mcp_command="$name"
    local mcp_args_json="[]"
    local sidecar="$subdir/.ccenv-mcp.json"
    if [ -f "$sidecar" ]; then
        mcp_name=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('name','$name'))" "$sidecar")
        mcp_command=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('command','$name'))" "$sidecar")
        mcp_args_json=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps(d.get('args',[])))" "$sidecar")
    fi

    # Resolve command to absolute path for robust execution regardless of
    # how Claude Code is launched (desktop launcher, headless cron, etc.).
    # Skip resolution for commands containing slashes — those are already
    # explicit paths.
    case "$mcp_command" in
        */*) ;;  # already a path
        *)   mcp_command=$(resolve_cmd "$mcp_command") ;;
    esac

    # Convert JSON args array to positional shell args
    local mcp_args=()
    if [ "$mcp_args_json" != "[]" ]; then
        # shellcheck disable=SC2207
        IFS=$'\n' read -d '' -r -a mcp_args < <(
            python3 -c "import json,sys; [print(a) for a in json.loads(sys.argv[1])]" "$mcp_args_json"
            printf '\0'
        )
    fi

    register_mcp "$mcp_name" "$mcp_command" "${mcp_args[@]}"
}

scan_mcp_overlay() {
    local overlay="$1"
    [ -d "$overlay" ] || return 0

    step "overlay" "scanning $overlay for MCP subdirs"
    for subdir in "$overlay"/*/; do
        [ -d "$subdir" ] || continue
        local name; name=$(basename "$subdir")
        # Skip core subdirs when scanning the script's own dir
        if [ "$overlay" = "$SCRIPT_DIR" ] && is_core_subdir "$name"; then
            continue
        fi
        install_overlay_mcp_subdir "$subdir"
    done
}

if [ "$DO_OVERLAYS" = "1" ]; then
    # MCP discovery across all overlay dirs (script dir included)
    for d in "${MCP_OVERLAY_DIRS[@]}"; do
        scan_mcp_overlay "$d"
    done
fi

# ----------------------------------------------------------------------------
# Assemble ~/.claude/CLAUDE.md
#
# The expected content is built fresh in /tmp from:
#   1. The bundled CLAUDE.md (this repo's strict global rules — base)
#   2. The [AWARENESS PROTOCOL] block from ccproject's snippet
#   3. [CCENV OVERLAY: <dir>] blocks for each existing system/user overlay
#
# Then compared to ~/.claude/CLAUDE.md:
#   - identical            -> delete the /tmp file, leave the existing one
#   - different (or absent)-> rename existing to ~/.claude/CLAUDE.md.YYYYMMDDHHMMSS
#                             (announced to the user), then install the new one
#
# This guarantees the bundled rules ALWAYS land on every machine where ccenv
# is installed. We never silently merge into an existing file the user may
# have edited — we back it up so they can diff and reconcile by hand.
# ----------------------------------------------------------------------------
step "global CLAUDE.md" "assembling ~/.claude/CLAUDE.md"
TMP_CLAUDE_MD=$(mktemp)
# 1. base: bundled CLAUDE.md
cat "$SCRIPT_DIR/CLAUDE.md" > "$TMP_CLAUDE_MD"
# 2. ccproject awareness snippet (if present)
if [ -f "$SCRIPT_DIR/ccproject/global-claude-md-snippet.md" ]; then
    printf '\n' >> "$TMP_CLAUDE_MD"
    cat "$SCRIPT_DIR/ccproject/global-claude-md-snippet.md" >> "$TMP_CLAUDE_MD"
fi
# 3. overlay CLAUDE.md blocks (only when overlays are enabled)
if [ "$DO_OVERLAYS" = "1" ]; then
    for d in "${CLAUDE_MD_OVERLAY_DIRS[@]}"; do
        [ -f "$d/CLAUDE.md" ] || continue
        {
            printf '\n'
            printf '# [CCENV OVERLAY: %s]\n' "$d"
            cat "$d/CLAUDE.md"
            printf '# [/CCENV OVERLAY: %s]\n' "$d"
        } >> "$TMP_CLAUDE_MD"
    done
fi

mkdir -p "$HOME/.claude"
if [ -f "$GLOBAL_CLAUDE_MD" ] && cmp -s "$TMP_CLAUDE_MD" "$GLOBAL_CLAUDE_MD"; then
    rm -f "$TMP_CLAUDE_MD"
    info "$GLOBAL_CLAUDE_MD is already up to date"
else
    if [ -f "$GLOBAL_CLAUDE_MD" ]; then
        TS=$(date +%Y%m%d%H%M%S)
        BACKUP="$GLOBAL_CLAUDE_MD.$TS"
        mv "$GLOBAL_CLAUDE_MD" "$BACKUP"
        echo "  $GLOBAL_CLAUDE_MD renamed to $BACKUP"
    fi
    mv "$TMP_CLAUDE_MD" "$GLOBAL_CLAUDE_MD"
    info "installed new $GLOBAL_CLAUDE_MD"
fi

# ----------------------------------------------------------------------------
# Verify
# ----------------------------------------------------------------------------
echo ""
echo "=== verifying installed commands ==="
# We auto-augmented PATH with $USER_BIN above so `command -v` works here too.
# Report the bin dir we computed, and warn if the *user's* permanent PATH
# does not include it — we don't edit their shell rc, so MCP launches by
# Claude Code will still work (we registered absolute paths) but the user
# won't see the commands in an interactive shell until they fix their rc.
if [ -n "$USER_BIN" ]; then
    info "user-bin: $USER_BIN"
    USER_BIN_ON_REAL_PATH=0
    OLD_IFS="$IFS"; IFS=":"
    for d in $ORIGINAL_PATH; do
        [ "$d" = "$USER_BIN" ] && USER_BIN_ON_REAL_PATH=1 && break
    done
    IFS="$OLD_IFS"
    if [ "$USER_BIN_ON_REAL_PATH" = "1" ]; then
        info "$USER_BIN is on your PATH"
    else
        warn "$USER_BIN is NOT on your shell PATH"
        warn "  Add to your shell rc:  export PATH=\"$USER_BIN:\$PATH\""
        warn "  (MCP servers still work because we registered absolute paths;"
        warn "   this only affects interactive shell command lookup.)"
    fi
fi

for cmd in ccmemory ccusage-mcp ccusage-statusline ccloop ccteam ccteam-mcp; do
    resolved=$(command -v "$cmd" 2>/dev/null)
    if [ -n "$resolved" ]; then
        info "OK       $cmd -> $resolved"
    elif [ -n "$USER_BIN" ] && [ -x "$USER_BIN/$cmd" ]; then
        info "OK       $cmd -> $USER_BIN/$cmd"
    else
        info "MISSING  $cmd"
    fi
done

if [ "$HAS_CLAUDE" = "1" ]; then
    echo ""
    echo "Registered MCP servers (claude mcp list):"
    claude mcp list 2>&1 | sed 's/^/  /' || true
fi

echo ""
echo "=== ccenv install complete ==="
