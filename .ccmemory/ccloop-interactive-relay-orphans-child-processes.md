---
name: ccloop-interactive-relay-orphans-child-processes
description: TWO orphan mechanisms, both fixed: (1) ccloop's own abnormal death -> PDEATHSIG (v0.6.1); (2) relay SIGTERM hitting a CCLOOP_CLAUDE_BIN shell wrapper…
metadata:
  type: project
tags: [ccloop, orphans, signals, relay, process-tree, CCLOOP_CLAUDE_BIN]
---

## ccloop leaked `claude` processes — TWO separate mechanisms

Both are fixed now. They are independent; fixing the first did not fix the
second, and that is what made this confusing.

### Mechanism 1: ccloop's own process dies abnormally (fixed, bundle v0.6.1)

`run_session_interactive()` spawned the child with a plain `Popen` — no
death-of-parent protection. If ccloop itself was killed outside its own
signal handling (crash, `kill -9` on just that PID, OOM), the child was
reparented to init and ran forever.

Fix: `_pdeathsig_preexec` — `prctl(PR_SET_PDEATHSIG, SIGTERM)` via
`preexec_fn`, on both `run_session()` and `run_session_interactive()`.
Chosen over `killpg` because setsid/pgid changes would cost the interactive
TUI its controlling terminal (raw mode, Ctrl-C, SIGWINCH).

**Note the limit that mattered later: PDEATHSIG protects only the TRACKED
child. It is cleared on fork, so a wrapper's own children never inherit it.**

### Mechanism 2: relay SIGTERM lands on a wrapper, not on claude (fixed, bundle v0.11.1 / ccloop 0.10.1)

This is the one that produced the long-lived production leak, and it fires
while ccloop is perfectly healthy.

`CCLOOP_CLAUDE_BIN` is routinely a **shell wrapper** — e.g.
`/usr/local/bin/clyde`, which exports `ANTHROPIC_BASE_URL` /
`ANTHROPIC_MODEL` / `CLAUDE_CODE_MAX_CONTEXT_TOKENS` and then runs
`claude --dangerously-skip-permissions --effort medium "$@"`. So the PID
ccloop tracks is **bash**; `claude` is its grandchild.

The relay did `proc.terminate()` → SIGTERM to bash. A **non-interactive bash
does not forward SIGTERM to the foreground job it is waiting on** — bash
dies, the foreground child does not. `claude` was reparented (to
`systemd --user`, not always PID 1 — a user systemd is a subreaper) and kept
running forever. One leaked `claude` per relay, every one still pointed at
the same run state.

Production evidence (2026-07-24, atrader): one `ccloop --resume-run` with
FOUR live `claude ... begin` processes — sessions 10/11/12/13, elapsed
3h17m / 2h50m / 1h21m / 47m. Sessions 10-12 had `PPID 3043`
(`systemd --user`) and no wrapper left; session 13 was the live
`clyde`(bash) → `claude` pair. The itrader run on the same box invoked
`claude` DIRECTLY (no wrapper) and had exactly one claude, PPID = ccloop —
that contrast is the diagnosis in one `ps`.

30-second repro, no ccloop needed:
```
printf '#!/bin/bash\npython3 -c "import time; time.sleep(60)"\n' > w.sh
chmod +x w.sh; ./w.sh & W=$!; sleep 1; K=$(pgrep -P $W)
kill -TERM $W; sleep 1; ps -p $K -o pid=,ppid=      # -> alive, PPID 1
```

Fix in `runner.py`: `_descendants()` walks `/proc` for the child's whole
subtree, **deepest-first**, snapshotted **before** signalling (a wrapper's
worker stops being a descendant the instant the wrapper dies), then
`_terminate_tree()` escalates SIGTERM → SIGKILL across it. Every PID is
pinned to its `/proc/<pid>/stat` start time (`_proc_identity`) so a recycled
PID can never be signalled. Called from the watcher on relay AND swept again
after `proc.wait()` returns.

Why not the headless path's approach: `run_session()` uses
`start_new_session=True` + `os.killpg`, which does cover wrappers (the
grandchild inherits the pgid). The interactive path cannot — setsid detaches
the TUI's controlling terminal, and the child shares ccloop's own process
group, so killpg would kill ccloop too.

### CORRECTION to the earlier "ruled out" conclusion

A previous investigation ran real `claude` via `CCLOOP_CLAUDE_BIN=clyde`
through 5 relay cycles, saw `exit=143`, and concluded the relay path was
clean ("0/5 leaked", "NOT where the real leak comes from"). **That was
wrong.** 143 = SIGTERM is what the *wrapper bash* exits with — the check
confirmed the tracked PID died, which was never in question. It did not look
for a surviving grandchild. When a check can be satisfied by the wrong
process, it proves nothing: assert on the PID that must NOT survive
(`pgrep -P <wrapper>` before the kill), not on the one you signalled.

### Testing notes

- `tests/test_runner.py::test_interactive_relay_kills_claude_behind_a_shell_wrapper`
  uses a real bash wrapper + real fork/exec; signal delivery across a process
  boundary is the whole subject and cannot be mocked. It finds the worker via
  `pgrep -P` (deliberately NOT the runner's own `/proc` walk, so it can't
  pass by agreeing with a broken implementation).
- `run_session_interactive` calls `signal.signal`, so **it must run on the
  main thread** in tests. Drive it from the main thread and put the
  observer/trigger in the helper thread, not the other way around.
- The autouse `no_sleep` fixture patches `runner.time.sleep` — which is the
  *global* `time` module, so the test's own `time.sleep` is a no-op too. Any
  wait loop must use a `time.monotonic()` deadline; a counted retry loop
  waits zero wall-clock. This silently flaked the PDEATHSIG test (fixed).
- `/proc/<pid>` existence is NOT liveness: an unreaped zombie still has an
  entry. Check state field == `Z` (`_gone()` helper).

### Complementary hardening (user-side, not applied)

Making the wrapper's last line `exec claude ...` collapses bash into the
claude process: one PID, so both PDEATHSIG and `terminate()` hit claude
directly. Worth doing in `clyde`, but it is a wrapper-by-wrapper fix — ccloop
cannot rely on it, hence the tree kill.
