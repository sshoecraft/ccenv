#!/usr/bin/env bash
# check_sync_status.sh — SessionStart hook: warn when git checkouts are out
# of sync with their GitHub/origin remote.
#
# Installed by ccenv's top-level install.sh to ~/.claude/hooks/ and registered
# as a global SessionStart hook. Runs TWO checks every session start:
#
#   1. The user's CURRENT working directory (if it's a git repo).
#   2. The ccenv source checkout itself (path read from
#      ~/.config/ccenv/source.path, written by install.sh). This catches the
#      case where another machine pushed ccenv updates and this one never
#      pulled — even when the user is sitting in some other project.
#
# Both checks self-gate: silent no-ops unless ALL hold:
#   - git is installed
#   - the target path is inside a git work tree
#   - HEAD is on a branch (not detached)
#   - that branch has a remote (branch.<b>.remote, else "origin") with a URL
#   - the branch exists on the remote
#
# Network: ONE read-only `git ls-remote` per check (no fetch, no ref mutation),
# bounded by a timeout, with interactive auth prompts disabled. On any error
# or offline it exits silently — must never hang a session start, never cry
# wolf.
#
# Output: ONE SessionStart additionalContext JSON object, only when at least
# one check finds a problem. If both are out of sync, the message combines
# both findings.
#
# Sync classification (no fetch needed):
#   remote == local                          -> in sync (silent)
#   remote is ancestor of local              -> AHEAD  (unpushed local commits)
#   local  is ancestor of remote             -> BEHIND (GitHub has newer commits)
#   remote object absent locally             -> BEHIND/DIVERGED (run git fetch)
#   both have unique commits                 -> DIVERGED

set -u

# Fail fast instead of prompting for credentials or hanging on a dead network.
export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=5"
export GIT_HTTP_LOW_SPEED_LIMIT=1000
export GIT_HTTP_LOW_SPEED_TIME=5

NET_TIMEOUT=6

run_with_timeout() {
    local secs="$1"; shift
    if command -v timeout >/dev/null 2>&1; then
        timeout "$secs" "$@"
    elif command -v gtimeout >/dev/null 2>&1; then
        gtimeout "$secs" "$@"
    else
        "$@"
    fi
}

