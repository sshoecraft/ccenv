---
name: ccloop-cutoff-lowering-already-tried-and-lost
description: Never propose lowering ccloop --cutoff. 500→145 lost to restart churn, and startup context (65k, ~75% CLI floor) can't be cut enough to change that.
metadata:
  type: feedback
tags: [ccloop, cost, context, cutoff]
---

## The experiment (user, ~2 weeks, reverted 2026-08)

`ccloop --cutoff=500` → `--cutoff=145` → back to `--cutoff=500`.
**145 lost**, even though it kept sessions under the 150k "expensive"
threshold.

Why: a ccloop session reaches ~70k tokens of context before the first unit
of real work. That is a fixed cost paid once per session and rebuilt on
every restart.

- cutoff 145k → ~75k of actual work per session → fixed cost ~93% overhead
- cutoff 500k → ~430k of actual work per session → ~16% overhead

The restart churn cost more than the high-context tail saved. User's words:
"we burned so many tokens in that short period that it made it not worth
it… I set it back to 500 and yes it costs more at the top end, but we don't
end up with the constant restart churn."

## Measured 2026-08-22 — the decision is PERMANENT, not provisional

`scripts/startup_context_audit.py` over 409 mxfs sessions (S = the first
assistant turn's total prompt tokens; exact, not estimated):

- mxfs on CLI 2.1.239: **S = 65,075 median** (confirms the user's ~70k).
- **S is dominated by the CLI and is growing**: 40,799 on 2.1.217 →
  65,075 on 2.1.239, +24k (+59%) in ~22 releases, ~+1.3k per release.
- Attribution: ~47k CLI floor (not ours), ~8.8k project CLAUDE.md,
  ~4.0k ccloop session prompt, ~2.2k global CLAUDE.md, ~3k agents/ccmemory.
- MCP tool schemas are **already deferred** (`ENABLE_TOOL_SEARCH`; every
  2.1.239 session sampled used ToolSearch). That lever is spent.

~75% of S is not ours. A perfect job on everything we own saves ~8k of 65k
— nowhere near enough to make a 145k cutoff win.

## Standing rules

1. **Do not propose lowering the cutoff.** Not "until S is reduced" — S
   cannot be reduced enough, and the CLI floor rises every release.
2. The only remaining lever on context cost is **δ, context growth per
   request** — keep grind output out of the parent context. That is
   delegation to subagents, and it gets more valuable every CLI release.
3. Trimming the 35 KB mxfs CLAUDE.md is housekeeping (~4k/session for a
   50% cut), not a strategy.

Full analysis: `/src/ccenv/docs/context-economics.md`.
