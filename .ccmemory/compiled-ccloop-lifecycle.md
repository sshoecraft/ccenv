---
name: compiled-ccloop-lifecycle
description: ccloop session lifecycle: event-driven relay, Stop-hook blocking, two orphan mechanisms, CLI flag threading, handoff-doc reversal, cutoff not a cost…
metadata:
  type: project
tags: [compiled, ccloop, relay, orphans, hooks, handoff, cutoff]
---

# ccloop session lifecycle: relay, hooks, orphans, flags

ccloop's job: drive `claude` sessions, and when context fills, summarize and restart in a fresh session. Below is mechanism and failure history for that loop: relay triggering, the Stop hook that must not no-op, child orphaning across two independent mechanisms, CLI flag threading, and the handoff-doc question that was resolved and then reversed.

## Relay must be event-driven, not cutoff-driven

`<run-dir>/cutoff` (default 250k, an absolute token count with no relation to the model's real context window) is only an *early* relay knob, never the sole guarantee against hitting the hard context wall. A real run wedged on the 200K wall with `cutoff=500000` (2.5x the window, set deliberately as a test) because `tokens >= cutoff` could never trip below the wall, so no relay ever fired. Compounded by the old shared `/tmp/ccusage-<uid>.json` cache being clobbered by a concurrent same-UID session (foreign `session_id` -> `exact_tokens` returned None -> gate fail-open); either bug alone guarantees the wedge. `ccloop-relay-not-cutoff-dependent`

The hard guarantee is a deterministic transcript signal. With `DISABLE_AUTO_COMPACT=1` (ccloop always sets it), Claude Code does not error out on a full window. It injects a synthetic assistant turn into the transcript JSONL and idles on the TUI message "Context limit reached · /compact or /clear to continue", with no human to type `/compact`:

```json
{"type":"assistant","isApiErrorMessage":true,
 "message":{"role":"assistant","model":"<synthetic>",
            "content":[{"type":"text","text":"Prompt is too long"}]}}
```

Match `type=="assistant"` AND `isApiErrorMessage==true` AND content text contains `"Prompt is too long"`. Its `usage` block is all zeros, which is why a naive "last assistant usage" reader reports 0 tokens for a wedged session; skip `isApiErrorMessage` turns when summing tokens. ccloop v0.6.0 reacts to it: `transcript.hit_context_wall()` tail-scans, the interactive watcher relays on it, and headless `-p` relays on stream `saw_prompt_too_long` after real work (aborts only if zero real turns, meaning the handoff prompt itself is too big). Verified on a real wedged run: detector True, real assistant_turns=100, context_tokens=172438 on a 200000 window (~86%). `context-wall-deterministic-signal`

Do NOT "fix" wedges by adding another magic threshold (e.g. relay at 85% of window); the user explicitly rejected replacing one hand-set number with another. React to the real event, in `runner` + `transcript.hit_context_wall`, independent of cutoff and cache.

Cache redesign (ccusage v0.3.0, shipped ccenv v0.2.0 = ccloop 0.6.0 + ccusage 0.3.0) killed the concurrent-clobber fail-open: per-session file `$XDG_STATE_HOME/ccusage/<session-id>.json` (default `~/.local/state/ccusage/`), pruned after 2 days. ccloop reads its own session file (legacy `/tmp` path honored as transition fallback); the MCP server reads the newest file.

Because the design is event-driven and can't be tuned by cutoff, do not propose lowering `--cutoff` as a cost lever; see the dedicated section below.

## Stop hook must block, not return 0

In plain Claude Code, a Stop hook returning 0 with no output is a benign no-op: the model stops, the TUI idles, the harness wakes the model on the next event. In ccloop the runner relays on session-end, so `return 0` lets the session actually END, losing the running background task's context (the fresh session loses it) and short-circuiting later gates in `keepgoing.py`, notably the cutoff gate that writes the halt sentinel (`halt-<sid>`) the interactive watcher polls for. `ccloop-stop-hook-return-0-kills-session`

Real hang, step by step: (1) stale `.output` present at relay boundary; (2) wait gate fired `return 0`; (3) cutoff gate at line ~316 never ran; (4) `halt-<sid>` never written, so the watcher never SIGTERMed the TUI; (5) session sat at 270k/250k tokens after the model said "I'm at the relay boundary — wrapping up." plus a final summary, then nothing. Origin: the user had pushed for "just a fucking no-op" because in pure Claude Code that IS right; the mistake was applying it to ccloop without accounting for its session-driving behavior, and should have been caught at design time.

Rules:
- Any "do nothing" gate in `keepgoing.py` must emit `decision: block` (re-feed), never `return 0`.
- Cutoff must run BEFORE the wait gate so it always fires on context exhaustion regardless of pending-task state. Losing a task to relay is recoverable; blowing past the wall is not.
- Wait re-feeds deliberately do not bump the keepgoing counter or count toward `CCLOOP_MAX_CONTINUES` (that cap breaks model-pathology spin loops, not external-work waits).
- The wait re-feed reason must be minimal ("Wait. Background command still running."), not the keepgoing CONTINUE_MSG, which nudges toward "pick a new angle", the opposite of waiting.
- Regression tests: `test_cutoff_wins_over_pending_background`, `test_pending_background_blocks_with_wait_message`, `test_pending_background_does_not_bump_keepgoing_counter`.
- Related, same session of mistakes: [[no-git-checkout-to-undo-own-edits]].

The wait gate's original trigger was bare `.output` file *presence*, on the belief that "the harness reaps the file in seconds" (that belief was struck out and corrected 2026-06-16). Claude Code stores each Bash-background task's output at `/tmp/claude-<uid>/<slug>/<session-id>/tasks/<task-id>.output` and NEVER deletes it, for the whole session and beyond (confirmed 2026-06-16, Linux uid 1000; old mxfs sessions held 7 and 41 stale files; a 5-file session matched 5 long-finished task IDs). There is no on-disk running-state registry; the tasks dir holds only `.output` files. Running task = file held open by a live process (`/proc/<pid>/fd/<n>` symlink target, holders>0); finished = 0 holders, file persists. Presence-based detection wedged sessions permanently: any background command ever run re-fired "N background command(s) still running" on every Stop until the context wall (seen in mxfs run, session c3bb9e30, 5 dead files). Fix in ccloop 0.5.1 (`keepgoing._pending_background_task_count`): count an `.output` only when a live process holds it (`/proc/<pid>/fd` scan, no subprocess, only on Stop, short-circuited); non-procfs platforms (macOS) fall back to an mtime freshness window, `STALE_OUTPUT_SECONDS=90`. Verified on the real wedged session: 5 files present, 0 counted. Any tool asking whether a Claude Code background task still runs must check writer liveness, never presence. `claude-code-does-not-reap-task-output-files`

## Two independent orphan mechanisms (both fixed)

ccloop leaked `claude` processes via two unrelated bugs; fixing the first did not fix the second, which is what made it confusing. `ccloop-interactive-relay-orphans-child-processes`

1. **ccloop's own abnormal death** (fixed bundle v0.6.1). `run_session_interactive()` spawned a plain `Popen` with no death-of-parent protection; a crash, `kill -9` on just ccloop's PID, or OOM reparented the child to init forever. Fix: `_pdeathsig_preexec` (`prctl(PR_SET_PDEATHSIG, SIGTERM)` via `preexec_fn`) on both `run_session()` and `run_session_interactive()`, chosen over `killpg`/`setsid` because those would cost the interactive TUI its controlling terminal (raw mode, Ctrl-C, SIGWINCH). Limit that mattered later: PDEATHSIG protects only the TRACKED child and is cleared on fork, so a wrapper's own children never inherit it.

2. **Relay SIGTERM lands on a shell wrapper, not on claude** (fixed bundle v0.11.1 / ccloop 0.10.1). The long-lived production leak, firing while ccloop was perfectly healthy. `CCLOOP_CLAUDE_BIN` is routinely a shell wrapper, e.g. `/usr/local/bin/clyde`, which exports `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL` / `CLAUDE_CODE_MAX_CONTEXT_TOKENS` then runs `claude --dangerously-skip-permissions --effort medium "$@"`. The tracked PID is bash; `claude` is its grandchild. The relay did `proc.terminate()`; a non-interactive bash does not forward SIGTERM to the foreground job it waits on, so bash dies and `claude` is reparented (to `systemd --user`, not always PID 1, since a user systemd is a subreaper) and runs forever, one leaked `claude` per relay, all pointed at the same run state.

   Production evidence (2026-07-24, atrader): one `ccloop --resume-run` with FOUR live `claude ... begin` processes, sessions 10/11/12/13, elapsed 3h17m / 2h50m / 1h21m / 47m. Sessions 10-12 had `PPID 3043` (`systemd --user`) and no wrapper left; session 13 was the live `clyde`(bash) -> `claude` pair. The itrader run on the same box invoked `claude` DIRECTLY (no wrapper) and had exactly one claude, PPID = ccloop; that contrast is the diagnosis in one `ps`.

   30-second repro, no ccloop:
   ```
   printf '#!/bin/bash\npython3 -c "import time; time.sleep(60)"\n' > w.sh
   chmod +x w.sh; ./w.sh & W=$!; sleep 1; K=$(pgrep -P $W)
   kill -TERM $W; sleep 1; ps -p $K -o pid=,ppid=      # -> alive, PPID 1
   ```

   Fix in `runner.py`: `_descendants()` walks `/proc` for the child's whole subtree, deepest-first, **snapshotted before signalling** (a wrapper's worker stops being a descendant the instant the wrapper dies); `_terminate_tree()` escalates SIGTERM -> SIGKILL across it. Every PID is pinned to its `/proc/<pid>/stat` start time (`_proc_identity`) so a recycled PID is never signalled. Called from the watcher on relay and swept again after `proc.wait()` returns. The headless path's `start_new_session=True` + `os.killpg` does cover wrappers (grandchild inherits the pgid) but doesn't generalize to interactive: setsid detaches the TUI's controlling terminal, and the child shares ccloop's own process group so killpg would kill ccloop too.

   **Correction of a wrong "ruled out"**: a prior investigation ran real `claude` via `CCLOOP_CLAUDE_BIN=clyde` through 5 relay cycles, saw `exit=143` (SIGTERM), and concluded "0/5 leaked", "NOT where the real leak comes from". Wrong: 143 is what the *wrapper bash* exits with; the check never looked for a surviving grandchild. Lesson: when a check can be satisfied by the wrong process it proves nothing; assert on the PID that must NOT survive (`pgrep -P <wrapper>` before the kill), not the one you signalled.

   Testing notes:
   - `tests/test_runner.py::test_interactive_relay_kills_claude_behind_a_shell_wrapper` uses a real bash wrapper + real fork/exec (signal delivery across a process boundary cannot be mocked). It finds the worker via `pgrep -P`, deliberately not the runner's own `/proc` walk, so it can't pass by agreeing with a broken implementation.
   - `run_session_interactive` calls `signal.signal`, so it must run on the main thread; drive it from main, put the observer/trigger in a helper thread.
   - The autouse `no_sleep` fixture patches `runner.time.sleep`, the *global* `time` module, so a test's own `time.sleep` is a no-op too. Wait loops must use a `time.monotonic()` deadline, not a counted retry (this silently flaked the PDEATHSIG test; fixed).
   - `/proc/<pid>` existing is not liveness: an unreaped zombie still has an entry; check state field == `Z` (`_gone()` helper).

   Complementary hardening, user-side, not applied: make the wrapper's last line `exec claude ...` so bash collapses into claude (one PID; PDEATHSIG and `terminate()` both hit it directly). Worth doing in `clyde`, but wrapper-by-wrapper; ccloop can't rely on it, hence the tree kill.

