# `memory_list` bounding and citation-based folding

## The problem

`memory_list()` is the mandatory first action of every session
(`hooks.py`, `SESSION_PROTOCOL`). Until v0.14.0 it had no cap of any kind —
`Store.list_all()` selected every row and returned all of it.

Measured against `/src/mxfs/.ccmemory` on 2026-08-03 (1,695 memories, 8.2 MB
of markdown):

| | |
|---|---|
| `memory_list()` payload | 684,407 chars ≈ **171,000 tokens** |
| share of a 200k context window | **85.6%** |
| `MEMORY.md` (capped at `DEFAULT_FILE_CAP`) | 12 KB |
| `index.db` | 15.2 MB |
| `reindex()` per tool call | 1.7 s |
| peak RSS | 25 MB |

RAM was never the issue. The cost is context, it is paid before the user's
first message, and under ccloop every relayed session pays it again.

Three compounding causes:

1. **No cap.** `index_gen.py` documents this exact bug class in its own
   docstring ("52KB index loaded every session… Fix: … a hard cap") and fixed
   it for `MEMORY.md`. The larger surface never got the same treatment.
2. **The protocol pointed at the uncapped surface.** It told the model not to
   read `MEMORY.md` (which is capped) and to call `memory_list` instead
   (which was not), while asserting the call "is cheap". True at 29 memories.
   False by two orders of magnitude at 1,695.
3. **Compaction ran but retired nothing.** That store had 120 `compiled-*`
   articles citing 1,144 of its 1,575 raw memories — and every one of those
   raw memories was still listed at full cost. `compile.py` states the design:
   the raw inputs stay, the article is purely additive. So each compile pass
   made `memory_list` *bigger*. It reads as "compaction never happens"; it
   happened 120 times and the payoff was designed out.

## The mechanism

The store already knew what had been folded. A compile pass wikilinks the
inputs it folds, `Store._upsert` records every wikilink in `mem_edges`, and
nothing read that table for this purpose.

`Store.cited_names()` returns every raw memory cited by a `compiled-` article.
`Store.folded_names()` applies listing policy on top: cited **and** not
load-bearing. `list_all()` omits those, then fills a token budget.

Nothing is deleted, moved or rewritten. A folded memory stays on disk, stays
indexed, and stays reachable through `memory_search`, `memory_get`, the
PreToolUse auto-injection, and `memory_list(include_folded=true)`. Only the
default listing changes.

### Measured effect on the same store

| stage | entries | tokens |
|---|---|---|
| before | 1,695 | 171,101 |
| drop `path` from entries | 1,695 | 98,641 |
| + omit raws cited by a `compiled-` article | 553 | 30,855 |
| + `CCMEMORY_LIST_TOKEN_BUDGET` = 6000 | 123 | **5,961** |

`path` was 43% of the payload and unusable — `memory_get` keys on name.

### What is never withheld

`Store.ALWAYS_LIST_TYPES` (`user`, `feedback`, `reference`) plus untyped
memories are never folded and never budget-trimmed — one predicate,
`Store._is_always_listed`, governs both. These carry behavior, conventions,
preferences and durable facts, and the PreToolUse auto-injection only fires on
a file Read, so nothing else surfaces them. On the mxfs store that is 90
entries out of 1,695: keeping all of them always is affordable, and it is
precisely what the mandatory session-start call exists for.

Truncation is never silent. `list_all` returns explicit
`total` / `shown` / `folded` / `withheld` counts, and `mcp_server._list_note`
states what was withheld and how to reach it.

## Backlog counting

`count_backlog` used to define the backlog as raw memories *newer than the
most recent compiled article* — a heuristic assuming each pass covers
everything older than itself. On the mxfs store it reported **249** while
**432** memories had never been cited by any article. Those 183 were older
than the newest article but had never actually been folded into one, so they
were invisible to the nudge permanently.

It now counts uncited memories from `mem_edges`. This is exact, and it still
quiets down after compaction, because citing an input is what retires it.
`_select()` prefers never-cited candidates for the same reason: recompiling an
already-folded note adds an article without retiring anything, which is how
the backlog grew while 120 passes ran.

## Consequence for the compile skill

Wikilinks are now load-bearing, not stylistic. A compiled article that folds
in a note without citing its exact slug leaves that note in the backlog and in
every future listing. `skills/compile-memories/SKILL.md` step 4.3 states this
as mandatory.

## Knobs

| Env var | Default | Effect |
|---|---|---|
| `CCMEMORY_LIST_TOKEN_BUDGET` | 6000 | Per-call `memory_list` token ceiling; 0 = unbounded |
| `CCMEMORY_COMPILE_THRESHOLD` | 20 | Uncited count at which the in-band COMPACTION DUE directive appears |

`memory_stats` reports `list_tokens_actual`, `list_tokens_unbounded`,
`folded` and `list_counts`, so listing pressure is measurable before it hurts.
