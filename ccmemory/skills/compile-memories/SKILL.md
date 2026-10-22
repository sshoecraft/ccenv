---
name: compile-memories
description: >
  Compact a project's raw ccmemory notes into a dense, deduplicated, cross-referenced
  `compiled-<topic>` knowledge article — running inline in THIS session. Normally the
  "📦 Memory compaction available" nudge is handled by dispatching background
  `memory-compactor` agents, not by this skill. Use this skill when: the user says "compile
  memories", "compact memory", "the memories are cluttered/piling up", "densify my notes",
  "consolidate memory", "clean up ccmemory" and wants it done in this session; or the
  `memory-compactor` agent is unavailable when a nudge fires. Do not run it unprompted on
  every session.
---

# Compile memories (inline compaction)

Raw per-session ccmemory notes accumulate faster than they get curated. This skill folds a
batch of related raw memories into ONE dense article so the index stays useful. It runs in
the current session using the ccmemory MCP tools, so it is ordinary session usage.

## This is the fallback path

The normal path is the `memory-compactor` subagent, dispatched in the
background so the user is never left waiting on maintenance — `wave` agents,
dispatched once, each claiming groups from the stored compaction plan until
none are left:

    wave times, once:
        Agent(subagent_type="memory-compactor", prompt="Compact memory groups from the plan.")

Use this skill instead only when that agent is unavailable, or when the user
explicitly asks to compact in the current session. Doing the work inline costs
the session a stop-the-world read of every memory body in the batch — which is
precisely why, before the agent existed, 29 of 30 project memory dirs on this
machine had never been compacted at all.

Fire `wave` agents, not one. One agent works at most `agent_groups` groups and
stops; a backlog in the hundreds then needs dozens of sessions while ordinary
use keeps adding notes, and it never converges. The stored plan and its claims
exist so the whole backlog can be drained from one dispatch without two agents
landing on the same notes.

## When to run it

Run when the user asks for inline compaction, or when a "📦 Memory compaction available"
nudge fires and the `memory-compactor` agent is not available. Do NOT run it speculatively
every session.

To inspect the backlog and candidate inputs first (optional): `ccmemory compile` (and
`ccmemory compile --topic "<topic>"`). That command no longer calls any LLM — it just
reports `backlog`, `threshold`, and `candidate_names`. `ccmemory compile --plan` (or the
`memory_compaction_plan` tool) reports the group partition instead.

## Procedure

1. **Get the plan.** Call `memory_compaction_plan()`. It partitions every uncompiled
   `project`/`reference` note into disjoint groups, each addressed by a `seed` slug, and
   covers the whole backlog — `kind: assorted` groups are the tail that did not cluster
   onto a topic. Do not hand-pick clusters out of `memory_list()`: that is what made two
   concurrent compactors converge on the same notes.

2. **Take one group per pass.** Claim each with `memory_compaction_plan(take=true)`, one
   article per group, until it returns `done` or the backlog is under `threshold`. Claiming
   keeps you off groups a background compactor already holds. Compile an `assorted` group too — say in its opening line
   that it is a mixed batch. If the user named a topic, `memory_compaction_plan(seed=...)`
   fetches just that group.

3. **Read the bodies.** `memory_get(name)` for each `names` entry in the group. Read them fully —
   you are deduplicating and synthesizing, so you need the actual content, not just
   descriptions.

4. **Synthesize** ONE article following these exact rules:

   > You are merging raw memory files into a single knowledge article that REPLACES
   > them: once it is verified, the inputs are deleted, so anything the article leaves
   > out is lost. Read the inputs. Produce ONE markdown article that:
   >
   > 1. Identifies the central topic the inputs share.
   > 2. Keeps every concrete fact from every input — decisions, lessons, failure modes,
   >    and the specifics behind them: exact quotes, numbers, versions, paths, commands,
   >    error text, measured results, and who said what. Deduplicate only where two
   >    inputs say the same thing; never trade a specific for a summary of it. Order
   >    chronologically when timing matters.
   > 3. Cross-references the source memories using their literal slugs as wikilinks
   >    (e.g. `[[pythonuserbase-in-zshenv]]`). MANDATORY — cite EVERY input you
   >    folded in, with its exact slug. These wikilinks are the retirement record:
   >    `memory_list` omits a raw note precisely because a compiled article cites
   >    it, and `count_backlog` counts a note as compiled precisely when it is
   >    cited. An input you fold in but do not cite stays in the backlog and keeps
   >    costing every session tokens forever.
   > 4. Is terse in its prose, never in its facts. Engineering prose, no platitudes, no
   >    headers like "## Summary".

5. **Write it** with `memory_write`:
   - `name`: `compiled-<short-kebab-topic>` (the `compiled-` prefix is REQUIRED — it marks
     the article as compiled so the backlog nudge resets and future compiles skip it).
   - `type`: the group's `article_type`, copied from the plan — `feedback` for a group of
     `user`/`feedback` notes, `project` otherwise. This is not a judgement call. A behavior
     group's article has to sit in the same first-claim listing tier as the corrections it
     retires; write `project` on one and those notes are folded away behind a
     representative the budget is allowed to drop, which is worse than not folding them.
   - `description`: one-line summary suitable for the index (≤150 chars).
   - `tags`: include `compiled` plus a few topic tags.
   - `body`: the synthesized article.

   `memory_write` writes `compiled-<topic>.md` at the memory-dir root and reindexes, so the
   article is searchable immediately and the Stop hook regenerates `MEMORY.md`.

6. **Verify, then delete the sources.** The article replaces its inputs. Re-read the
   article you wrote (`memory_get`) and, for each input, check that every concrete fact it
   held is in the article: quotes, numbers, versions, paths, commands, measured results. If
   one is missing, add it and rewrite the article before going on. Then
   `memory_delete(name)` each verified input. An input you cannot verify stays — do not
   delete it. Citing is still required: `memory_delete` refuses a note no article folds.

   A `kind: backfill` group is an existing article (`article`) whose sources are still on
   disk. Read the article and every source, fold back whatever the article dropped, rewrite
   it with `memory_write` under the same `name` and `type`, then verify and delete exactly
   as above.

   A `kind: merge` group is an existing first-tier article (`article`) and the other
   `compiled-` articles it absorbs (`names`). The plan emits these only when the first
   listing tier costs more than its share of the budget. Read every one in full, fold each
   absorbed article into `article`, and rewrite it with `memory_write` under the same `name`
   and `type`, with a description covering all of them. Then verify, and delete each
   absorbed article with `memory_delete(name, expect_hash=hashes[name])`. If that is refused
   because the article changed after the merge read it, someone else has rewritten it since:
   leave it on disk.

   A `kind: split` group is one existing article over the size ceiling
   (`CCMEMORY_ARTICLE_MAX_BYTES`, default 24 KiB). Divide it by subtopic: one part keeps its
   name, the rest become new `compiled-` articles of the same type, and every `[[slug]]`
   citation goes with the facts it backs. Nothing is deleted.

   `memory_write` refuses any `compiled-` article over the ceiling. When it does, split that
   article the same way before going on.

7. **Report** to the user: which raw memories you folded in and deleted, any you kept and
   why, the article name, and a one-line description. If the backlog is still over `threshold`, keep going — inline
   compaction that stops after one group leaves the rest to the next session, and that is
   exactly how a backlog reaches the hundreds.
