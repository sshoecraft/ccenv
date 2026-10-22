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
  - `count_backlog(memory_dir)` → `{backlog, retained, behavior_backlog,
    merge_backlog, total_raw, has_compiled, threshold, since_compiled}`.
    `merge_backlog` counts the first-tier articles the merge groups would
    absorb (see "Why articles merge"). Backlog = raw memories no `compiled-*` article
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
    `plan_wave_size()` reads `CCMEMORY_COMPILE_WAVE` (default 6);
    `article_max_bytes()` reads `CCMEMORY_ARTICLE_MAX_BYTES` (default 24576);
    `plan_ttl()` reads `CCMEMORY_COMPILE_PLAN_TTL` (default 3600 s);
    `claim_lease()` reads `CCMEMORY_COMPILE_CLAIM_LEASE` (default 1800 s);
    `agent_groups()` reads `CCMEMORY_COMPILE_AGENT_GROUPS` (default 6).
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

**One plan per run, stored.** `compaction_plan` computes the partition once,
writes it to `compaction-plan.json` in the memory dir, and serves it from there
until every group in it is done or it is an hour old (`CCMEMORY_COMPILE_PLAN_TTL`).
Each call drops what is finished: a note an article folds, a source or absorbed
article that has been deleted. A recomputed plan cannot do this, because every
pass changes the store and with it the BM25 scores the clustering runs on. On
mxfs's first merge run that shifted groups under running agents. Two agents were
told their seed was `done` before doing anything, and one group named an article
another group had absorbed half a second earlier. Notes written mid-run wait for
the next plan.

**Agents claim groups; the session does not hand them out.** The session
dispatches `wave` agents once. Each calls `memory_compaction_plan(take=true)`,
which returns the next group no one has claimed and claims it with an `O_EXCL`
file under `compaction-claims/<plan id>/`. Each agent stops at `done` or after
`agent_groups` groups (default 6, what fits one agent's context). A claim older
than `CCMEMORY_COMPILE_CLAIM_LEASE` (30 min) belonged to an agent that never
finished, and is taken over by renaming it aside, which only one taker can do.
Before this, one agent ran per group and the session had to launch the next as
each finished. A session busy with the user's work stops doing that: mxfs's
dispatched 13 of 28 groups and never launched another. The plan file and claims
are run state and are gitignored.

Groups are still addressed by **seed slug**: `memory_compaction_plan(seed=...)`
returns one group, for the inline skill or a caller naming a group, and `done`
when nothing in it is outstanding.

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

`memory_delete` is the only deletion path. It refuses any note
`folded_names()` does not report, so a behavior note cited by a `project`
article (which cannot represent it in the first listing tier) cannot be deleted
on that article's strength. It refuses a `compiled-` article except as the
absorbed half of a merge (below). Links to a deleted note become plain
`` `slug` `` text so the citation survives as provenance.

Existing articles were written as summaries, so they were never checked
against their sources. `compaction_plan` hands each one out as a `backfill`
group: its compactor re-reads the article and its sources, folds back
whatever the summary dropped, rewrites the article under the same name, and
then deletes. Where the project has git, the history keeps every deleted note.
Where it has none, deletion is permanent, which is why verification comes
first.

## Why articles merge

Compaction turns notes into articles and, without merging, never touches an
article again. In the knowledge pool that is fine: articles have their own
listing tier, and raw notes are what compete for the budget. In the behavior
pool it is not. Feedback articles stay in the first tier, and every pass adds
more of them, so a long-lived project eventually fills that tier with nothing
but compiled articles. mxfs reached 120 of them: 8,339 tokens against a
4,925-token share, with no raw note left to compact.

`merge_groups` is the drain. It runs only while `behavior_excess` is positive,
meaning the first tier costs more than `LIST_TIER_SHARES[0]` of the entry
budget. Once lower tiers have taken their share, the listing hands unused
budget back up, so an excess does not withhold anything yet. It is budget the
lower tiers are owed, and it comes due as they grow.

- **A merge keeps one article and absorbs others.** The seed (`article`) is
  rewritten in place with every absorbed article folded in, as losslessly as a
  note is, and each absorbed article is deleted.
- **Topic first.** Members come from the same BM25 clustering as note groups.
  Leftovers that cluster with nothing are pooled into `assorted` merges, and
  only when the topical merges cannot clear the excess on their own.
- **Only what clears the excess.** Each merge leaves one description in the
  listing pointing at what several used to, so the plan stops once the
  absorbed articles' listing cost covers the excess.
- **Bounded size.** A merge's inputs are packed to the article ceiling (below)
  over `MERGE_GROWTH` (1.10). A merged article comes out larger than its
  inputs: 2% to 9.3% on mxfs's first merges. That margin keeps a merge from
  running into the ceiling at write time.

## The article size ceiling

`memory_get` returns a whole article, so an article's size is what every fetch
of it costs. `CCMEMORY_ARTICLE_MAX_BYTES` (default 24 KiB) is the ceiling. That
is the largest article mxfs's compactors had produced before merging existed.

It is enforced where articles are written: `memory_write` refuses a
`compiled-` article over the ceiling, and the refusal tells the writer to split
it by subtopic into articles of the same type, each citing the inputs whose
facts it holds. An earlier ceiling bounded only a merge's inputs. Two merges
came out at 25,833 and 26,729 bytes, and backfill and note groups had no bound
at all. No path can write an oversized article now, whichever group produced
it. A compactor that cannot split leaves its sources undeleted, because
deletion follows a verified write.

Articles that predate the ceiling are handed out as `kind: split` groups, after
everything else in the plan, one per oversized article. The exception is an
article a backfill owns, whose rewrite is refused and split anyway. A split
deletes nothing. The article keeps part of its content under its own name,
the rest goes to new articles, and each `[[slug]]` citation moves with the
facts it backs, so every folded source stays folded. A split group is done
once its article is under the ceiling.
- **No article is in two jobs.** An article that owns a backfill group, or was
  written inside the cooldown window, is never a merge candidate.

Articles are rewritten in place, so a merge can race another compactor. Say
merge A reads article X while compactor B is growing X. If A then deleted X,
everything B folded into it would be gone. The plan therefore hands each
absorbed article's content hash to its compactor, and `Store.check_merged`
refuses the delete unless the article still has that hash. It also requires
that another article of a representing type cites it, which is the same
retirement test a raw note passes. A refused delete leaves a duplicate, which
the next plan merges again. Nothing is lost.

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
