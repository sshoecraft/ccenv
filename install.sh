#!/bin/bash
# ccenv — install the Claude Code env/harness + overlay system
#
# Core components (installed in this order):
#   ccproject   — three-layer project awareness skill + global CLAUDE.md snippet
#   gitsync     — SessionStart hook that warns when the repo is out of sync with GitHub
#   ccenvmcp    — stdlib-only MCP shim (foundation; lets the MCP servers run on Python 3.9)
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
GLOBAL_CLAUDE_MD="$HOME/.claude/CLAUDE.md"
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
CORE_SUBDIRS=(ccenvmcp ccloop ccmemory ccusage ccproject ccteam)

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
# `command -v` only finds things on PATH. We temporarily add $USER_BIN
# (~/.local/bin, pinned via PYTHONUSERBASE) to PATH for the duration of
# this script, so this normally succeeds. The fallback to $USER_BIN/$cmd
# covers the edge case where a binary was installed but is not on PATH
# (e.g. another shell with a stale PATH), and registering a bare name is
# the last resort.
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

# PEP 668 (Debian 12+, Ubuntu 23.04+, Fedora 38+, Arch, Homebrew Python): these
# ship an EXTERNALLY-MANAGED marker beside the stdlib that makes pip refuse to
# install — INCLUDING `pip install --user` — with
# "error: externally-managed-environment", which trips this script's `set -e`
# before anything installs. Overriding it is safe HERE: ccenv installs
# exclusively with --user into ~/.local and never touches the system
# site-packages the marker protects. We do NOT fall back to pipx (the usual
# PEP 668 answer) because all five components share ONE --user site so the
# ccenvmcp shim is importable across them — pipx's per-app venvs would break
# that. Enable the override only when the marker is actually present AND this
# pip knows the flag (added in the same pip 23.x that enforces PEP 668), so an
# older pip is never handed an option it doesn't understand. Exported here so
# EVERY `pip install --user` below inherits it (component installs, the PEP 621
# toolchain upgrade, native-ext force-reinstalls) with no per-call edits.
EXTERNALLY_MANAGED=$(python3 -c 'import os, sysconfig; print(os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED"))' 2>/dev/null || true)
if [ -n "$EXTERNALLY_MANAGED" ] && [ -f "$EXTERNALLY_MANAGED" ] \
   && python3 -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
    export PIP_BREAK_SYSTEM_PACKAGES=1
    info "PEP 668 externally-managed environment — enabling --break-system-packages for --user installs"
fi

# Force pip's `--user` installs to land under ~/.local on every platform.
#
# Without this, pip obeys Python's `sysconfig` user scheme, which on macOS
# with Homebrew Python picks `osx_framework_user` and writes scripts to
# `~/Library/Python/<ver>/bin/` — a path that isn't on default macOS PATH,
# splits the install between Mac and Linux, and breaks every time the user
# upgrades Python (3.14/bin/ -> 3.15/bin/ -> ...). PYTHONUSERBASE overrides
# the scheme and pins `--user` scripts to $PYTHONUSERBASE/bin, so on both
# OSes ccenv binaries end up in ~/.local/bin — which is already on the
# user's PATH (every component creates it as a side effect of `--user` use
# and most distros put it on PATH by default).
#
# This also redirects user-site packages to ~/.local/lib/pythonX.Y/site-packages/
# instead of the platform default, but only commands that run with this env
# variable see them — which is exactly the components we install here.
# Capture whether the user's shell environment already has PYTHONUSERBASE set
# to what we want, BEFORE we override it for this script's subshell. We use
# this at the end to decide whether to print the copy-paste rc snippet.
ORIGINAL_PYTHONUSERBASE="${PYTHONUSERBASE:-}"

