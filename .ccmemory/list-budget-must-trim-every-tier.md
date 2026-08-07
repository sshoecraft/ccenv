---
name: list-budget-must-trim-every-tier
description: ccmemory 0.19.0: a budget exempting a type from BOTH trimming and folding is not a budget. reference was write-only for 160 entries on mxfs.
metadata:
  type: project
tags: [ccmemory, memory_list, compaction, invariant]
---

## The failure

`memory_list` on mxfs (1,848 memories) shipped ~14.9k tokens against a
6,000-token budget while `memory_stats` reported 10,490 — and the listing held
**zero** project notes and **zero** compiled articles. 180 entries, all
`reference`/`feedback`/`user`. Paid before the user's first message every
session, and again on every ccloop relay.

## Why (four interacting defects, not one)

1. `Store.list_all` seeded `spent` with the full always-listed set and then
   trimmed only the remainder. Once those types alone exceeded the budget, the
   loop broke on the first project note — the budget capped nothing AND starved
   every other tier.
2. `reference` was in `ALWAYS_LIST_TYPES` (exempt from folding) while
   `compile._select` ingested only `type='project'`. **Nothing in the system
   could retire a reference memory at any point, ever.** Write-only type.
3. `compiled-` articles competed with raw notes on mtime alone → 2 of 132
   listed. Folding retired 1,494 notes in favour of articles that were then
   withheld. The session saw neither.
4. `_entry_tokens` modelled `name+description+30`; the server shipped
   `json.dumps(indent=2)`. Under-counted 1.42x, and `memory_stats` inherited it
   — the field you'd consult to detect the problem hid it.

Plus: `count_backlog` counted every type while only `project` was actionable →
mxfs had a backlog floor of 144 against a threshold of 20. The compaction nudge
fired every session and **could not be silenced by compacting**.

## Invariants

- **A budget must be able to trim every tier, including the first.** An
  un-trimmable tier makes the budget advisory, and advisory budgets get blown.
- **Keep `ALWAYS_LIST_TYPES` small.** Every type in it is a type nothing can
  ever retire. It is now `("user","feedback")` — behavior and corrections,
  which genuinely have no other retrieval path. `reference` is a durable fact;
  BM25 search reaches it.
- **`ALWAYS_LIST_TYPES` and `compile.COMPILABLE_TYPES` must stay
  complementary.** A type in neither can be neither retired from the listing
  nor drained from the backlog. Enforced by
  `test_compilable_and_always_listed_types_are_complementary`.
- **An unsilenceable alarm is a broken alarm.** Count backlog only over what a
  compile pass can actually ingest.
- **Estimator and serializer drift silently.** If `_entry_tokens` stops
  matching what `mcp_server` emits, the budget quietly stops meaning anything.
  `test_entry_tokens_tracks_real_wire_size` pins them together.

## Diagnostic method that worked

Do not trust a component's self-reported cost. `memory_stats.list_tokens_actual`
said 10,490; `len(json.dumps(payload))` said 14,921. Measure the bytes that
actually ship. Probes live in `tests/measure_list_payload.py` and
`tests/simulate_list_policy.py` (the latter opens `index.db` `mode=ro` and
simulates policy changes against real stores before writing any code — that is
what surfaced defect 3, which was not in the original diagnosis).

## Not a defect, checked and cleared

33 rows on mxfs have a frontmatter `name:` that differs from their filename
(legacy prose-titled notes, e.g. `feedback_commits.md` → `name: Commits only
when work is done/working`). The index keys on frontmatter `name`, and `path`
is stored, so `memory_get` resolves. 0 dead paths, 0 unindexed files.
