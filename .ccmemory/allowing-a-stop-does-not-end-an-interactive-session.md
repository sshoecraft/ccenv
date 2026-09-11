---
name: allowing-a-stop-does-not-end-an-interactive-session
description: ccloop: returning 0 from keepgoing permits a stop but never ends an interactive TUI — only the halt sentinel does. Fixed in 0.34.1.
metadata:
  type: project
tags: [ccloop, keepgoing, cutoff, debugging]
---

## The mechanism

`keepgoing` returning 0 PERMITS a stop. It does not END a session.

- Headless `-p`: a permitted stop exits the process. Fine.
- Interactive TUI: a permitted stop returns to the prompt and the session stays
  resident. The ONLY thing that terminates it is the watcher in
  `runner.run_session_interactive` seeing `<run-dir>/halt-<session_id>`.

So any `return 0` path in `keepgoing` that does not write the sentinel leaves an
interactive session alive forever.

## The defect (fixed in ccenv 0.34.1)

The legitimate-completion check sat ABOVE the cutoff gate:

    if _criteria_met(run_dir):
        return 0            # no sentinel written
    ...
    cutoff = _read_cutoff(run_dir)   # the ONLY code that wrote the sentinel
    if cutoff > 0 and tokens >= cutoff:
        _signal_halt(...)

Once `criteria-met` went YES, the sentinel became unreachable by any route, so
no amount of token growth could end the session. A run met its criteria at
11:39 and its session was still resident and idle at 18:16, drifted to 510k
tokens past a 500k cutoff.

Both completion paths now call `_signal_converged()` before returning, logging
`converged` rather than `halt` so a clean finish stays distinguishable from a
cutoff relay.

## Two cutoff code paths — do not confuse them

`hook-events.log` shows `fired <tokens> <sid>` from **guard.py** (PostToolUse),
which detects the crossing but CANNOT terminate anything. `keepgoing` writes
`halt` / `converged`. Repeated `fired` lines with no `halt` line means the
crossing was noticed and nothing acted on it — which reads deceptively like
"the cutoff works but is ignored."

## Diagnostic lessons from getting this wrong twice

- **Verify process liveness, never infer it.** Concluded "the parent exited"
  from `sessions.log` being 0 bytes and the run reading converged. Wrong — the
  parent was alive the whole time, 3h29m in. `ps -o pid,etime,cmd -C ccloop`
  settled it in one call and should have been the FIRST call.
- **Check log field shapes against the code that writes them.** The log said
  `fired` with 3 fields; `_signal_halt` writes `halt` with 4. That mismatch was
  visible early and named a different writer (`guard`), but was read past.
- **Tests can encode the defect.** Two tests asserted the sentinel was ABSENT
  after completion, using that as a proxy for "no relay happened". They passed
  for as long as the bug existed. The honest assertion is that the sentinel IS
  present and the logged event is `converged`.

## Campaign criteria that can never be met

This run's criteria was "pick the highest-severity open defect ... and move to
the next one" — no state satisfies it. Any `criteria-met=YES` against such
criteria is a session misjudging, typically after finishing one item. Before
0.34.1 that produced a silent park; now it produces a clean early exit. Either
way the campaign stops short, so a run ending sooner than expected is worth
checking for `criteria-met` reappearing rather than assuming a loop failure.