# Detect the PLATFORM DEFAULT user-base (what Python's site.py picks when
# PYTHONUSERBASE is unset). On Linux this is already $HOME/.local — so the
# PYTHONUSERBASE override below is a no-op there, and we won't tell the user
# to set anything. On macOS with Homebrew Python it's ~/Library/Python/<ver>,
# which is where our override actually matters and where the runtime needs
# the env var to find packages.
PLATFORM_USER_BASE=$(env -u PYTHONUSERBASE python3 -c 'import site; print(site.USER_BASE)' 2>/dev/null)
PLATFORM_DEFAULTS_TO_LOCAL=0
if [ "$PLATFORM_USER_BASE" = "$HOME/.local" ]; then
    PLATFORM_DEFAULTS_TO_LOCAL=1
fi

export PYTHONUSERBASE="$HOME/.local"
USER_BIN="$PYTHONUSERBASE/bin"
info "user-base: $PYTHONUSERBASE  (PYTHONUSERBASE forced for consistency)"

# Snapshot the user's real PATH before we augment it, so the verify step
# at the bottom can tell the user accurately whether THEIR shell sees the
# bin dir — not the temporarily-augmented one this script is running in.
ORIGINAL_PATH="$PATH"

# Augment PATH for the duration of this script so `command -v` finds the
# scripts we just installed. We do NOT modify the user's shell rc — that is
# their decision. We warn at the end if $USER_BIN is not on their PATH.
case ":$PATH:" in
    *":$USER_BIN:"*) ;;
    *) export PATH="$USER_BIN:$PATH" ;;
esac

