---
name: changelog-rationale-is-not-evidence
description: Never repeat a CHANGELOG/doc rationale as fact. Two false ones (claude -p metered billing; compactor blocks session start) cost compaction for months.
metadata:
  type: feedback
tags: [ccmemory, ccloop, compaction, billing, claude-p, verification]
---

A rationale written into this repo's CHANGELOG, docs or docstrings by an earlier Claude session is a claim, not evidence. Before relaying it to the user or designing on it, check it: external-policy claims (billing, pricing, API behavior) against the vendor's current page with a web search, behavioral claims against a transcript or measurement.

Two such claims were relayed to the user as fact and both were wrong:

- **"`claude -p` bills a separate metered API-rate credit pool."** Anthropic announced that on 2026-05-14 for 2026-06-15 and paused it on 2026-06-15. It never took effect. support.claude.com article 15036540 (update dated 2026-10-07) says `claude -p` and the Agent SDK can still be used with subscription limits. Max/Team plans also get monthly API credits that cover it. ccmemory removed its `claude -p` compile path over this, and ccloop gated `--headless` behind `--accept-api-cost`. Both were corrected in bundle v0.38.0.
- **"Compactor agents dispatched at session start make the session wait ~90s."** ccmemory v0.20.0 removed automatic compaction over this. No transcript was ever cited. The user had watched those runs: the memory-compactor always ran as a background agent and never blocked the turn. With nothing dispatching, one store's backlog reached 165 notes and its feedback memories overflowed even a feedback-only `memory_list`. The dispatch was restored in ccmemory v0.21.0.

Separately: Agent-tool subagents run inside the live session and never involve `claude -p`. Whatever `claude -p` costs, it was never a reason to stop background compaction.

**Why:** The user had to push back twice ("do a damn web search") before the claims were checked. Each one had been read out of the tree and repeated with confidence.

**How to apply:** When explaining why the system behaves some way and the only source is our own prose, say that it is our own prose. Verify before stating it as fact, and verify before any change that depends on it.