json_escape() {
    # single-line value: escape backslash and doublequote only
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

emit() {
    # $1 = additionalContext text (kept to a single line)
    local esc; esc=$(json_escape "$1")
    printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s"}}\n' "$esc"
}

# check_repo_sync DIR LABEL BEHIND_ACTION
#
#   DIR           absolute path to the work tree to check
#   LABEL         short identifier for the message ("git repo 'foo'",
#                 "ccenv harness", etc.) — included verbatim in the warning
#   BEHIND_ACTION command to suggest when the local branch is BEHIND the
#                 remote (the "interesting" case for harness checkouts).
#                 Pass empty string to use the plain default "git pull".
#
# Echoes the warning suffix on stdout when out of sync; echoes nothing when
# in sync, when a gate fails, or on any error. Never returns non-zero — a
# hook must not hang a session start.
#
# All git invocations use `git -C "$dir"` so the caller's CWD is untouched
# and we can check multiple repos in a single hook run.
check_repo_sync() {
    local dir="$1" label="$2" behind_action="$3"

    [ -d "$dir" ] || return 0
    command -v git >/dev/null 2>&1 || return 0
    git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0

    local branch
    branch=$(git -C "$dir" symbolic-ref --quiet --short HEAD 2>/dev/null) || return 0
    [ -n "$branch" ] || return 0   # detached HEAD

    local remote
    remote=$(git -C "$dir" config --get "branch.$branch.remote" 2>/dev/null)
    [ -n "$remote" ] || remote="origin"
    git -C "$dir" remote get-url "$remote" >/dev/null 2>&1 || return 0

    local local_sha
    local_sha=$(git -C "$dir" rev-parse HEAD 2>/dev/null) || return 0

    local remote_line remote_sha
    remote_line=$(run_with_timeout "$NET_TIMEOUT" git -C "$dir" ls-remote "$remote" "refs/heads/$branch" 2>/dev/null) || return 0
    remote_sha=$(printf '%s\n' "$remote_line" | awk 'NR==1{print $1}')
    [ -n "$remote_sha" ] || return 0   # branch not on remote yet -> skip

    [ "$remote_sha" = "$local_sha" ] && return 0   # in sync -> silent

    local behind_default="git pull"
    [ -n "$behind_action" ] && behind_default="$behind_action"

    local status=""
    if git -C "$dir" cat-file -e "${remote_sha}^{commit}" 2>/dev/null; then
        if git -C "$dir" merge-base --is-ancestor "$remote_sha" "$local_sha" 2>/dev/null; then
            local ahead
            ahead=$(git -C "$dir" rev-list --count "${remote_sha}..HEAD" 2>/dev/null)
            status="${label} is AHEAD of ${remote}/${branch} by ${ahead} commit(s) not on GitHub — run: git push"
        elif git -C "$dir" merge-base --is-ancestor "$local_sha" "$remote_sha" 2>/dev/null; then
            local behind
            behind=$(git -C "$dir" rev-list --count "HEAD..${remote_sha}" 2>/dev/null)
            status="${label} is BEHIND ${remote}/${branch} by ${behind} commit(s) — run: ${behind_default}"
        else
            status="${label} and ${remote}/${branch} have DIVERGED (each has unique commits) — run: git pull and reconcile"
        fi
    else
        local short
        short=$(printf '%s' "$remote_sha" | cut -c1-9)
        status="${label}: GitHub commit ${short} on ${remote}/${branch} is not present locally — run: git fetch && git pull"
    fi

    local dirty=""
    if [ -n "$(git -C "$dir" status --porcelain 2>/dev/null)" ]; then
        dirty=" (there are also uncommitted local changes)"
    fi

    printf '%s%s' "$status" "$dirty"
}

# ----------------------------------------------------------------------------
# Check 1: the user's CURRENT working directory.
# ----------------------------------------------------------------------------
repo_msg=""
repo_root=""
if command -v git >/dev/null 2>&1 \
   && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
    if [ -n "$repo_root" ]; then
        repo_name=$(basename "$repo_root")
        repo_branch=$(git -C "$repo_root" symbolic-ref --quiet --short HEAD 2>/dev/null || echo "")
        if [ -n "$repo_branch" ]; then
            repo_msg=$(check_repo_sync \
                "$repo_root" \
                "git repo '${repo_name}' (branch ${repo_branch})" \
                "")
        fi
    fi
fi

# ----------------------------------------------------------------------------
# Check 2: the ccenv source checkout (independent of the user's CWD).
# Path comes from the marker file install.sh writes; if the marker is
# missing or points at a now-removed directory, silently skip.
# ----------------------------------------------------------------------------
ccenv_msg=""
ccenv_marker="$HOME/.config/ccenv/source.path"
if [ -f "$ccenv_marker" ]; then
    ccenv_src=$(head -n1 "$ccenv_marker" 2>/dev/null | tr -d '[:space:]')
    # Avoid double-warning if the user happens to BE in the ccenv repo right
    # now — check 1 already covered it.
    if [ -n "$ccenv_src" ] \
       && [ -d "$ccenv_src" ] \
       && [ "$ccenv_src" != "$repo_root" ]; then
        ccenv_branch=$(git -C "$ccenv_src" symbolic-ref --quiet --short HEAD 2>/dev/null || echo "")
        if [ -n "$ccenv_branch" ]; then
            # The action hint for ccenv intentionally differs from a plain
            # repo's: a pull alone won't refresh installed pip packages /
            # hooks / global CLAUDE.md — the user needs the full install.sh
            # re-run to reconcile the harness state.
            ccenv_msg=$(check_repo_sync \
                "$ccenv_src" \
                "ccenv harness at ${ccenv_src} (branch ${ccenv_branch})" \
                "cd ${ccenv_src} && git pull && ./install.sh")
        fi
    fi
fi

# ----------------------------------------------------------------------------
# Emit a single combined banner if anything was out of sync.
# ----------------------------------------------------------------------------
if [ -n "$repo_msg" ] || [ -n "$ccenv_msg" ]; then
    combined=""
    if [ -n "$repo_msg" ]; then
        combined="*** WARNING: ${repo_msg} ***"
    fi
    if [ -n "$ccenv_msg" ]; then
        if [ -n "$combined" ]; then
            combined="${combined}  ALSO:  *** WARNING (ccenv harness): ${ccenv_msg} ***"
        else
            combined="*** WARNING (ccenv harness): ${ccenv_msg} ***"
        fi
    fi
    emit "IMPORTANT — show this to the user verbatim at the very start of your reply: ${combined}."
fi
exit 0