HAS_CLAUDE=0
if command -v claude >/dev/null; then
    HAS_CLAUDE=1
    CLAUDE_PATH=$(command -v claude)
    info "claude:  $CLAUDE_PATH"
    # If claude lives outside the user's home (typically /usr/local/bin or
    # /opt/homebrew/bin), it was installed system-wide — we can't write to
    # that prefix as a regular user, so ccenv components still install into
    # $HOME/.local. Surface the asymmetry so the user isn't surprised that
    # `claude` and `ccloop` live in different directories.
    case "$CLAUDE_PATH" in
        "$HOME"/*) ;;
        *) warn "claude is installed system-wide ($CLAUDE_PATH);"
           warn "  ccenv components will install to \$HOME/.local (user scope)." ;;
    esac
else
    warn "'claude' CLI not found on PATH — MCP registrations will be skipped"
fi

# ----------------------------------------------------------------------------
# pip_install_local SRC_DIR -- install a local pip package from a CLEAN copy.
#
# This repo may live on a filesystem that cannot store macOS extended
# attributes natively (e.g. some bind mounts / network volumes). On such a
# volume the OS materializes AppleDouble "._*" sidecar files next to anything
# written here, INCLUDING the "<name>.dist-info" directory setuptools emits
# during a wheel build. bdist_wheel then zips both "<name>.dist-info" and the
# junk "._<name>.dist-info" into the wheel, and pip rejects it with
# "multiple .dist-info directories found". COPYFILE_DISABLE does not help —
# that only governs tar/cp, not the FS-level sidecar creation.
#
# Fix: stage the source to /tmp (native APFS, no sidecars), stripping all
# build cruft and "._*" files, and build/install from there. The wheel then
# contains exactly one dist-info and installs cleanly.
pip_install_local() {
    local src="$1"
    local name; name=$(basename "$src")
    local stage; stage=$(mktemp -d "/tmp/ccenv-${name}.XXXXXX")

    # Copy source minus build cruft and AppleDouble sidecars. rsync is present
    # on macOS by default; fall back to cp + prune if it is ever missing.
    if command -v rsync >/dev/null 2>&1; then
        rsync -a \
            --exclude='._*' --exclude='build' --exclude='dist' \
            --exclude='*.egg-info' --exclude='__pycache__' \
            "$src"/ "$stage"/
    else
        cp -R "$src"/ "$stage"/
        find "$stage" \( -name '._*' -o -name '__pycache__' \
            -o -name '*.egg-info' -o -name 'build' -o -name 'dist' \) \
            -prune -exec rm -rf {} + 2>/dev/null || true
    fi

    # Use `python3 -m pip` (not the `pip3` script) so an upgraded user-site pip
    # — see ensure_build_toolchain — is actually used: ~/.local is ahead of the
    # system dist-packages on sys.path, but the `pip3` entry-point script stays
    # pinned to the old system pip.
    COPYFILE_DISABLE=1 python3 -m pip install --user "$stage"
    local rc=$?
    rm -rf "$stage"
    return $rc
}

# ----------------------------------------------------------------------------
# ensure_build_toolchain -- guarantee a PEP 621-capable build toolchain.
#
# Old distro toolchains (Debian 11 / Raspberry Pi OS: pip 20.3.4 + setuptools
# 52, RHEL/Alma/Rocky 9 similar) predate PEP 621: they cannot parse a
# [project] table, so each ccenv package builds as "UNKNOWN" and then trips
# Debian's `install_layout` wheel-build bug. Every package in this repo
# (ccenvmcp included) uses [project], so without a modern setuptools the very
# first pip build fails — which is exactly the failure seen on `solardirector`.
#
# Fix: upgrade pip/setuptools/wheel into the --user site (~/.local) only when
# the current setuptools is too old (major < 61). This never touches the
# system interpreter that the distro's own apt/dnf tooling depends on, and is
# a no-op on machines that already have a recent setuptools.
# ----------------------------------------------------------------------------
ensure_build_toolchain() {
    local sv
    sv=$(python3 -c 'import setuptools,sys; sys.stdout.write(setuptools.__version__.split(".")[0])' 2>/dev/null || echo 0)
    if [ "${sv:-0}" -ge 61 ] 2>/dev/null; then
        info "build toolchain: setuptools $(python3 -c 'import setuptools;print(setuptools.__version__)' 2>/dev/null) (PEP 621 ok)"
        return 0
    fi
    info "build toolchain: setuptools ${sv:-?} is too old for PEP 621 — upgrading pip/setuptools/wheel into $PYTHONUSERBASE"
    python3 -m pip install --user --upgrade pip setuptools wheel 2>&1 | sed 's/^/  /' \
        || warn "toolchain upgrade failed — pip builds may still fail on this box"
}

# ----------------------------------------------------------------------------
# heal_stale_compiled_exts -- force-reinstall native deps stranded by a Python
# version bump.
#
# Why this is needed: with PYTHONUSERBASE set, Homebrew's osx_framework_user
# scheme collapses the --user site to a SINGLE version-agnostic directory,
# $PYTHONUSERBASE/lib/python/site-packages — shared verbatim by python3.13,
# python3.14, ... (verify: `python3 -c 'import site;print(site.getusersitepackages())'`
# returns the same path for every minor version). Pure-Python packages survive
# a Python upgrade there, but COMPILED extensions are ABI-tagged
# (e.g. watchfiles' _rust_notify.cpython-314-darwin.so) and only load under the
# matching interpreter. After a Python bump the old tagged .so lingers, the new
# interpreter cannot import it, and pip — seeing the distribution already
# "present" in the shared dir — never refetches the right-ABI wheel. The
# observed symptom was ccteam's MCP failing to connect with
# `ModuleNotFoundError: No module named 'watchfiles._rust_notify'`.
#
# Fix: after everything is installed, walk the shared user-site for native
# extension files whose ABI tag does not match the running interpreter, map each
# stale file back to its owning pip distribution (via that dist's RECORD), and
# `pip install --force-reinstall --no-deps` it so the correct-ABI wheel lands.
# Generic by construction — it heals ANY compiled dep (today only watchfiles via
# ccteam), self-heals an already-broken box, and is a near-instant no-op when
# every extension already matches.
# ----------------------------------------------------------------------------
heal_stale_compiled_exts() {
    step "native deps" "checking for stale-ABI compiled extensions in the shared user-site"

    local site
    site=$(python3 -c 'import site; print(site.getusersitepackages())' 2>/dev/null)
    if [ -z "$site" ] || [ ! -d "$site" ]; then
        info "no user-site directory yet — nothing to check"
        return 0
    fi

    local cur_ext
    cur_ext=$(python3 -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX") or "")' 2>/dev/null)
    info "current interpreter ABI: ${cur_ext:-<unknown>}  ($(python3 --version 2>&1))"

    # Detect a Python bump since the last install for an informative message.
    # The actual fix below is keyed off the on-disk .so files, not this marker,
    # so it still heals a fresh checkout (no marker) or a box where the bump
    # predates this feature. The marker is (re)written at the end of install.
    local cur_tag prev_tag
    cur_tag=$(python3 -c 'import sys; print(sys.implementation.cache_tag)' 2>/dev/null)
    PYTHON_TAG_MARKER="$HOME/.config/ccenv/python-tag"
    prev_tag=$(cat "$PYTHON_TAG_MARKER" 2>/dev/null | head -1)
    if [ -n "$prev_tag" ] && [ "$prev_tag" != "$cur_tag" ]; then
        info "Python changed since last install: $prev_tag -> $cur_tag (compiled deps may need rebuilding)"
    fi

    # Emit one distribution name per line for each stale extension found; emit
    # "ORPHAN<TAB><relpath>" on stderr for stale files no dist-info claims.
    local stale_dists
    stale_dists=$(CCENV_SITE="$site" python3 - <<'PY'
import os, re, sys, sysconfig

site = os.environ["CCENV_SITE"]
cur = sysconfig.get_config_var("EXT_SUFFIX") or ""

# A compiled extension is loadable by THIS interpreter only if its filename ends
# in the interpreter's exact EXT_SUFFIX, the stable-ABI ".abi3.so", or carries
# no interpreter tag at all. A different "cpythonNN"/"cpNN"/"pypy" tag is stale.
def is_stale(fn):
    if fn.endswith(".abi3.so"):
        return False
    if not re.search(r"\.(?:cpython-|cp|pypy)\d", fn):
        return False  # untagged or non-CPython-tagged — leave it alone
    return not fn.endswith(cur)

stale = []
for root, dirs, files in os.walk(site):
    for f in files:
        if f.endswith((".so", ".pyd", ".dylib")) and is_stale(f):
            stale.append(os.path.normpath(os.path.relpath(os.path.join(root, f), site)))

if not stale:
    sys.exit(0)

# Map every RECORD-listed path to its owning *.dist-info directory.
owner = {}
for entry in os.listdir(site):
    if not entry.endswith(".dist-info"):
        continue
    record = os.path.join(site, entry, "RECORD")
    try:
        with open(record, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                path = line.split(",", 1)[0].strip()
                if path:
                    owner[os.path.normpath(path)] = entry
    except OSError:
        continue

# Pin the EXACT installed version (name==version) so the reinstall rebuilds the
# right-ABI wheel of the SAME release — never a surprise upgrade of a package
# ccenv does not own (the --user site is shared with the user's own installs).
def name_version(distinfo):
    name = ver = ""
    meta = os.path.join(site, distinfo, "METADATA")
    try:
        with open(meta, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("Name:") and not name:
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("Version:") and not ver:
                    ver = line.split(":", 1)[1].strip()
                if name and ver:
                    break
    except OSError:
        pass
    if not name or not ver:
        base = distinfo[: -len(".dist-info")]
        m = re.match(r"(.+?)-(\d.*)$", base)
        if m:
            name, ver = name or m.group(1), ver or m.group(2)
    return name, ver

seen = set()
for rel in stale:
    di = owner.get(rel)
    if di:
        name, ver = name_version(di)
        if name and ver and name.lower() not in seen:
            seen.add(name.lower())
            print("%s==%s" % (name, ver))
        elif not (name and ver):
            print("ORPHAN\t" + rel, file=sys.stderr)
    else:
        print("ORPHAN\t" + rel, file=sys.stderr)
PY
)

    if [ -z "$stale_dists" ]; then
        info "no stale-ABI compiled extensions found"
        return 0
    fi

    warn "stale-ABI compiled extensions detected (Python changed since they were built) — force-reinstalling the same versions for the current ABI:"
    local d
    while IFS= read -r d; do
        [ -n "$d" ] || continue
        info "  force-reinstall $d"
        python3 -m pip install --user --force-reinstall --no-deps "$d" 2>&1 | sed 's/^/    /' \
            || warn "  force-reinstall of $d failed — its native extension may still be stale"
    done <<EOF
$stale_dists
EOF
}

# ----------------------------------------------------------------------------
# Assemble the BASE ~/.claude/CLAUDE.md — FIRST, before any component runs.
#
# The top-level installer owns ONLY the base (this repo's bundled rules) plus
# any user/system overlay blocks. It writes that into a delimited
# [CCENV MANAGED] region and preserves everything outside it. Component
# installers (ccproject, etc.) own and append their OWN sections afterward
# (e.g. [AWARENESS PROTOCOL]); the top-level never installs a component's
# CLAUDE.md content. Re-runs refresh only the managed region, leaving each
# component's appended section intact.
# ----------------------------------------------------------------------------
assemble_ccenv_base_claude_md() {
    step "global CLAUDE.md" "installing base ~/.claude/CLAUDE.md (components add their own sections)"
    mkdir -p "$HOME/.claude"
    local tmp; tmp=$(mktemp)
    {
        printf '# [CCENV MANAGED]\n'
        printf '# Managed by ccenv install.sh — do not edit between these markers.\n\n'
        cat "$SCRIPT_DIR/CLAUDE.md"
        if [ "$DO_OVERLAYS" = "1" ]; then
            for d in "${CLAUDE_MD_OVERLAY_DIRS[@]}"; do
                [ -f "$d/CLAUDE.md" ] || continue
                printf '\n# [CCENV OVERLAY: %s]\n' "$d"
                cat "$d/CLAUDE.md"
                printf '# [/CCENV OVERLAY: %s]\n' "$d"
            done
        fi
        printf '# [/CCENV MANAGED]\n'
        # Preserve component-owned sections that live outside our markers.
        # (Skipped for a legacy file with no markers — that is backed up and
        # rebuilt, then components re-append their sections.)
        if [ -f "$GLOBAL_CLAUDE_MD" ] && grep -q '^# \[CCENV MANAGED\]' "$GLOBAL_CLAUDE_MD"; then
            awk '
                /^# \[CCENV MANAGED\]/   { skip=1; next }
                /^# \[\/CCENV MANAGED\]/ { skip=0; next }
                !skip { print }
            ' "$GLOBAL_CLAUDE_MD"
        fi
    } > "$tmp"

    if [ -f "$GLOBAL_CLAUDE_MD" ] && cmp -s "$tmp" "$GLOBAL_CLAUDE_MD"; then
        rm -f "$tmp"
        info "$GLOBAL_CLAUDE_MD already up to date"
        return
    fi
    if [ -f "$GLOBAL_CLAUDE_MD" ]; then
        local backup; backup="$GLOBAL_CLAUDE_MD.$(date +%Y%m%d%H%M%S)"
        mv "$GLOBAL_CLAUDE_MD" "$backup"
        echo "  existing $GLOBAL_CLAUDE_MD renamed to $backup"
    fi
    mv "$tmp" "$GLOBAL_CLAUDE_MD"
    info "installed base $GLOBAL_CLAUDE_MD"
}

assemble_ccenv_base_claude_md

# Make sure pip can build PEP 621 packages before we install any of them.
ensure_build_toolchain

# ----------------------------------------------------------------------------
# Foundation: ccenvmcp — the stdlib-only MCP shim that ccmemory, ccusage, and
# ccteam import in place of the official `mcp` SDK (which requires Python
# 3.10+). It MUST be installed before them. It is deliberately NOT a declared
# dependency of those packages (this repo installs from local source, never
# PyPI, so a declared dep would trigger a failing PyPI lookup) — install
# ordering is what guarantees it is importable from the shared --user site.
# ----------------------------------------------------------------------------
if should_install ccmemory || should_install ccusage || should_install ccteam; then
    step ccenvmcp "pip install --user (MCP shim for ccmemory/ccusage/ccteam)"
    pip_install_local "$SCRIPT_DIR/ccenvmcp"
fi

# ----------------------------------------------------------------------------
# Core: ccproject (its own installer owns the awareness skill + its
# [AWARENESS PROTOCOL] section in ~/.claude/CLAUDE.md)
# ----------------------------------------------------------------------------
if should_install ccproject; then
    step ccproject "running ccproject/install.sh"
    bash "$SCRIPT_DIR/ccproject/install.sh"
fi

# ----------------------------------------------------------------------------
# Core: gitsync — global SessionStart hook that warns (in-session) when the
# current repo is out of sync with its GitHub/origin remote. Pure-bash,
# self-gating (silent outside git repos / offline / when in sync), read-only
# (git ls-remote, no fetch). Catches the "another machine pushed and this one
# never pulled" trap.
# ----------------------------------------------------------------------------
if should_install gitsync; then
    step gitsync "installing git-sync SessionStart hook"
    HOOK_DIR="$HOME/.claude/hooks"
    mkdir -p "$HOOK_DIR"
    if command -v install >/dev/null 2>&1; then
        install -m 0755 "$SCRIPT_DIR/check_sync_status.sh" "$HOOK_DIR/check_sync_status.sh"
    else
        cp "$SCRIPT_DIR/check_sync_status.sh" "$HOOK_DIR/check_sync_status.sh"
        chmod +x "$HOOK_DIR/check_sync_status.sh"
    fi
    info "installed $HOOK_DIR/check_sync_status.sh"

    # Register (and heal stale path) the SessionStart hook entry. Idempotent.
    CCENV_SYNC_HOOK_CMD="$HOOK_DIR/check_sync_status.sh" python3 - <<'PY'
import json, os
from pathlib import Path

cmd = os.environ["CCENV_SYNC_HOOK_CMD"]
sp = Path.home() / ".claude" / "settings.json"
sp.parent.mkdir(parents=True, exist_ok=True)

data = {}
if sp.exists():
    try:
        data = json.loads(sp.read_text() or "{}")
    except json.JSONDecodeError:
        data = {}

ss = data.setdefault("hooks", {}).setdefault("SessionStart", [])

def is_ours(c):
    return isinstance(c, str) and c.rstrip().endswith("check_sync_status.sh")

present = changed = False
for item in ss:
    for h in item.get("hooks", []):
        if h.get("type") == "command" and is_ours(h.get("command", "")):
            present = True
            if h["command"] != cmd:
                h["command"] = cmd
                changed = True

if not present:
    ss.append({"hooks": [{"type": "command", "command": cmd}]})
    changed = True

if changed:
    sp.write_text(json.dumps(data, indent=2) + "\n")
    print("  registered/healed SessionStart hook check_sync_status.sh")
else:
    print("  SessionStart hook check_sync_status.sh already registered")
PY

    # Pin the ccenv source path so the hook can ALSO check whether the
    # installed ccenv harness is in sync with its GitHub remote — even when
    # the user is sitting in some other project's working directory.
    # ~/.config/ccenv/source.path is a one-line text file holding the
    # absolute path of the ccenv checkout that ran ./install.sh; the hook
    # uses it as the second target of its git-sync check. Rewritten on every
    # install so it tracks the most recent install location.
    SRC_MARKER="$HOME/.config/ccenv/source.path"
    mkdir -p "$(dirname "$SRC_MARKER")"
    if [ ! -f "$SRC_MARKER" ] || [ "$(cat "$SRC_MARKER" 2>/dev/null)" != "$SCRIPT_DIR" ]; then
        printf '%s\n' "$SCRIPT_DIR" > "$SRC_MARKER"
        info "recorded ccenv source path in $SRC_MARKER"
    else
        info "ccenv source path in $SRC_MARKER is up to date"
    fi
fi

# ----------------------------------------------------------------------------
# Core: ccmemory — MCP name: ccmemory
# ----------------------------------------------------------------------------
if should_install ccmemory; then
    step ccmemory "pip install --user + register MCP 'ccmemory'"
    pip_install_local "$SCRIPT_DIR/ccmemory"
    register_mcp ccmemory "$(resolve_cmd ccmemory)" mcp

    # Install the compile-memories skill. Compaction runs in the interactive
    # session (no claude -p / no metered Agent-SDK credit); the skill carries
    # the procedure and the SessionStart hook nudges when the backlog is high.
    CCMEM_SKILL_DIR="$HOME/.claude/skills/compile-memories"
    mkdir -p "$CCMEM_SKILL_DIR"
    cp "$SCRIPT_DIR/ccmemory/skills/compile-memories/SKILL.md" "$CCMEM_SKILL_DIR/SKILL.md"
    info "installed $CCMEM_SKILL_DIR/SKILL.md"
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
    pip_install_local "$SCRIPT_DIR/ccloop"
    # ccloop registers its own PostToolUse/Stop hooks (the keepgoing /
    # context-guard pair). When the ccloop binary moves — e.g. from a
    # legacy ~/.venvs/ccloop install to ~/.local/bin/ccloop — those
    # registrations stay pinned to the old absolute path until something
    # tells ccloop to re-register. `ccloop install` does exactly that
    # (its registration logic uses a loose `_is_ours` matcher so it
    # rewrites any stale ccloop entry rather than duplicating).
    if command -v ccloop >/dev/null 2>&1; then
        info "refreshing ccloop hook registrations (heals stale paths)"
        ccloop install 2>&1 | sed 's/^/  /' || warn "ccloop install failed (non-fatal)"
    fi
fi

# ----------------------------------------------------------------------------
# Core: ccteam package — MCP name: ccteam (standardized)
# ----------------------------------------------------------------------------
if should_install ccteam; then
    step ccteam "pip install --user + register MCP 'ccteam' + SessionStart hook"
    pip_install_local "$SCRIPT_DIR/ccteam"
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

install_overlay_mcp_subdir() {
    local subdir="$1"
    local name; name=$(basename "$subdir")
    [ -f "$subdir/pyproject.toml" ] || return 0

    info "overlay MCP package: $name ($subdir)"
    pip_install_local "$subdir"

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

# Heal native deps stranded by a Python version bump. Runs AFTER every install:
# components/overlays satisfy their compiled deps from the shared user-site, so
# pip leaves a stale-ABI .so in place on a Python bump — this re-fetches the
# right-ABI wheel. Self-heals an already-broken box even with no version change.
heal_stale_compiled_exts

# NOTE: the base ~/.claude/CLAUDE.md is assembled BEFORE the components run
# (see assemble_ccenv_base_claude_md, invoked above). Each component installer
# owns and appends its own section (e.g. ccproject's [AWARENESS PROTOCOL]); the
# top-level installer never reaches into a component's CLAUDE.md content.

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

# ----------------------------------------------------------------------------
# Shell environment setup — auto-write to the env file sourced for EVERY
# shell invocation, interactive or not.
# ----------------------------------------------------------------------------
# Claude Code spawns hooks, MCP servers, and the statusLine in
# NON-INTERACTIVE shells. The user's interactive rc file (~/.bashrc,
# ~/.zshrc) is NOT sourced in those contexts, so env vars we need have
# to live in the file sourced on every invocation:
#
#   zsh  → ~/.zshenv  (sourced for ALL zsh invocations)
#   bash → ~/.bashrc  (no bash equivalent of zshenv; works for terminal-
#                      launched claude since env inherits to subprocesses)
#
# Per the [pythonuserbase-in-zshenv] memory: Windsurf writes to ~/.zshrc
# (the wrong file), Claude hooks fail with ModuleNotFoundError. We don't
# repeat that mistake.
#
# Idempotent: each ensure_* call checks the file before appending. PATH
# additions use the runtime case-guard idiom so even when the env file is
# sourced repeatedly (nested subshells, fresh terminals over time)
# ~/.local/bin doesn't accumulate in PATH.

case "${SHELL##*/}" in
    zsh)  CCENV_ENV_FILE="$HOME/.zshenv" ;;
    bash) CCENV_ENV_FILE="$HOME/.bashrc" ;;
    *)    CCENV_ENV_FILE="$HOME/.profile" ;;
