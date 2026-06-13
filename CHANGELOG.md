# ccenv changelog

Per the global rule: patch = fix, minor = feature, major = breaking.

## v0.1.4

`install.sh` auto-appends `PYTHONUSERBASE` (where needed) and a
runtime-guarded PATH-prepend for `~/.local/bin` to the shell's env file —
no more "REQUIRED: shell environment setup" copy-paste banner. Picks the
file sourced for non-interactive shells:

  - zsh  → `~/.zshenv` (sourced for ALL zsh invocations)
  - bash → `~/.bashrc` (no bash equivalent of zshenv; works for terminal-
                        launched claude since env inherits to subprocesses)

Two helpers: `ensure_env_var VAR VALUE` (skips if any existing
`export VAR=` line is present — never overrides the user's own setting),
and `ensure_env_path DIR` (writes a `case ":$PATH:" in *":$dir:"*) ;; *) export PATH="$dir:$PATH" ;; esac`
block so even when the env file is sourced repeatedly — nested subshells,
fresh terminals over a long session — `~/.local/bin` doesn't accumulate
in PATH). Each appended block is preceded by a `# [ccenv]` marker so
future install.sh runs (and humans) can identify what we put there.

Removed: the `rc_has` helper added earlier this version cycle (obsoleted
by the auto-append approach), the entire "REQUIRED: shell environment
setup" banner including the `need_pythonuserbase` / `need_path` gating
and the copy-paste one-liner generation, and the redundant
"USER_BIN is NOT on your shell PATH" warning in the verify step.

Per the [pythonuserbase-in-zshenv] memory: Windsurf writes its PATH
export to `~/.zshrc` — sourced only for interactive shells — and the
Claude Code hooks then fail with `ModuleNotFoundError`. ccenv 0.1.4 does
NOT repeat that mistake.

## v0.1.3

ccmemory v0.9.0: SessionStart protocol now MANDATES `memory_list()` as
the REQUIRED first action of every session, before responding to the
user's first message. v0.7.0 had steered the model toward it via
decision rules ("inventory? → list. topic? → search. body? → get."), but
concept/behavior memories (user preferences, conventions,
cross-cutting invariants) are not tied to any file path. The
PreToolUse-on-Read auto-injection that surfaces file-tied memories
never fires for them, so the model only learned of their existence if
it independently decided to query — which it usually didn't, because
it had no signal that anything was worth querying for. Result: lessons
captured into memory were re-derived from scratch in subsequent
sessions, and corrections the user had already applied got
re-litigated. Fixed in the SESSION_PROTOCOL text.

Also in 0.1.3: moved ccmemory's version history out of
`ccmemory/CLAUDE.md` and into a proper `ccmemory/CHANGELOG.md`, then
deleted `ccmemory/CLAUDE.md` entirely. Per-module `CLAUDE.md` files in
this repo are deprecated — only the top-level `/src/ccenv/CLAUDE.md`
(installed as the global `~/.claude/CLAUDE.md` rules file) should
exist; subdirectory architecture/install/test info belongs in that
subdirectory's `README.md`.

## v0.1.2

`install.sh` writes the bundle version to
`~/.config/ccenv/installed-version` on successful completion;
`instenv.prompt` reads it as the per-machine "what's actually installed
here" signal. Fixes a cross-machine version-check false positive
surfaced on the first multi-system update (0.1.0 → 0.1.1):

  $ <fire instenv.prompt>
  → "All six systems are current at v0.1.1 — no updates needed."

Reality: none of those six had actually re-run `install.sh`. They all
saw `/src/ccenv/VERSION` = 0.1.1 because `/src` is NFS-shared across
the cluster and the master's push updated the shared file in-place.
Their installed bits (pip packages, hooks, `~/.local/bin/...`) were
still at 0.1.0.

Root cause: confused "source code version" with "installed version."
The source tree is shared; the installed state is per-machine. Checking
the source `VERSION` file to decide "is this machine current?" is wrong
by construction in an NFS setup. Fix: write a per-machine marker only
`install.sh` updates.

## v0.1.1

ccloop wait gate fix. The previous "background-work wait gate" in
`ccloop/src/ccloop/keepgoing.py` was broken two ways that, together,
hung interactive sessions at the relay boundary:

  1. `return 0` is the wrong semantic in ccloop. In pure Claude Code a
     Stop hook returning 0 is a benign no-op. But ccloop's runner
     actively drives the session: when claude stops, the loop
     summarizes and relays. `return 0` = let the session END, which
     loses the running task.

  2. The wait gate ran BEFORE the cutoff gate, so the wait's `return 0`
     short-circuited the cutoff check. With a stale `.output` file
     in the tasks dir (harness reaps eventually, but not instantly),
     the wait gate would fire at the relay boundary, the cutoff gate
     never ran, the halt sentinel never got written, and the
     interactive watcher never SIGTERMed the TUI. Observed live:
     session hung at 270k/250k tokens with the model saying "I'm at
     the relay boundary — wrapping up." and then nothing.

Fix:
- Moved the wait gate to AFTER the cutoff gate. Cutoff always wins.
- Replaced `return 0` with `_emit_wait(n)` that emits
  `decision: block` with a minimal "Wait. Background command still
  running." re-feed. Session stays alive without the keepgoing
  CONTINUE_MSG "pick a new angle" push.
- Wait re-feeds intentionally do NOT bump the keepgoing counter and
  are NOT capped by `CCLOOP_MAX_CONTINUES` — that cap protects
  against model pathology, not external work.

New regression-guarding tests:
`test_cutoff_wins_over_pending_background`,
`test_pending_background_blocks_with_wait_message`,
`test_pending_background_does_not_bump_keepgoing_counter`,
`test_done_wins_over_pending_background`,
`test_pending_background_task_count_real_glob`.

## v0.1.0

Initial bundle VERSION. ccenv as a whole had no formal version string
until this — each component had its own pyproject version (ccloop
0.3.x, ccmemory 0.6.x, ccusage 0.1.x, ccteam 0.3.x, ccenvmcp 0.1.x)
but there was nothing at the umbrella level. Bare-semver one-line
file at `/src/ccenv/VERSION`, matching `ccteam/VERSION`'s format.
Read by `instenv.prompt` (locally via `cat`, remotely via
`raw.githubusercontent.com`) as the single source of truth for
cross-machine "is ccenv current?" checks.
