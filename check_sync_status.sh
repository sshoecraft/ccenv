#!/usr/bin/env bash
# check_sync_status.sh — SessionStart hook: warn when the current git repo is
# out of sync with its GitHub/origin remote.
#
# Installed by ccenv's top-level install.sh to ~/.claude/hooks/ and registered
# as a global SessionStart hook. It self-gates: a silent no-op unless ALL hold:
#   - git is installed
#   - the working directory is inside a git work tree
#   - HEAD is on a branch (not detached)
#   - that branch has a remote (branch.<b>.remote, else "origin") with a URL
#   - the branch exists on the remote
#
# Network: ONE read-only `git ls-remote` (no fetch, no ref mutation), bounded by
# a timeout, with interactive auth prompts disabled. On any error/offline it
# exits silently — it must never hang a session start and never cry wolf.
#
# Output: when (and only when) out of sync, it prints a SessionStart
# additionalContext JSON object instructing the assistant to surface a banner.
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

# --- gates ------------------------------------------------------------------
command -v git >/dev/null 2>&1 || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null) || exit 0
[ -n "$branch" ] || exit 0   # detached HEAD

remote=$(git config --get "branch.$branch.remote" 2>/dev/null)
[ -n "$remote" ] || remote="origin"
git remote get-url "$remote" >/dev/null 2>&1 || exit 0

local_sha=$(git rev-parse HEAD 2>/dev/null) || exit 0

# --- network: remote branch SHA (read-only) ---------------------------------
remote_line=$(run_with_timeout "$NET_TIMEOUT" git ls-remote "$remote" "refs/heads/$branch" 2>/dev/null) || exit 0
remote_sha=$(printf '%s\n' "$remote_line" | awk 'NR==1{print $1}')
[ -n "$remote_sha" ] || exit 0          # branch not on remote yet -> skip (avoid noise on local-only branches)

[ "$remote_sha" = "$local_sha" ] && exit 0   # in sync -> silent

# --- classify ---------------------------------------------------------------
status=""
if git cat-file -e "${remote_sha}^{commit}" 2>/dev/null; then
    if git merge-base --is-ancestor "$remote_sha" "$local_sha" 2>/dev/null; then
        ahead=$(git rev-list --count "${remote_sha}..HEAD" 2>/dev/null)
        status="you are AHEAD of ${remote}/${branch} by ${ahead} commit(s) not on GitHub — run: git push"
    elif git merge-base --is-ancestor "$local_sha" "$remote_sha" 2>/dev/null; then
        behind=$(git rev-list --count "HEAD..${remote_sha}" 2>/dev/null)
        status="you are BEHIND ${remote}/${branch} by ${behind} commit(s) — run: git pull"
    else
        status="local and ${remote}/${branch} have DIVERGED (each has unique commits) — run: git pull and reconcile"
    fi
else
    short=$(printf '%s' "$remote_sha" | cut -c1-9)
    status="GitHub commit ${short} on ${remote}/${branch} is not present locally — run: git fetch && git pull"
fi

dirty=""
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    dirty=" (there are also uncommitted local changes)"
fi

repo=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)")

emit "IMPORTANT — show this to the user verbatim at the very start of your reply: *** WARNING: git repo '${repo}' (branch ${branch}) is NOT in sync with GitHub *** — ${status}${dirty}."
exit 0