esac

step env "ensuring exports in $CCENV_ENV_FILE"
[ -f "$CCENV_ENV_FILE" ] || touch "$CCENV_ENV_FILE"

# Ensure VAR=VALUE is exported. If the file already exports VAR (to ANY
# value — we don't override the user's own setting), leave it alone.
ensure_env_var() {
    local var="$1" value="$2"
    if grep -qE "^[[:space:]]*export[[:space:]]+${var}=" "$CCENV_ENV_FILE" 2>/dev/null; then
        info "$var already exported in $CCENV_ENV_FILE — leaving alone"
        return
    fi
    {
        printf '\n# [ccenv]\n'
        printf 'export %s=%s\n' "$var" "$value"
    } >> "$CCENV_ENV_FILE"
    info "appended: export $var=$value"
}

# Ensure $dir is prepended to PATH via a runtime-guarded export. The case
# guard makes the line a no-op when PATH already contains $dir, so nested
# subshells / repeated sourcing don't keep adding duplicate entries. The
# check for presence in the file just looks for the dir literal anywhere —
# that catches the user's existing entries regardless of how they wrote
# the export.
ensure_env_path() {
    local dir="$1"  # e.g. '$HOME/.local/bin'
    local esc
    esc=$(printf '%s' "$dir" | sed -e 's/[][\\.^$*/]/\\&/g')
    if grep -qE "$esc" "$CCENV_ENV_FILE" 2>/dev/null; then
        info "$dir already referenced in $CCENV_ENV_FILE — leaving alone"
        return
    fi
    {
        printf '\n# [ccenv]\n'
        printf 'case ":$PATH:" in\n'
        printf '    *":%s:"*) ;;\n' "$dir"
        printf '    *) export PATH="%s:$PATH" ;;\n' "$dir"
        printf 'esac\n'
    } >> "$CCENV_ENV_FILE"
    info "appended PATH guard for $dir"
}

