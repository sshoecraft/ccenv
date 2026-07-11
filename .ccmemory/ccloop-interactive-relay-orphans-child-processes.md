---
name: ccloop-interactive-relay-orphans-child-processes
description: ccloop's tracked claude child has no death-of-parent protection: if ccloop's own process dies abnormally, the child is orphaned to init and runs fore…
metadata:
  type: project
---

## Bug: ccloop leaves orphaned `claude` processes running when the wrapper itself dies

Confirmed 2026-07-11 with three separate repros. Matches production evidence:
a real `/src/mxfs` ccloop run had 8 prior-session `claude ... begin`
processes still running with **PPID 1** (orphaned, reparented to init)
while only the current session had a live parent.

### Root cause (CONFIRMED — this is the real one; see "ruled out" below)

`ccloop/src/ccloop/runner.py`'s `run_session_interactive()` spawns the
`claude` child with plain `subprocess.Popen(cmd, env=env)` — no
`start_new_session`, no death-of-parent protection of any kind. As long as
ccloop's own relay logic is the thing that ends the child (halt sentinel /
context wall → `proc.terminate()`), it works fine. But if the **ccloop
wrapper process itself dies for any reason other than its own graceful
relay** — crash, `kill -9` on just that PID, OOM-kill, or (in the general
case, not the session-leader special case below) the terminal session
disappearing — nothing tells the kernel to clean up the child. It gets
reparented to init (PID 1) and keeps running forever, fully intact
(the complete `claude --effort=... --session-id ... begin` invocation, not
some stripped-down remnant).

Repro (no `script`/pty — see "gotcha" below for why that matters):
```
CCLOOP_CLAUDE_BIN=<fake-claude wrapper> FAKE_MODE=sleep FAKE_SLEEP=120 \
  ccloop -i --cutoff=0 '' 'sit and wait' &
CCLOOP_PID=$!
sleep 3
kill -9 "$CCLOOP_PID"          # kills ONLY ccloop, nothing else
# → the claude child is still alive afterward, PPid: 1
```
Verified: after `kill -9` on ccloop alone, `/proc/<child>/status` showed
`PPid: 1`, `State: S (sleeping)` — running indefinitely.

### Gotcha that produced a false negative first

Wrapping the launch in `script -qec "ccloop ..." log` makes **ccloop itself
the session leader** of the new pty `script` allocates. Killing the session
leader makes the kernel auto-deliver `SIGHUP` to the whole foreground
process group (a real POSIX protection), which happened to also kill the
`claude` child in that setup — a false negative. In real usage the user's
**shell/tmux/sshd is the session leader**, not `ccloop` (`ccloop` is just
an ordinary job in that session), so killing `ccloop` alone does NOT
trigger that kernel protection. Reproduce parent-death scenarios by
backgrounding the command directly (`cmd &`), not through `script`, unless
you're deliberately testing the session-leader case.

### Hypothesis RULED OUT: the graceful in-run relay path leaking a claude-internal subprocess

Original hypothesis (wrong, but worth recording so it isn't re-litigated):
that `run_session_interactive()`'s SIGTERM-only-`proc.pid` relay logic (as
opposed to `run_session()`'s headless path, which correctly uses
`start_new_session=True` + `os.killpg` — see DESIGN.md line ~40-42, ~449,
added specifically to fix orphaned processes from Ctrl-C) leaves behind
some subprocess the real `claude` CLI forks internally, every relay.

Disproved by a live end-to-end test: real `claude` binary via
`CCLOOP_CLAUDE_BIN=/usr/local/bin/clyde` (execs real `claude` against a
free local OpenAI-compatible model, zero API cost) run through actual
`ccloop -i --cutoff=1 ...` (cutoff=1 = `keepgoing.MIN_REASONABLE_CUTOFF_TOKENS`
= 1000 tokens, the minimum meaningful value — trips the halt sentinel on
turn one) for 5 full relay cycles under a `script`-allocated pty. Result:
**0/5 leaked** — every session's `claude` process exited cleanly with
`exit=143` (SIGTERM) and no descendant survived. The targeted relay path is
solid; this is NOT where the real leak comes from.

(A synthetic variant — a fake `claude` that deliberately forks a worker and
never forwards signals to it — DOES leak 5/5 under the same driver. That
mechanism is real in principle if `claude` ever internally forks a
long-lived helper, but isn't what's happening with the actual `claude`
binary today. Worth a defensive fix but not the production bug.)

### Fix direction

The idiomatic Linux fix for "child must die if its specific parent process
dies, no matter how" is `prctl(PR_SET_PDEATHSIG, SIGTERM)`, set in the
child (via `preexec_fn` in `subprocess.Popen`, which runs post-fork
pre-exec in the child's single remaining thread — safe here since the
callback is a single side-effect-free ctypes call, not lock-touching).
Unlike `killpg`, this needs no process-group/session changes, so it can't
interfere with the interactive TUI's terminal ownership (raw mode,
Ctrl-C handling, SIGWINCH) — it only fires when ccloop's own process (the
one that called prctl at spawn time) actually dies. Apply to BOTH
`run_session()` and `run_session_interactive()`'s `Popen` calls (headless
mode has killpg for graceful cases, but no protection against ccloop's own
process crashing either).

Not yet applied as of this memory (diagnosis + repro only).
