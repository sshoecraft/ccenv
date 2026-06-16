---
name: ccloop-stop-hook-return-0-kills-session
description: ccloop Stop hook MUST block, not return 0, to keep the session alive — ccloop's runner relays on session-end. "No-op" semantics differ from pure Clau…
metadata:
  type: project
tags: [ccloop, hooks, session-lifecycle]
---

In a normal Claude Code session, a Stop hook that returns 0 with no output is a benign no-op: the model stops, the TUI sits idle, the harness wakes the model on the next event (e.g. background task completion).

In **ccloop**, the runner actively drives the session — when claude stops, ccloop summarizes the transcript and relays to a fresh session. So `return 0` from a Stop hook ≠ "no-op" — it lets the session END, which costs the running background task (the fresh session loses task context) AND it short-circuits any later gates in `keepgoing.py` (notably the cutoff gate, which writes the halt sentinel the interactive watcher polls for).

This bit us when the "background-work wait gate" was implemented as `return 0` on detection of a pending `*.output` file:

1. Stale `.output` file present at relay boundary (harness hadn't reaped it yet).
2. Wait gate fired → `return 0`.
3. Cutoff gate at line ~316 never ran → halt sentinel `halt-<sid>` never written.
4. Interactive watcher's poll loop never saw the sentinel → never SIGTERMed the TUI.
5. Session hung at 270k/250k tokens; the user saw the model say "I'm at the relay boundary — wrapping up." followed by a final summary, then nothing.

**Why:** The user pushed for "just a fucking no-op" earlier because in pure Claude Code semantics that IS the right answer. The mistake was applying that semantic to ccloop without accounting for ccloop's session-driving behavior. Should have caught this at design time.

**How to apply:**

- In ccloop's `keepgoing.py`, any gate that wants to "do nothing" while keeping the session alive MUST emit `decision: block` (re-feed), NOT `return 0`.
- Order matters: cutoff MUST come BEFORE the wait gate, so cutoff always fires when context is exhausted regardless of pending task state. Losing a task to relay is recoverable; blowing past the context wall is not.
- ~~File-presence is fine as the trigger for the wait gate; false positives (stale `.output` not yet reaped) are recoverable because the harness reaps the file in seconds.~~ **WRONG — corrected 2026-06-16.** Claude Code NEVER reaps `tasks/*.output`; they persist for the whole session, so presence-based detection wedged sessions PERMANENTLY (gate re-fired on dead tasks forever). The wait gate must check writer *liveness* (procfs fd holder / mtime), not presence. See [[claude-code-does-not-reap-task-output-files]].
- Wait re-feeds intentionally do NOT bump the keepgoing counter or count toward `CCLOOP_MAX_CONTINUES` — that cap exists to break model-pathology spin loops, not external-work waits.
- The reason text in the wait re-feed should be MINIMAL ("Wait. Background command still running."), not the keepgoing CONTINUE_MSG. The keepgoing nudge pushes the model toward "pick a new angle," which is the opposite of what waiting requires.

Tests guarding the regression: `test_cutoff_wins_over_pending_background`, `test_pending_background_blocks_with_wait_message`, `test_pending_background_does_not_bump_keepgoing_counter`.

Related: [[no-git-checkout-to-undo-own-edits]] — also from this same session of mistakes.
