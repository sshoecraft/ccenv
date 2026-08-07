---
name: handoff-docs-must-be-freshness-stamped
description: ccloop 0.20.0: a hand-maintained handoff is a TIER, never a replacement. mxfs state.md sat 7d stale across 36 runs; only mtime separates stale from f…
metadata:
  type: project
tags: [ccloop, handoff, summarize, invariant]
---

## The pattern, twice now

`state.py` (v0.14.0) already recorded it: *"a document the outgoing model has
to remember to update eventually stops being updated, and a stale one is worse
than none."* That is why the forward-looking half of the handoff became a
computed hook (`state.sh`) instead of a hand-maintained `state.md`.

An mxfs session then proposed replacing the *backward* half — generated
`summarize()` output — with a hand-maintained `handoff.md`. Same idea, same
trap, one module over.

**Evidence, from the project making the proposal:**

```
/src/mxfs/state.md          mtime 2026-07-31   7 days stale
/src/mxfs/.ccloop/state.sh  mtime 2026-08-03   runs fresh every session
```

36 ccloop run directories in that window. The hand-maintained document was
already dead; the computed one was fine.

## The invariant

**A hand-maintained handoff is a TIER, never a replacement for generated
content.** The asymmetry that matters:

- A generated summary can be unhelpful, but it is *derived*, so it can never
  be stale.
- A stale hand-written handoff is **byte-identical to a fresh one**. Nothing in
  the content distinguishes them. Only the mtime does.

So: compare mtime against the session's start time and render a stale file
under an explicit marker naming its age. Never let it suppress the generated
fallback. And when there is no start time to compare against, report `stale` —
defaulting the other way lets any caller that forgets to thread the timestamp
silently assert currency it never checked.

Same discipline `state.py` uses for hook failures: render the problem INTO the
block, never swallow it.

## What was actually worth cutting

Measured across mxfs resumes (~1,900-2,100 tok each):

| section | tokens | verdict |
|---|---|---|
| `Last text from previous session` | 1,001 every time | keep as FALLBACK |
| `Last 20 bash commands` | 465-803 | **cut** |
| `Files written or edited` | ~40 | **keep** |

`last_text` hitting 1,001 on every session means it hit its 4,000-char cap
every time — it was never a summary, just the tail of assistant chatter. Still
the only thing that works when a session crashes without writing a handoff.

`files_edited` was the sole *unbounded* scraper (bash caps at 20x160, last_text
at 4,000 chars). Capped at 60. When auditing scrapers, check every one for a
cap — the one without it is the one nobody thought about.

## Mechanism correction worth keeping

There is no "cutoff hook" that generates the resume. `summarize()` runs in the
runner AFTER a session exits (`runner.py`), and the relay is event-driven on
the wall event — the token cutoff is only an early-relay knob. Proposals that
describe ccloop as summarizing "at cutoff" are describing a component that does
not exist.

## Result

2,074 tok → 1,284 (no handoff) → 396 (fresh handoff), on a real mxfs transcript.