## CLI flags thread as explicit params, not env mutation

Established with `--model` (ccloop 0.9.0 / bundle 0.5.0). Runtime config is env-driven (`_config()` reads `CCLOOP_*`), but CLI flags do NOT mutate `os.environ`; they thread as explicit keyword params: `cli.main -> runner.cmd_run / cmd_resume -> runner.loop(..., model=None)`, and `loop()` overrides the cfg dict after `_config()`: `cfg = _config(); if model: cfg["model"] = model` (flag wins over `CCLOOP_MODEL`). `ccloop-cli-flags-thread-as-params`

Recipe for the next value flag (e.g. `--effort`):
1. `cli.py`: `_extract_value_flag(argv, flag, what)` is the generic popper (handles `--flag=V` and `--flag V`, last occurrence wins, missing-value error). `_extract_cutoff` and `_extract_model` are thin wrappers; add validation in the wrapper (`--model` rejects empty/whitespace so `--model=` doesn't silently no-op via the falsy `if cfg[...]` check in `_build_command`).
2. Thread through BOTH `cmd_run` and `--resume-run`'s `cmd_resume`; extraction happens before dispatch so both get it.
3. Update USAGE (Options section; the env var's line gets "(--x flag wins over this)").
4. Semantics: per-invocation like the env var; a resume does NOT remember the run's original model. Unlike `--cutoff`, persisted in `<run-dir>/cutoff` because guard/keepgoing HOOKS in other processes must read it; model is consumed only in the runner process.

Testing: `tests/fake_claude.py` honors `FAKE_ARGS_FILE`, appending each invocation's argv as a JSON line, to assert end-to-end what reached the claude command line (flag-beats-env: set `CCLOOP_MODEL=sonnet`, pass `model="opus"`, assert `argv[argv.index("--model")+1] == "opus"`). cli-parse tests stub `runner.cmd_run`/`cmd_resume` with fakes capturing kwargs (`_stub_run`/`_stub_resume` in test_cli.py); keep signatures in sync when adding a param. Don't write a seam test whose fake reimplements the override logic (tests the test); use the FAKE_ARGS_FILE path.

## Handoff docs: tried freshness-stamping, then banned entirely

v0.20.0 tried to make a model-maintained handoff document (under `<project>/.ccloop/`) trustworthy via freshness stamps: mtime-vs-session-start checks, a STALE marker, tiering rules, capped scrapers (`files_edited` was the only unbounded one, capped at 60; `last_text` capped at 4,000 chars, and hitting that cap every session is what proved it was never really a summary), and "Last 20 bash commands" (465-803 tokens, a third of the resume) cut. Durable lessons, still true although the doc is gone: a document the outgoing model must remember to update stops being updated (hard evidence: mxfs `state.md` sat 7 days stale across 36 ccloop runs while `state.sh` beside it ran fresh every session; this is why `state.py` stays a *computed* hook); a stale hand-written doc is byte-identical to a fresh one, only mtime tells them apart; render a problem INTO the block, never swallow it (`state.py` discipline); audit every scraper for a cap. Mechanism: `summarize()` runs in the runner AFTER a session exits; relay is event-driven on the wall event; there is no "cutoff hook" that generates the resume. `handoff-docs-must-be-freshness-stamped`

v0.21.0 reversed the premise, not just the mechanics: the handoff document itself was the mistake (0.20.0 asked "how do we make a model-maintained handoff trustworthy" when the answer was "don't have one"). User verdict, blunt and repeated: it "cost me millions in tokens" — every prompt/skill/hook instruction to keep a handoff file current charges output tokens every turn to reproduce, from memory, a document Claude Code already writes free as the per-session transcript at `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`. Do not put such an instruction in any prompt, preamble, skill, or hook, not as a tier, not with a freshness check, not "just a short one." The 0.20.0 "1,284 tok (no handoff) -> 396 (fresh handoff)" result is dead: it priced only the prompt, never the output tokens burned rewriting the file all run.

Instead, locate the prior session deterministically and hand the next session the path (`runner.prior_session_block`): `sessions.log` holds session-ids in order within a run (walk backwards past any deleted transcript); across runs, the newest non-empty `.jsonl` in the project dir excluding this run's own ids. Emit nothing if there's nothing to point at; never name a path that doesn't exist. The pointer ships with size, line count, a suggested tail `Read` offset (`lines - 300`), and a grep example, or "read that file" blows the context it was meant to save. Non-Anthropic API backends driving `claude` can't be trusted to know their own transcript path or convention, so the wrapper computes and states it. The module, two `CCLOOP_HANDOFF_*` env vars, and freshness/STALE machinery were deleted in v0.21.0; the filename is deliberately not documented anywhere in ccenv so no session goes looking for one. `never-make-sessions-maintain-handoff-docs`

## Cutoff is not a cost lever — do not revisit

Downstream of the event-driven relay: a two-week live experiment lowering `--cutoff` from 500k to 145k (to stay under a 150k "expensive" threshold) was reverted. 145k lost even though it hit its own target, because restart churn cost more than the high-context tail it saved. A ccloop session pays ~65-70k tokens of fixed context before any real work (measured median S=65,075 over 409 sessions on CLI 2.1.239), rising ~1.3k/release from the CLI alone (~47k of the 65k is CLI floor, not ccenv's). At cutoff=145k that floor is ~93% overhead per session; at 500k ~16%. ~75% of S is not ccenv's, and MCP tool schemas are already deferred (`ENABLE_TOOL_SEARCH`), so that lever is spent. Permanent conclusion: do not propose lowering `--cutoff` again, not "until S is reduced." The only remaining lever is per-request context growth (delta): keep subagent/grind output out of the parent context (delegation, more valuable every CLI release). Full analysis: `/src/ccenv/docs/context-economics.md`. `ccloop-cutoff-lowering-already-tried-and-lost`
