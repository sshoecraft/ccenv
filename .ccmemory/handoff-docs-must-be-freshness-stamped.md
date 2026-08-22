---
name: handoff-docs-must-be-freshness-stamped
description: REVERSED in v0.21.0 — the handoff doc itself was the mistake, not its freshness. Kept for the durable half: hand-maintained docs die; scraper caps.
metadata:
  type: project
tags: [ccloop, handoff, summarize, superseded]
---

# SUPERSEDED — see `never-make-sessions-maintain-handoff-docs`

ccenv v0.21.0 deleted the handoff module, its two `CCLOOP_HANDOFF_*` env vars,
the freshness/STALE machinery, and the prompt instruction that told every
session to maintain a handoff document under `<project>/.ccloop/`.

**The filename is deliberately not written down** — here or anywhere in the
ccenv tree. A session that reads the name goes looking for the file, and the
whole point of 0.21.0 is that no session should ever touch one.

The 0.20.0 reasoning below was internally sound and still wrong at the root:
it asked *"how do we make a model-maintained handoff trustworthy"* when the
answer was *"don't have one"*. The per-session transcript Claude Code already
writes is complete, free, and cannot go stale. ccloop now names that file in
the prompt (`runner.prior_session_block`) and asks nothing of the session.
The user's verdict on the write-tax: "cost me millions in tokens."

**Still true and worth keeping** from the original finding:

- A document the outgoing model has to remember to update stops being updated.
  Hard evidence: mxfs `state.md` sat 7 days stale across 36 ccloop runs while
  `state.sh` beside it ran fresh every session. This is why `state.py` is a
  computed hook — that decision stands.
- A stale hand-written document is byte-identical to a fresh one; only mtime
  separates them. If you ever *must* consume one, stamp it. (v0.21.0's answer
  is to not consume one.)
- Render a problem INTO the block, never swallow it (`state.py` discipline).
- Audit every scraper for a cap. `files_edited` was the only unbounded one —
  capped at 60. `last_text` caps at 4,000 chars, and hitting that cap on every
  session is what proves it was never a summary.
- `Last 20 bash commands` (465-803 tok, a third of the resume) was correctly
  cut in 0.20.0 and stays cut.
- Mechanism: `summarize()` runs in the runner AFTER a session exits; the relay
  is event-driven on the wall event. There is no "cutoff hook" that generates
  the resume — the token cutoff is only an early-relay knob.

**Dead with 0.21.0:** the tiering rules, the mtime-vs-session-start freshness
check, the STALE marker, and the "1,284 tok (no handoff) → 396 (fresh handoff)"
result — that measurement priced only the prompt, never the output tokens the
session burned rewriting the file all run.
