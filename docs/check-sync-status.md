# gitsync — git ↔ GitHub sync warning (SessionStart hook)

`check_sync_status.sh` is a global Claude Code **SessionStart** hook that warns,
in-session, when the repository you're working in has drifted out of sync with
its GitHub/`origin` remote. It exists to prevent the failure mode where one
machine commits + pushes work, another machine never pulls it, and you keep
working on a stale tree (which is exactly how a `memory_list` commit went
"missing" until a `git fetch` revealed local was a commit behind).

## Where it lives

- Source: `check_sync_status.sh` (repo root).
- Installed to: `~/.claude/hooks/check_sync_status.sh` by the top-level
  `install.sh` (`gitsync` step), registered as a `SessionStart` hook in
  `~/.claude/settings.json`. Registration is idempotent and self-heals a stale
  path. Skip with `./install.sh --skip gitsync`.

## Behavior

Self-gating — a **silent no-op** unless every precondition holds:
- `git` is installed,
- the cwd is inside a git work tree,
- HEAD is on a branch (detached HEAD is skipped),
- that branch has a remote (`branch.<b>.remote`, else `origin`) with a URL,
- the branch exists on the remote.

It makes **one read-only `git ls-remote`** (no `fetch`, no ref mutation) to read
the remote branch SHA, then classifies using local ancestry checks:

| condition                                   | message |
|---------------------------------------------|---------|
| remote SHA == local HEAD                    | in sync → silent |
| remote is an ancestor of HEAD               | **AHEAD** — unpushed local commits → `git push` |
| HEAD is an ancestor of remote               | **BEHIND** — GitHub has newer commits → `git pull` |
| remote SHA not present locally              | **BEHIND/DIVERGED** → `git fetch && git pull` |
| neither is an ancestor of the other         | **DIVERGED** → `git pull` + reconcile |

If out of sync it also notes whether there are uncommitted local changes.

## Output

When (and only when) out of sync, it prints a `SessionStart` JSON object with
`hookSpecificOutput.additionalContext` containing an imperative instruction +
banner, so the assistant surfaces it verbatim at the top of its first reply:

```
*** WARNING: git repo '<name>' (branch <b>) is NOT in sync with GitHub *** — <status>.
```

## Safety / robustness

- **Never hangs / never prompts**: `GIT_TERMINAL_PROMPT=0`, SSH
  `BatchMode=yes -o ConnectTimeout=5`, HTTP low-speed timeouts, and a `timeout`/
  `gtimeout` wrapper (6s). Offline or auth-required → fails fast, exits silently.
- **Never cries wolf**: any error path exits `0` with no output.
- **Zero dependencies**: pure bash, so it does not depend on `PYTHONUSERBASE`/
  python import paths (which can break python-based hooks).
- **Read-only**: `git ls-remote` only — it never fetches, writes refs, or
  modifies the working tree.
