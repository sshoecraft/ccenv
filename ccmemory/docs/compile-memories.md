# Memory compaction (compile-memories)

## What it does

Raw per-session ccmemory notes (one fact per `.md` file) pile up over time.
Compaction folds a batch of related raw notes into ONE dense, deduplicated,
cross-referenced `compiled-<topic>` article, then deletes the notes it folded.
The article is a lossless merge, not a summary: it replaces its sources, so it
keeps every concrete fact they held. Each source is deleted only after the
compactor re-reads the article and finds that source's content there.

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
  group seeds and asking the session to dispatch one background
  `memory-compactor` per group and carry straight on, without waiting. The
  `memory_list` note carries the same request for sessions the hook did not
  reach. Fail-open (`_compaction_nudge` swallows errors → no nudge). Under
  threshold or inside the cooldown it injects nothing.
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

1. **Active push** — the SessionStart nudge and the `memory_list` note, fired
   off the live backlog count, which dispatch the background compactors.
2. **Passive trigger** — the skill's own description lists trigger phrases so it
   activates when the user asks for compaction in-session.

## Why no `claude -p`

The original `compile.py` shelled out to a headless `claude -p` subprocess. It
was removed on the premise that `claude -p` was moving to a separate API-rate
credit pool; that change was paused before it took effect, and `claude -p`
draws from subscription usage limits. The subprocess is still not needed: the
session that sees the backlog is already a model, and a background Agent-tool
subagent does the synthesis without a second process, a second CLI
invocation, or any wait in the caller.

## Why count the backlog, not the total

A naive `total > N` check counts articles and every note a pass has not yet
deleted, so it does not reflect what is left to do. Counting raw memories that
no compiled article **cites** makes the nudge self-resetting: the wikilinks a
compile pass writes are the retirement record, so compiling drops the backlog
toward zero until new notes accumulate, and it never goes quiet about notes
that were genuinely skipped. Folded notes still on disk are counted separately
as `retained`, and the nudge fires on the sum, so a store that is fully folded
but never cleaned up still gets its backfill.

## Why the sources are deleted

Compaction used to be additive: the raw notes stayed "as the source of truth"
and the article was an extra, denser entry. That made every pass grow the
store. On mxfs, 638 of 650 raw notes sat beside the 101 articles that already
held them. If an article is faithful, its sources are a second copy; if it is
not, the article is what needs fixing. So the article is written to replace
its sources, and verification happens before deletion, per source.

`memory_delete` is the only deletion path. It refuses `compiled-` articles and
any note `folded_names()` does not report, so a behavior note cited by a
`project` article (which cannot represent it in the first listing tier) cannot
be deleted on that article's strength. Links to a deleted note become plain
`` `slug` `` text so the citation survives as provenance.

Existing articles were written as summaries, so they were never checked
against their sources. `compaction_plan` hands each one out as a `backfill`
group: its compactor re-reads the article and its sources, folds back
whatever the summary dropped, rewrites the article under the same name, and
then deletes. Where the project has git, the history keeps every deleted note.
Where it has none, deletion is permanent, which is why verification comes
first.

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
