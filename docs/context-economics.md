# Context economics — what actually costs quota, and what ccloop can do about it

**Measured 2026-08-22.**  Written because the obvious lever (lower ccloop's
`--cutoff`) has already been tried and lost, and the reason it lost is not
obvious from the outside.  Read this before proposing any change to session
length, cutoff, or delegation.

Companion analysis: `/src/mxfs/docs/cost-audit.md` and
`/src/mxfs/docs/delegation.md` (per-request audit of the mxfs Fable loop),
`scripts/delegate_chain_distribution.py` (chain-length distribution).

---

## 1. Quota is metered in two weekly buckets, not one

`/usage` on 2026-08-22, mid-session:

| window | used |
|---|---:|
| Current session (5-hour) | 15% |
| Current week (all models) | 19% |
| **Current week (Fable)** | **25%** |

Fable has its own weekly cap, separate from the all-models cap.  The CLI
tracks six windows internally — `five_hour`, `seven_day`,
`seven_day_opus`, `seven_day_sonnet`, `seven_day_overage_included`,
`overage` — fetched from `GET /api/oauth/usage` (`fetchUtilization` in the
2.1.239 binary).

Consequences:

- A **sonnet subagent request cannot debit the Fable bucket.**  Delegation
  moves work off the binding constraint.  This was the load-bearing
  unverified premise of the delegation proposal; it now holds.
- It is a **transfer, not a freebie.**  A Fable request debits *both*
  buckets; a sonnet request debits only all-models.  Delegation pays until
  all-models becomes binding.  At the 2026-08-22 mix Fable is binding
  (25% vs 19% — ~1.3x faster), with 75% vs 81% headroom.
- `get_context_usage` / the statusline expose only `five_hour` and
  `seven_day`.  **The per-model windows never reach the statusline**, so
  the meter the loop reads cannot see the bucket that actually exhausts.
  Use `/usage` for that.
- A +50% weekly-limits promo runs through 2026-08-31.  Headroom measured
  now is temporarily inflated — do not calibrate thresholds on it.

## 2. Cost is context-weighted, not flat per request

`/usage` states it directly:

> **89% of your usage was at >150k context.**  Longer sessions are more
> expensive even when cached.

So every number denominated in *request counts* — including the mxfs
cost-audit's "~4,430 Fable requests/week" constant and the delegation
audit's category shares — is a **proxy**, not the cost.  The constant was
measured under a stable context profile and does not generalize across
profiles.  Anything that claims "every request costs the same regardless of
size" is wrong.

## 3. The cutoff experiment — ALREADY RUN, DO NOT RE-PROPOSE BLIND

The user ran ccloop at `--cutoff=145` for roughly two weeks, then reverted
to `--cutoff=500`.  Result: **145 was worse**, despite keeping sessions
under the 150k expensive threshold.

Why:

- A ccloop session reaches **~70k tokens of context before the first unit
  of real work happens** — system prompt, tool + MCP schemas, skills
  listing, global CLAUDE.md, project CLAUDE.md, the ccloop session prompt,
  the ccmemory SessionStart injection and the opening `memory_list`.
- That 70k is a **fixed cost paid once per session**, and it must be
  rebuilt (cache writes, re-orientation) on every restart.
- At cutoff 145k the amortization window is only ~75k of actual work per
  session — the fixed cost is ~93% overhead.  At 500k it is ~16%.
- The restart churn cost more than the high-context tail saved.

The naive model (minimise mean context ⇒ optimum near 135k) predicts the
opposite of the observed result.  Trust the measurement, not the model:
the rebuild term is far heavier than a cache-read-price model suggests.

## 4. The real lever is STARTUP context, not the cutoff

Startup context `S` is the only quantity that improves **both** regimes at
once:

- At a fixed 500k cutoff, every request in the session carries `S` fewer
  tokens — a straight multiplier on the context-weighted cost of the whole
  run.
- If `S` drops far enough, a lower cutoff stops being uneconomic, because
  the amortization window widens without touching the cutoff at all.

Lowering `S` trades nothing away.  Lowering the cutoff trades tail cost
against churn, and that trade has already been measured as a loss.

### Attribution of S (measured 2026-08-22, `scripts/startup_context_audit.py`)

`S` is measurable exactly, with no estimation: the first assistant turn of a
transcript records the prompt it was served, so
`input_tokens + cache_creation_input_tokens + cache_read_input_tokens` on
that turn **is** `S`.  Across 409 mxfs sessions and every other project on
this machine:

| project | n | median S | CLAUDE.md |
|---|---:|---:|---:|
| /src/mxfs | 409 | 55,130 | 35,267 B |
| /home/steve/underbelly | 30 | 48,143 | 10,290 B |
| /src/aitrader | 11 | 40,937 | 10,378 B |
| /src/ccenv | 13 | 38,146 | 7,249 B |
| /src/wowbot/alt | 17 | 19,816 | 0 |

**On the current CLI (2.1.239) mxfs sessions measure S = 65,075 median** —
matching the user's "~70k before any work" from the cutoff experiment.

#### S is dominated by the CLI, and it is GROWING

Median S for mxfs, by CLI version — the project's own config barely moved
across this range:

| CLI | median S | | CLI | median S |
|---|---:|---|---|---:|
| 2.1.217 | 40,799 | | 2.1.233 | 56,957 |
| 2.1.220 | 48,444 | | 2.1.237 | 61,282 |
| 2.1.226 | 55,983 | | 2.1.239 | **65,075** |

**+24k tokens (+59%) in ~22 releases**, roughly +1.3k per release.  July
median 47,763 → August median 55,942.

#### What that leaves us

mxfs ccloop session on 2.1.239, S = 65k:

| term | ~tokens | ours? |
|---|---:|---|
| CLI floor — system prompt, non-deferred tool schemas, skills listing | ~47k | **no** |
| project `CLAUDE.md` (35,267 B) | ~8.8k | yes |
| ccloop session prompt (16,049 B) | ~4.0k | yes |
| global `CLAUDE.md` (8,716 B) | ~2.2k | yes |
| project agents + ccmemory SessionStart injection + opening `memory_list` | ~3k | yes |

Cross-check: ccenv sessions on the same CLI measure S = 50,554 median.
The 14.5k gap to mxfs is accounted for by CLAUDE.md (+7k) and the ccloop
session prompt (+4k), leaving ~3.5k for agents/memory — consistent.

**MCP tool schemas are already deferred.**  Every 2.1.239 session sampled
in both projects used `ToolSearch`, so the four user-scope MCP servers
(`ask_gpt`, `ask_fable`, `ccusage`, `ccmemory`) are not sitting in the base
prompt.  The knob is `ENABLE_TOOL_SEARCH` in settings env
(`true` / `auto` / `auto:N` / `force`); it is effectively already on.  This
lever is spent.

#### Conclusion — S cannot be cut enough to change the cutoff decision

Roughly **75% of S is CLI floor we do not control**, and it grows ~1.3k per
release.  A perfect job on everything we do own — halve the project
CLAUDE.md, trim the session prompt — saves on the order of 8k out of 65k.
That does not move a 145k cutoff from "loses to churn" to "wins".

So:

1. **The cutoff stays at 500.  Permanently, not provisionally.**  §3's
   experiment is not merely un-repeatable, it is un-winnable on this axis.
2. The only remaining lever on context cost is **δ — context growth per
   request** — i.e. keeping grind output out of the parent.  That is
   delegation (§5), and it gets *more* valuable every CLI release as the
   floor rises.
3. Trimming the 35 KB project CLAUDE.md is still worth doing (~4k/session
   for a 50% cut, on every session forever), but as housekeeping, not as a
   strategy.

## 5. How delegation fits under a context-weighted model

Delegation was justified on request counts.  Under context weighting the
justification is stronger and the mechanism is different:

- The win is not mainly "one request instead of N".  It is that **the
  grind output never enters the parent's context at all** — raw dmesg
  rings, board tables, grep sweeps land in a sonnet subagent's context
  instead.
- That lowers `δ`, the parent's context growth per request, which
  **extends the work span inside the unchanged 500k cutoff**.  Same
  cutoff, more work per session, fewer restarts — it attacks the churn
  problem from the side the cutoff experiment could not.
- It also moves those requests off the binding Fable bucket (§1).

The chain-length distribution (`scripts/delegate_chain_distribution.py`,
5 mxfs Fable sessions, 721 requests, 95 chains) shows 6 chains of length
11-38 carrying 166 requests.  Those long chains are simultaneously the
biggest request sink and the biggest context inflator, so targeting long
chains is correct on both metrics.

## 6. Hook mechanics relevant to any enforcement (verified in 2.1.239)

- The base hook payload carries `agent_id` and `agent_type`.  The CLI's own
  schema says: *"Use this field (not agent_type) to distinguish subagent
  calls from main-thread calls"* — `agent_type` is also present on the main
  thread of a session started with `--agent`.  **Gate subagent
  pass-through on `agent_id`.**
- `PreToolUse` output supports `permissionDecision` + `permissionDecisionReason`
  (blocking) *and* `additionalContext` (advisory, non-blocking).  An
  advisory nudge costs no extra request — it rides on a call that already ran.
- A **denied** tool call has already cost its request.  Denying at chain
  position N and forcing an `Agent` call saves `max(0, L - (N+1))` for a
  chain of length `L`, and loses `(N+1) - L` when the chain would have
  ended on its own.
- `SubagentStart` is a real hook event with per-agent-type matchers
  (`SubagentStart:<agentType>`) and accepts `additionalContext`.
- ccloop's existing hooks self-gate on `CCLOOP_RUN_ID` and are no-ops
  elsewhere (`ccloop/src/ccloop/install.py` docstring).  Any machine-wide
  hook deliberately breaks that contract and must say so.

## 7. Standing conclusions

1. Do not lower `--cutoff` again until startup context `S` has been
   measurably reduced.  The experiment has been run.
2. Denominate future cost work in context-weighted terms; treat request
   counts as a proxy and say so.
3. Attribute the remaining ~70k of startup context before optimising
   anything else — it is the lever with no downside.
4. Delegation stays worth doing, for the context reason above and the
   bucket reason in §1.
