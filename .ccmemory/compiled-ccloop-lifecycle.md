---
name: compiled-ccloop-lifecycle
description: ccloop session lifecycle: event-driven relay, Stop-hook blocking rules, two orphan-process mechanisms, CLI flag threading, handoff-doc history, cutof…
metadata:
  type: project
tags: [compiled, ccloop, relay, orphans, hooks, handoff, cutoff]
---

# ccloop session lifecycle: relay, hooks, orphans, flags

ccloop's job: drive `claude` sessions, and when context fills, summarize and
restart in a fresh session. Everything below is mechanism and failure history
for that loop — relay triggering, the Stop hook that must not no-op, child
orphaning across two independent mechanisms, CLI flag threading, and the
handoff-doc question that got resolved and then reversed.

## Relay must be event-driven, not cutoff-driven

`<run-dir>/cutoff` (default 250k, an absolute token count with no relation to
the model's real context window) is only an *early* relay knob, never the sole
guarantee against hitting the hard context wall. A real run wedged with
`cutoff=500000` (2.5x the window) because `tokens >= cutoff` could never trip
below the wall — no relay ever fired. Compounded by the old shared
`/tmp/ccusage-<uid>.json` cache getting clobbered by a concurrent same-UID
session (foreign `session_id` -> `exact_tokens` returned None -> gate
fail-open); either bug alone guarantees the wedge. [[ccloop-relay-not-cutoff-dependent]]

The hard guarantee is a deterministic transcript signal: with
`DISABLE_AUTO_COMPACT=1` (which ccloop always sets), Claude Code does not
error out on a full context window — it injects a synthetic assistant turn
(`type=="assistant"`, `isApiErrorMessage==true`, text `"Prompt is too long"`,
`model:"<synthetic>"`, all-zero usage — skip these turns when summing tokens)
and idles on "Context limit reached · /compact or /clear to continue" with no
human present to type it. `transcript.hit_context_wall()` tail-scans for this
event; the interactive watcher relays on it, headless `-p` relays on stream
`saw_prompt_too_long` after real work. Do NOT "fix" wedges by adding another
magic threshold (e.g. relay at 85% of window) — explicitly rejected as
replacing one hand-set number with another; react to the real event instead.
[[context-wall-deterministic-signal]]

Cache redesign (ccusage v0.3.0, shipped ccenv v0.2.0) killed the
concurrent-clobber fail-open: per-session file
`$XDG_STATE_HOME/ccusage/<session-id>.json`, pruned after 2 days, legacy
`/tmp` path honored as transition fallback.

Given this design is deliberately event-driven and structurally can't be
tuned by cutoff, do not propose lowering `--cutoff` as a cost lever at all —
see the dedicated finding below.

## Stop hook must block, not return 0

In plain Claude Code, a Stop hook returning 0 with no output is a benign
no-op. In ccloop it is not: ccloop's runner relays on session-end, so
`return 0` lets the session actually END — losing any running background
task's context, and short-circuiting later gates in `keepgoing.py`, notably
the cutoff gate that writes the halt sentinel the interactive watcher polls
for. A wait gate for pending background work that did `return 0` on detecting
a `*.output` file caused a real hang: stale `.output` present at the relay
boundary -> wait gate fires `return 0` -> cutoff gate at line ~316 never runs
-> halt sentinel never written -> watcher never SIGTERMs the TUI -> session
sits at 270k/250k tokens forever after a "wrapping up" message.
[[ccloop-stop-hook-return-0-kills-session]]

Rules: any "do nothing" gate in `keepgoing.py` must emit `decision: block`
(re-feed), never `return 0`. Cutoff must run before the wait gate so it always
fires on context exhaustion regardless of pending-task state — losing a task
to relay is recoverable, blowing past the wall is not. Wait re-feeds
deliberately do not bump the keepgoing counter or count toward
`CCLOOP_MAX_CONTINUES` (that cap breaks model-pathology spin loops, not
external-work waits), and the wait re-feed's reason text must be minimal
("Wait. Background command still running.") rather than the keepgoing
CONTINUE_MSG, which nudges toward "pick a new angle" — the opposite of
waiting.

The wait gate's original trigger was bare `.output` file *presence*,
believing the harness reaps the file "in seconds." It does not: Claude Code
never deletes a Bash background task's `.output` file for the life of the
session (confirmed 2026-06-16; old mxfs sessions held 7 and 41 stale files).
Presence != liveness — the only correct check is whether a live process still
holds the file open (`/proc/<pid>/fd` scan for a holder; non-procfs platforms
fall back to an mtime freshness window, `STALE_OUTPUT_SECONDS=90`). Fixed in
ccloop 0.5.1; verified against the real wedged session (5 files present, 0
counted after the fix). Any tool anywhere that wants to know if a Claude Code
background task is still running must check writer liveness, never presence.
[[claude-code-does-not-reap-task-output-files]]

## Two independent orphan mechanisms (both fixed)

ccloop leaked `claude` processes via two unrelated bugs; fixing one did not
fix the other. [[ccloop-interactive-relay-orphans-child-processes]]

1. **ccloop's own abnormal death** (fixed bundle v0.6.1): the interactive
   child was a plain `Popen` with no death-of-parent protection — `kill -9`
   on just ccloop's PID, a crash, or OOM reparented the child to init forever.
   Fix: `_pdeathsig_preexec` (`prctl(PR_SET_PDEATHSIG, SIGTERM)` via
   `preexec_fn`) on both `run_session()` and `run_session_interactive()`,
   chosen over `killpg`/`setsid` because that would cost the interactive TUI
   its controlling terminal. PDEATHSIG protects only the tracked child and is
   cleared on fork — a wrapper's own children never inherit it, which sets up
   mechanism 2.

2. **Relay SIGTERM landing on a shell wrapper, not on claude** (fixed bundle
   v0.11.1 / ccloop 0.10.1) — this produced the long-lived production leak
   while ccloop was perfectly healthy. `CCLOOP_CLAUDE_BIN` is routinely a
   shell wrapper (e.g. `clyde`, which exports env vars then execs `claude
   ...`), so the PID ccloop tracks is bash and `claude` is its grandchild. A
   non-interactive bash does not forward SIGTERM to the foreground job it's
   waiting on: bash dies, `claude` is reparented (to `systemd --user`, not
   always PID 1) and runs forever. Production evidence: one
   `ccloop --resume-run` with four live `claude` processes from sessions
   10-13, up to 3h17m old. Fix in `runner.py`: `_descendants()` walks `/proc`
   for the child's whole subtree, deepest-first, **snapshotted before
   signalling** (a wrapper's worker stops being a descendant the instant the
   wrapper dies), then `_terminate_tree()` escalates SIGTERM -> SIGKILL
   across it. Every PID is pinned to its `/proc/<pid>/stat` start time so a
   recycled PID is never signalled. Called from the watcher on relay and
   swept again after `proc.wait()`. The headless path's `killpg` approach
   doesn't generalize to interactive: `setsid` would detach the TUI's own
   controlling terminal.

   A prior investigation had wrongly cleared this path: it ran real `claude`
   through 5 relay cycles via a wrapper, saw `exit=143` (SIGTERM), and
   concluded "0/5 leaked." Wrong — 143 is what the *wrapper bash* exits with;
   the check never looked for a surviving grandchild. Lesson: assert on the
   PID that must NOT survive (`pgrep -P <wrapper>` before the kill), not on
   the one you signalled.

   30-second repro without ccloop: spawn a bash wrapper around a sleeping
   python child, `kill -TERM` the wrapper, and observe the child now has
   PPID 1.

   Testing notes: signal-delivery-across-a-process-boundary tests need a real
   bash wrapper + real fork/exec, not mocks
   (`test_interactive_relay_kills_claude_behind_a_shell_wrapper`, finds the
   worker via `pgrep -P`, deliberately not the runner's own `/proc` walk, so
   it can't pass by agreeing with a broken implementation).
   `run_session_interactive` calls `signal.signal` and so must run on the
   main thread in tests — drive it from main, put the observer/trigger in a
   helper thread. The autouse `no_sleep` fixture patches the global
   `runner.time.sleep`, so a test's own `time.sleep` becomes a no-op too —
   any wait loop must use a `time.monotonic()` deadline, not a counted retry
   (this silently flaked the PDEATHSIG test). `/proc/<pid>` existing is not
   liveness — an unreaped zombie still has an entry; check state == `Z`.

   Complementary but not applied: making a wrapper's last line
   `exec claude ...` collapses bash into claude (one PID, PDEATHSIG and
   `terminate()` both hit it directly) — worth doing wrapper-by-wrapper, but
   ccloop can't rely on every wrapper doing it, hence the tree kill.

## CLI flags thread as explicit params, not env mutation

Established with `--model` (ccloop 0.9.0 / bundle 0.5.0). Runtime config is
env-driven (`_config()` reads `CCLOOP_*`), but CLI flags do NOT mutate
`os.environ` — they thread as explicit keyword params:
`cli.main -> runner.cmd_run/cmd_resume -> runner.loop(..., model=None)`, and
`loop()` overrides the cfg dict after `_config()` so the flag wins over the
env var. [[ccloop-cli-flags-thread-as-params]]

Recipe for the next value flag: use `_extract_value_flag(argv, flag, what)`
(generic popper, handles `--flag=V` and `--flag V`, last occurrence wins) via
a thin wrapper that adds validation (e.g. `--model` rejects empty/whitespace
so `--model=` doesn't silently no-op through the falsy check in
`_build_command`); thread through both `cmd_run` and `cmd_resume` (extraction
happens before dispatch so both get it); update the USAGE string. Semantics
are per-invocation like the env var, unless the value must survive a resume
for other processes' hooks to read (as `--cutoff` does, persisted in
`<run-dir>/cutoff`) — model is consumed only in the runner process so it does
not persist. Testing: `tests/fake_claude.py` honors `FAKE_ARGS_FILE` to
assert end-to-end what argv reached the claude command line; cli-parse tests
stub `cmd_run`/`cmd_resume` to capture kwargs — keep signatures in sync when
adding a param. Don't write a seam test whose fake reimplements the override
logic — that tests the test, use the FAKE_ARGS_FILE path instead.

## Handoff docs: tried freshness-stamping, then banned entirely

v0.20.0 tried to make a model-maintained handoff document trustworthy via
freshness stamps: mtime-vs-session-start checks, a STALE marker, capped
scrapers (`files_edited` capped at 60, `last_text` capped at 4,000 chars —
hitting that cap every session is what proved it was never really a
summary), "last 20 bash commands" cut for being 465-803 tokens. Durable
lessons from that effort, still true even though the doc itself is gone: a
document the outgoing model must remember to update stops being updated
(hard evidence: mxfs `state.md` sat 7 days stale across 36 ccloop runs while
`state.sh` beside it ran fresh every session — this is why `state.py` stayed
a *computed* hook); a stale hand-written doc is byte-identical to a fresh
one, only mtime tells them apart; render a problem into the block, never
swallow it; audit every scraper for a cap. [[handoff-docs-must-be-freshness-stamped]]

v0.21.0 reversed the premise, not just the freshness mechanics: the handoff
document itself was the mistake. User verdict, blunt and repeated: it "cost
me millions in tokens" — every prompt/skill/hook instruction to "keep a
handoff file current" charges output tokens on every turn to reproduce, from
memory, a document Claude Code already writes for free as the per-session
transcript at `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`. Do not put
such an instruction in any prompt, preamble, skill, or hook — not as a tier,
not with a freshness check, not "just a short one." Instead, locate the prior
session deterministically and hand the next session the path:
`sessions.log` holds session-ids in order within a run (walk backwards past
any deleted transcript); across runs, the newest non-empty `.jsonl` in the
project dir excluding this run's own ids. Emit nothing if there's nothing to
point at — never name a path that doesn't exist. Because a transcript is
multi-megabyte JSONL, the pointer must ship with size, line count, a
suggested tail `Read` offset (`lines - 300`), and a grep example, or "read
that file" blows the context it was meant to save. Non-Anthropic API backends
driving `claude` can't be trusted to know their own transcript path or
convention, so the wrapper computes and states it. All of this — module,
`CCLOOP_HANDOFF_*` env vars, freshness/STALE machinery — was deleted in
v0.21.0; the resulting filename is deliberately not documented anywhere in
ccenv, so no session goes looking for one. [[never-make-sessions-maintain-handoff-docs]]

## Cutoff is not a cost lever — do not revisit

Downstream of the event-driven relay design above: a two-week live experiment
lowering `--cutoff` from 500k to 145k (to stay under a 150k "expensive"
threshold) was reverted — 145k lost even though it hit its own target,
because restart churn cost more than the high-context tail it saved. A ccloop
session pays ~65-70k tokens of fixed context before any real work happens
(measured median S=65,075 over 409 sessions on CLI 2.1.239), and that floor
is rising ~1.3k/release from the CLI alone (~47k of the 65k is CLI floor, not
ccenv's). At cutoff=145k that floor is ~93% overhead per session; at
cutoff=500k it's ~16%. ~75% of S is not something ccenv owns, and MCP tool
schemas are already deferred (`ENABLE_TOOL_SEARCH`) — that lever is spent.
This is a permanent conclusion, not provisional: do not propose lowering
`--cutoff` again, not "until S is reduced" — S cannot be reduced enough, and
the CLI floor keeps growing. The only remaining lever on context cost is
per-request context growth (δ) — keep subagent/grind output out of the
parent context; that's delegation, and it gets more valuable every CLI
release. Full analysis: `/src/ccenv/docs/context-economics.md`.
[[ccloop-cutoff-lowering-already-tried-and-lost]]
