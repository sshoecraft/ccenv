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
background so the user is never left waiting on maintenance — one agent per
group in the compaction plan, `wave` of them at a time:

    plan = memory_compaction_plan()        # disjoint groups, addressed by seed slug
    for each group, wave at a time:
        Agent(subagent_type="memory-compactor", prompt="Compact memory group seeded by `<seed>`.")

Use this skill instead only when that agent is unavailable, or when the user
explicitly asks to compact in the current session. Doing the work inline costs
the session a stop-the-world read of every memory body in the batch — which is
precisely why, before the agent existed, 29 of 30 project memory dirs on this
machine had never been compacted at all.

Fire one agent per group, not one agent. One agent folds in roughly a dozen
notes and stops; a backlog in the hundreds then needs dozens of sessions while
ordinary use keeps adding notes, and it never converges. The plan exists so the
whole backlog can be drained in a single session without two agents landing on
the same notes.

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

2. **Take one group per pass.** Work the groups in order, one article per group, until the
   backlog is under `threshold`. Compile an `assorted` group too — say in its opening line
   that it is a mixed batch. If the user named a topic, `memory_compaction_plan(seed=...)`
   fetches just that group.

3. **Read the bodies.** `memory_get(name)` for each `names` entry in the group. Read them fully —
   you are deduplicating and synthesizing, so you need the actual content, not just
   descriptions.

4. **Synthesize** ONE article following these exact rules:

   > You are compiling raw per-session memory files into a single dense knowledge
   > article. Read the inputs. Produce ONE markdown article that:
   >
   > 1. Identifies the central topic the inputs share.
   > 2. Extracts every decision, lesson, and recurring failure mode — deduplicated
   >    and chronologically ordered when timing matters.
   > 3. Cross-references the source memories using their literal slugs as wikilinks
   >    (e.g. `[[pythonuserbase-in-zshenv]]`). MANDATORY — cite EVERY input you
   >    folded in, with its exact slug. These wikilinks are the retirement record:
   >    `memory_list` omits a raw note precisely because a compiled article cites
   >    it, and `count_backlog` counts a note as compiled precisely when it is
   >    cited. An input you fold in but do not cite stays in the backlog and keeps
   >    costing every session tokens forever.
   > 4. Is terse. Engineering prose, no platitudes, no headers like "## Summary".

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

6. **Do NOT delete the raw memories.** The compiled article is additive — the raw notes stay
   as the source of truth, fully searchable via `memory_search` / `memory_get`. What changes
   is that a cited note is no longer listed by `memory_list` (the article represents it), so
   compaction is what actually reduces per-session listing cost. Citing is the whole
   mechanism: an uncited note is neither retired from the listing nor cleared from the
   backlog, no matter how thoroughly you folded its content in.

7. **Report** to the user: which raw memories you folded in, the new article name, and a
   one-line description. If the backlog is still over `threshold`, keep going — inline
   compaction that stops after one group leaves the rest to the next session, and that is
   exactly how a backlog reaches the hundreds.