# PATH always gets ~/.local/bin (pip --user lands binaries there).
ensure_env_path '$HOME/.local/bin'

# PYTHONUSERBASE only when the platform default isn't already ~/.local.
# Linux's posix_user scheme already defaults there; macOS with Homebrew
# Python uses osx_framework_user (points at ~/Library/Python/<ver>), and
# that's when this matters.
if [ "$PLATFORM_DEFAULTS_TO_LOCAL" != "1" ]; then
    ensure_env_var PYTHONUSERBASE '"$HOME/.local"'
fi

info "open a new terminal or run: . $CCENV_ENV_FILE"

# ----------------------------------------------------------------------------
# Record what we just installed.
#
# install.sh runs to completion above (set -e exits early on any failure), so
# at this point the install IS complete on THIS machine. Write the bundle
# version we just installed to ~/.config/ccenv/installed-version so the
# cross-machine `instenv.prompt` can tell "what's actually installed here"
# from "what source code happens to be on disk."
#
# Critical when /src is NFS-shared across machines: every machine sees the
# same /src/ccenv/VERSION the instant any one of them git-pulls or someone
# edits it on the share, but each machine's actual install state is its own.
# Without this marker, the prompt would falsely report every NFS-mounted
# system as "current" the moment ONE of them got installed.
# ----------------------------------------------------------------------------
INSTALL_MARKER="$HOME/.config/ccenv/installed-version"
mkdir -p "$(dirname "$INSTALL_MARKER")"
if [ -f "$SCRIPT_DIR/VERSION" ]; then
    cp "$SCRIPT_DIR/VERSION" "$INSTALL_MARKER"
    info "recorded installed version $(cat "$INSTALL_MARKER" 2>/dev/null | head -1) in $INSTALL_MARKER"
fi

# Record the Python ABI tag this install ran under, so the NEXT install can
# detect (and announce) a Python version bump and heal stale-ABI native deps.
# heal_stale_compiled_exts sets PYTHON_TAG_MARKER and reads the prior value.
if [ -n "${PYTHON_TAG_MARKER:-}" ]; then
    CUR_TAG=$(python3 -c 'import sys; print(sys.implementation.cache_tag)' 2>/dev/null)
    if [ -n "$CUR_TAG" ]; then
        printf '%s\n' "$CUR_TAG" > "$PYTHON_TAG_MARKER"
        info "recorded Python ABI tag $CUR_TAG in $PYTHON_TAG_MARKER"
    fi
fi

echo ""
echo "=== ccenv install complete ==="
