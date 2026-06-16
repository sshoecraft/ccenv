---
name: compile-memories
description: >
  Compact a project's raw ccmemory notes into a dense, deduplicated, cross-referenced
  `compiled-<topic>` knowledge article — running entirely in THIS interactive session
  (no `claude -p`, no metered Agent-SDK credit). Use this skill whenever: a SessionStart
  nudge reports uncompiled memories over threshold ("📦 Memory compaction available"); the
  user says "compile memories", "compact memory", "the memories are cluttered/piling up",
  "densify my notes", "consolidate memory", "clean up ccmemory"; `ccmemory compile` /
  `memory_stats` shows a large backlog; or you notice many overlapping raw memories on one
  topic. This is OCCASIONAL maintenance — do not run it unprompted on every session; run it
  when a trigger above fires. It replaces the old `ccmemory compile` `claude -p` path, which
  was removed because headless `claude -p` now bills against a separate metered credit pool.
---

# Compile memories (interactive, zero-cost compaction)

Raw per-session ccmemory notes accumulate faster than they get curated. This skill folds a
batch of related raw memories into ONE dense article so the index stays useful. It runs in
the current interactive session using the ccmemory MCP tools — it never shells out to
`claude -p`, so it costs nothing beyond normal subscription usage.

## When to run it

Run when a trigger fires (a SessionStart "📦 Memory compaction available" nudge, the user
asking, or an obviously large/overlapping backlog). Do NOT run it speculatively every
session — compaction is deliberate maintenance, not a background habit.

To inspect the backlog and candidate inputs first (optional): `ccmemory compile` (and
`ccmemory compile --topic "<topic>"`). That command no longer calls any LLM — it just
reports `backlog`, `threshold`, and `candidate_names`.

## Procedure

1. **Survey.** Call `memory_list()` to get every memory (name, type, description, age).
   Ignore any already named `compiled-*` — those are prior articles, not raw inputs.

2. **Pick a topic batch.** Group the raw memories by shared subject and choose ONE cohesive
   cluster (typically 3–20 notes). If the user named a topic, use `memory_search("<topic>")`
   to gather the cluster. Compile one topic per invocation; repeat for others.

3. **Read the bodies.** `memory_get(name)` for each memory in the batch. Read them fully —
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
   >    (e.g. `[[pythonuserbase-in-zshenv]]`).
   > 4. Is terse. Engineering prose, no platitudes, no headers like "## Summary".

5. **Write it** with `memory_write`:
   - `name`: `compiled-<short-kebab-topic>` (the `compiled-` prefix is REQUIRED — it marks
     the article as compiled so the backlog nudge resets and future compiles skip it).
   - `type`: `project`
   - `description`: one-line summary suitable for the index (≤150 chars).
   - `tags`: include `compiled` plus a few topic tags.
   - `body`: the synthesized article.

   `memory_write` writes `compiled-<topic>.md` at the memory-dir root and reindexes, so the
   article is searchable immediately and the Stop hook regenerates `MEMORY.md`.

6. **Do NOT delete the raw memories.** The compiled article is additive — the raw notes stay
   as the source of truth. Writing the `compiled-<topic>` article is what quiets the backlog
   nudge (it counts raw memories newer than the most recent compiled article).

7. **Report** to the user: which raw memories you folded in, the new article name, and a
   one-line description. Offer to compile another topic cluster if the backlog is still high.
