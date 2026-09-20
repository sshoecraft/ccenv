# Memory compaction (compile-memories)

## What it does

Raw per-session ccmemory notes (one fact per `.md` file) pile up over time.
Compaction folds a batch of related raw notes into ONE dense, deduplicated,
cross-referenced `compiled-<topic>` article so the index stays useful. The raw
notes stay as the source of truth — the compiled article is additive.

## Architecture

Three pieces, no LLM subprocess:

- **`ccmemory/compile.py`** — backlog detection + partitioning + candidate
  selection + the shared `COMPILER_PROMPT`. No LLM call.
  - `count_backlog(memory_dir)` → `{backlog, total_raw, has_compiled,
    threshold, since_compiled}`. Backlog = raw memories no `compiled-*` article
    has *folded* — for `project`/`reference` that means cited at all, for
    `user`/`feedback` it means cited by an article that is itself always-listed
    (`Store.folded_names`). Every type is compilable; a type with no drain
    grows forever.
  - `compaction_plan(memory_dir, size=, seed=)` → the whole backlog partitioned
    into disjoint groups, each `{seed, kind, size, names, pool, article_type}`.
    One group is one compactor run. Groups never span the behavior
    (`user`/`feedback`) and knowledge (`project`/`reference`) pools: the group
    decides the article's single `metadata.type`, and a behavior note folded
    into a `project` article would be represented in a tier that can be
    budget-trimmed.
  - `compile_status(memory_dir, topic, max_inputs)` → backlog + candidate input
    names + a `how` pointer. Read-only; this is what `ccmemory compile` prints.
  - `threshold()` reads `CCMEMORY_COMPILE_THRESHOLD` (default 20);
    `plan_group_size()` reads `CCMEMORY_COMPILE_GROUP_SIZE` (default 12);
    `plan_wave_size()` reads `CCMEMORY_COMPILE_WAVE` (default 6).
  - A `compiled-` **name prefix** marks an article as compiled. `memory_write`
    has no subdir support, so compiled articles live at the memory-dir root, not
    in a `compiled/` subdirectory.
- **`ccmemory/hooks.py` → `session_handler`** — appends a nudge to the
  SessionStart `additionalContext` when `backlog >= threshold`, naming the plan's
  group seeds and the fan-out recipe **for when the user asks for it**. It does
  not ask for the work: it arrives before the first user message, so "dispatch
  these and carry on" has nothing to carry on with and the session ends up
  waiting on agents instead of answering. Fail-open (`_compaction_nudge`
  swallows errors → no nudge). Under threshold it injects nothing.
- **`agents/memory-compactor.md`** (top-level ccenv, installed to
  `~/.claude/agents/`) — the background worker. Takes a seed slug, fetches its
  own group via `memory_compaction_plan`, compiles that group and nothing else.
- **`skills/compile-memories/SKILL.md`** — the same procedure run inline by the
  interactive session, for when the agent is unavailable. Synthesizes per
  `COMPILER_PROMPT`, writes via `memory_write` (`name: compiled-<topic>`,
  `type:` the group's `article_type`, `tags: [compiled, ...]`). Installed to
  `~/.claude/skills/compile-memories/` by the top-level `install.sh`.

## Why the backlog is partitioned

A compactor that picks its own cluster out of `memory_list()` can only be run
one at a time: two started together see the same listing and converge on the
same obvious cluster, producing two articles about the same notes. One agent
per session folds in roughly a dozen notes, which is at or below the rate at
which ordinary use writes new ones — so a store that falls behind never catches
up, and the partitioning has to be done by hand before any fan-out is possible.

`compaction_plan` does that partitioning deterministically and without an LLM:
greedy clustering over the existing BM25 index, newest unclaimed note as seed,
its best-matching unclaimed topic-mates pulled in up to `group_size`.

Two properties make the result safe to fan out:

- **Disjoint** — no note is in two groups, so concurrent agents never collide.
- **Total coverage** — every uncompiled note is in some group. Clusters below
  `MIN_CLUSTER` are pooled into `kind: assorted` groups rather than dropped. An
  article that lumps loosely related notes together reads worse than a focused
  one, but a note left out of every plan can never be cited, never retires from
  the listing, and puts a permanent floor under the backlog.

Groups are addressed by **seed slug, never by index**. The plan is recomputed
from whatever is still uncompiled, so indices shift as concurrent agents retire
notes; a seed does not. A seed that no longer appears in the plan means that
group is already compiled, and the agent holding it is told to write nothing
rather than go take someone else's group.

`CLUSTER_FLOOR` is what keeps a group a topic rather than "the twelve notes
with the most words in common": a candidate must score at least that fraction
of the seed's BM25 match against its own text. Note that BM25 weights a term by
rarity, so on a small store nothing discriminates and every group comes out
`assorted` — that is correct behaviour, not a failure.

## Two-layer "when to use"

A skill with no trigger never gets invoked, so compaction has two triggers that
both reference the same threshold:

1. **Active push** — the SessionStart hook nudge, fired off the live backlog count.
2. **Passive trigger** — the skill's own description lists trigger phrases so it
   auto-activates when the user asks or the nudge appears.

## Why no `claude -p`

The original `compile.py` shelled out to a headless `claude -p` subprocess.
Anthropic is moving the Agent SDK / `claude -p` / Claude Code GitHub Actions off
subscription usage onto a separate metered monthly credit pool (full API rates,
no rollover), so every compile run would burn that credit. Compaction now runs in
the live INTERACTIVE session (unaffected by the change) — zero `claude -p`, zero
metered credit, full LLM-quality synthesis.

## Why count the backlog, not the total

Compiled articles are additive — they never delete the raw notes. A naive
`total > N` check would fire the nudge forever once crossed, even right after
compacting. Counting raw memories that no compiled article **cites** makes the
nudge self-resetting: the wikilinks a compile pass writes are the retirement
record, so compiling drops the backlog toward zero until new notes accumulate,
and it never goes quiet about notes that were genuinely skipped.

## History

- v0.18.0 — automation restored, without `claude -p`. The nudge now dispatches
  the `memory-compactor` subagent (sonnet, background, subscription-billed via
  the Agent tool) instead of asking the session to compact inline. Measured
  cause: of 30 project memory dirs on the dev box, only `/src/mxfs` — the one
  running unattended under ccloop — had ever produced a compiled article. Every
  interactive project sat at zero, because the inline ask always lost to
  whatever the user was waiting on. Added `CCMEMORY_COMPILE_COOLDOWN` (default
  900s) so concurrent sessions do not all dispatch a compactor for the same
  notes. The skill remains as the fallback path.
- v0.10.0 — removed the `claude -p` path; added `compile-memories` skill,
  backlog-threshold SessionStart nudge, and read-only `ccmemory compile` status.
  Previously (`v0.9.0` and earlier) `compile.py` ran `claude -p` and the
  `ccmemory compile` CLI produced the article directly.
