---
name: quota-two-weekly-buckets-and-context-weighting
description: Quota has a separate weekly Fable bucket the statusline can't see, and cost is context-weighted, not flat per request. Request counts are a proxy.
metadata:
  type: project
tags: [cost, quota, ccusage, delegation, ccloop]
---

Measured from `/usage` and the 2.1.239 binary, 2026-08-22.

## Two weekly buckets, not one

| window | used (2026-08-22) |
|---|---:|
| Current session (5-hour) | 15% |
| Current week (all models) | 19% |
| **Current week (Fable)** | **25%** |

The CLI tracks six windows internally (`five_hour`, `seven_day`,
`seven_day_opus`, `seven_day_sonnet`, `seven_day_overage_included`,
`overage`) via `GET /api/oauth/usage` (`fetchUtilization`).

- A **sonnet subagent request cannot debit the Fable bucket** — this
  settles the load-bearing premise of the delegation proposal.
- It is a transfer, not a freebie: a Fable request debits *both* buckets,
  a sonnet request only all-models. Delegation pays until all-models
  becomes binding. At the measured mix Fable binds (25% vs 19%).
- **`get_context_usage` / the statusline expose only `five_hour` and
  `seven_day`** — the per-model windows never reach them, so the meter the
  loop reads cannot see the bucket that actually exhausts. Use `/usage`.
- A +50% weekly-limits promo ran through 2026-08-31; headroom measured in
  that window is inflated. Do not calibrate thresholds on it.

## Cost is context-weighted

`/usage` states it: "**89% of your usage was at >150k context.** Longer
sessions are more expensive even when cached."

So the mxfs cost-audit's "~4,430 Fable requests/week" constant and every
request-count share are **proxies**, not costs — measured under one stable
context profile, not generalizable. Reject any claim that "every request
costs the same regardless of size".

Under context weighting, delegation's real mechanism is that grind output
never enters the parent's context, which lowers context growth per request
and extends the work span inside the unchanged 500k cutoff — attacking the
churn problem from the side the cutoff experiment could not.

Full write-up: `/src/ccenv/docs/context-economics.md` (also carries the
verified hook mechanics: gate subagent pass-through on `agent_id` not
`agent_type`; `PreToolUse` supports non-blocking `additionalContext`; a
denied tool call has already cost its request).
