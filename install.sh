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

# Resolve a bare command name to an absolute path via PATH lookup.
# Falls back to the bare name if not currently on PATH (Claude Code will
# do its own PATH lookup at MCP launch time in that case).
resolve_cmd() {
    local cmd="$1"
    local resolved
    resolved=$(command -v "$cmd" 2>/dev/null)
    if [ -n "$resolved" ]; then
        echo "$resolved"
    else
        warn "command '$cmd' not on PATH at install time — registering bare name"
        echo "$cmd"
    fi
}

# Check whether an MCP server is already registered (any scope).
mcp_registered() {
    local name="$1"
    claude mcp get "$name" >/dev/null 2>&1
}

# Register an MCP server at user scope. Skips if already registered.
# Args: name command [args...]
register_mcp() {
    local name="$1"; shift
    if [ "$HAS_CLAUDE" != "1" ]; then
        warn "skipping MCP register for '$name' (claude CLI not on PATH)"
        return
    fi
    if mcp_registered "$name"; then
        info "MCP $name already registered"
        return
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
    bash "$SCRIPT_DIR/ccproject/install.sh"
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
# Overlay scan: CLAUDE.md merge + additional MCP subdirs
# ----------------------------------------------------------------------------
GLOBAL_CLAUDE_MD="$HOME/.claude/CLAUDE.md"

# Strip ALL existing CCENV OVERLAY blocks (self-healing — removes stale blocks
# for overlay dirs that no longer exist or are no longer eligible to merge).
strip_all_overlay_blocks() {
    [ -f "$GLOBAL_CLAUDE_MD" ] || return 0
    grep -qE '^# \[CCENV OVERLAY:' "$GLOBAL_CLAUDE_MD" || return 0
    local tmp; tmp=$(mktemp)
    awk '
        /^# \[CCENV OVERLAY:/      { skip=1; next }
        skip && /^# \[\/CCENV OVERLAY:/ { skip=0; next }
        !skip { print }
    ' "$GLOBAL_CLAUDE_MD" > "$tmp"
    # Collapse any trailing blank lines left behind
    sed -i -e :a -e '/^$/{$d;N;ba' -e '}' "$tmp" 2>/dev/null || true
    mv "$tmp" "$GLOBAL_CLAUDE_MD"
    info "stripped existing CCENV OVERLAY blocks from $GLOBAL_CLAUDE_MD"
}

merge_overlay_claude_md() {
    local overlay="$1"
    local src="$overlay/CLAUDE.md"
    [ -f "$src" ] || return 0

    mkdir -p "$HOME/.claude"
    touch "$GLOBAL_CLAUDE_MD"

    {
        echo ""
        echo "# [CCENV OVERLAY: $overlay]"
        cat "$src"
        echo "# [/CCENV OVERLAY: $overlay]"
    } >> "$GLOBAL_CLAUDE_MD"
    info "merged $src into $GLOBAL_CLAUDE_MD"
}

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
    # Always strip stale CLAUDE.md overlay blocks first (self-healing)
    strip_all_overlay_blocks

    # MCP discovery across all overlay dirs (script dir included)
    for d in "${MCP_OVERLAY_DIRS[@]}"; do
        scan_mcp_overlay "$d"
    done

    # CLAUDE.md merge only from system / user overlay dirs
    for d in "${CLAUDE_MD_OVERLAY_DIRS[@]}"; do
        [ -d "$d" ] || continue
        merge_overlay_claude_md "$d"
    done
fi

# ----------------------------------------------------------------------------
# Verify
# ----------------------------------------------------------------------------
echo ""
echo "=== verifying installed commands ==="
USER_BIN="$HOME/.local/bin"
case ":$PATH:" in
    *":$USER_BIN:"*) info "$USER_BIN is on PATH" ;;
    *) warn "$USER_BIN is NOT on PATH — add it to your shell rc" ;;
esac

for cmd in ccmemory ccusage-mcp ccusage-statusline ccloop ccteam ccteam-mcp; do
    if command -v "$cmd" >/dev/null; then
        info "OK       $cmd -> $(command -v $cmd)"
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
