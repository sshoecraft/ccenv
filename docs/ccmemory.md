# ccmemory

Persistent per-project memory: `.md` files with YAML frontmatter in a
`.ccmemory/` directory, indexed by a derived SQLite FTS5 database, surfaced to
Claude Code through an MCP server and two hooks.

## Layout

```
.ccmemory/
  <slug>.md            raw memory — frontmatter + markdown body
  compiled-<topic>.md  compaction article, wikilinks the notes it absorbed
  MEMORY.md            generated index (description: lines); never hand-edited
  index.db             derived SQLite index — gitignored, rebuildable
  .gitignore           excludes index.db*, ._*, .DS_Store
```

`.ccmemory/` travels with the repo by design. Only `index.db` is derived.

## Modules

| file | role |
|---|---|
| `store.py` | SQLite/FTS5 index, `reindex`, `search`, `get`, `list_all`, folding, injection ledger |
| `mcp_server.py` | MCP tool surface: `memory_list/search/get/write/stats/regen_index` |
| `hooks.py` | SessionStart protocol injection + compaction nudge; PreToolUse-on-Read auto-injection |
| `compile.py` | compaction backlog accounting and candidate selection (no LLM) |
| `index_gen.py` | regenerates `MEMORY.md` from frontmatter descriptions |
| `paths.py` | memory-dir resolution, `.gitignore` maintenance |

## Retrieval paths

Three, with different costs and different blind spots:

1. **PreToolUse auto-injection** — on `Read` of a project file, searches by
   path and injects matching metadata. Free, but only fires on a file Read, so
   it never surfaces memories that aren't tied to a path.
2. **`memory_search(query)`** — BM25 over the FTS index. Needs specific terms;
   returns nothing for generic queries.
3. **`memory_list()`** — the inventory. Mandatory first call of every session,
   per the injected protocol. This is the only path that surfaces behavior and
   preference memories, which is why its budgeting is load-bearing.

## The listing budget

`memory_list` is paid before the user's first message, and again on every
ccloop relay. An unbounded listing on a 1,700-memory store measured ~171k
tokens, so it is bounded by construction.

`CCMEMORY_LIST_TOKEN_BUDGET` (default 6000) caps the **whole serialized
payload**. `LIST_ENVELOPE_TOKENS` is held back for the `note` and counts; the
remainder is spent on entries across three tiers with **cumulative** caps
(`Store.LIST_TIER_SHARES = (0.25, 0.70, 1.00)`), newest-first inside each:

1. `Store.ALWAYS_LIST_TYPES` (`user`, `feedback`) and untyped memories —
   behavior, corrections, preferences. Nothing else surfaces these.
2. `compiled-` articles — the dense representative of everything folded away.
3. Raw `project` / `reference` notes.

Cumulative caps mean an underspending tier donates its remainder downward, so
a store with no articles still spends the full budget on raw notes.

**Every tier is trimmable, including the first.** When tier 1 overflows,
`counts["load_bearing_withheld"]` reports it and the note escalates: unlike a
withheld project note, a withheld behavioral correction has no topic to search
for, so the session cannot recover it and does not know to try.

**A `type_filter` suspends the tier split and spends the whole budget on the
one type.** The shares are cumulative and donate downward only, so a filtered
listing would otherwise be capped at its tier's share with the rest of the
budget stranded above it in tiers that cannot be populated. That made
`memory_list(type="feedback")` — the escape hatch the `load_bearing_withheld`
warning prescribes — return exactly the truncated set the unfiltered call had
already returned: on a 331-memory store, 34 of 56 feedback memories withheld
both times.

## Compaction (folding)

A `compiled-<topic>` article wikilinks the raw notes it absorbed. Those
wikilinks are stored in `mem_edges`, and `Store.folded_names()` reads them to
omit cited notes from the listing — the article now represents them. **Folding
never deletes anything**; folded notes stay fully reachable via
`memory_search` / `memory_get` / `memory_list(include_folded=true)`.

`compile.COMPILABLE_TYPES` is **every** type. A type a compile pass cannot
ingest is a type nothing can retire, so it accumulates until it overflows
whatever budget governs it and then complains forever. That happened twice:
`reference` below the tier boundary (160 pinned entries on mxfs), then
`user`/`feedback` above it. `test_every_type_can_be_drained` pins it.

Behavior notes are foldable, but **only into an article of an always-listed
type**. `compile.group_pool` splits the backlog into a behavior pool
(`user`/`feedback` → `article_type: feedback`) and a knowledge pool
(`project`/`reference` → `article_type: project`); a group never spans both,
because the group decides the article's single type. `Store.folded_names`
enforces the other half: a behavior note cited only by a `project` article is
**not** folded. Otherwise compaction would trade an entry with first claim on
the budget for a representative the budget is allowed to drop — strictly worse
than not folding it.

`count_backlog` and `pending_notes` therefore key on `folded_names`, not
`cited_names`: a note is pending until something that can actually represent it
in the listing cites it.

Compaction is **not automatic**, and it is **not self-dispatching**. `claude -p`
was removed (it bills metered credit), so nothing in this module runs a model.
The backlog surfaces as text in two places — the SessionStart nudge
(`hooks._compaction_nudge`) and the backlog clause in the `memory_list` note —
and both report a count without ordering the work. They used to prescribe a
fan-out, one background agent per group. Both fire before the user's first
message, where there is no task to run agents alongside, so the only available
behavior was dispatch-then-block: sessions opened by spending ~90s and their
first turn on memory housekeeping nobody had asked for. Whether that turn is
worth spending is the user's call; the count rides back and the decision does
not. The user asks, and then the plan and the `compile-memories` skill (or the
`memory-compactor` agent, one per group) do the work.

## History

- **0.6.1** — index renamed `.memory_index.db` → `index.db`; the leading dot
  produced `._.memory_index.db` AppleDouble sidecars on the xattr-less `/src`
  volume. `Store._drop_legacy_index` self-migrates.
- **0.13.0/0.13.2** — `alwaysLoad` removed; injected protocol text must gate on
  the tools it prescribes actually existing.
- **0.17.0 (bundle 0.18.x)** — listing token budget introduced; `count_backlog`
  switched from an mtime heuristic to citation edges, which had been hiding
  182 never-folded notes on a 1,695-memory store.
- **0.17.0 (bundle 0.19.0)** — the budget made enforceable. See CHANGELOG
  v0.19.0 for the measurements. Four interacting defects: the budget was
  charged for the always-listed tier but could not trim it (so it capped
  nothing and starved everything else); `reference` was unfoldable *and*
  uncompilable, so nothing could ever retire it (160 pinned entries on mxfs);
  `compiled-` articles lost to raw notes on mtime, so folding retired notes in
  favour of articles that were themselves withheld; and the token estimator
  modelled a wire format the server did not emit, under-counting by 1.42x.

## The index is derived, and must be disposable

`index.db` holds nothing the `.md` files do not. That is what makes the repair
in `Store.__init__` safe: an index that cannot be opened is deleted and rebuilt
rather than diagnosed.

It has to be, because the store lives on whatever filesystem the project does.
`/src` is NFS, and SQLite's WAL mode needs a shared `-shm` mapping that NFS
does not provide — a WAL-mode index written on another host answers "unable to
open database file" to *every* statement there, read-only opens included. WAL
is therefore preferred, not assumed: `Store._open` falls back to TRUNCATE when
the filesystem refuses WAL, and `busy_timeout` absorbs the writer-blocks-reader
cost. An index that cannot be opened has no concurrency story to protect.

This failure is silent by construction — the hooks fail open, so a session
whose memory is unreachable is indistinguishable from one with no memories.
Any future change here should keep `memory_stats` loud: it is the only tool
that reports the store's health instead of degrading politely.

### Invariants worth not re-deriving

- Keep `ALWAYS_LIST_TYPES` **small**. Every type in it is a type that must be
  worth first claim on the budget of every session, forever.
- Every type must be compilable. A type with no drain grows without bound.
- A memory may only be folded into an article that is listed at least as early
  as the memory itself. Folding is a swap, and a swap for something the budget
  can drop is a loss.
- Any budget must be able to trim every tier, or it is not a budget.
- A filtered listing gets the whole budget; the tier shares exist to ration a
  mixed listing and have nothing to ration in a filtered one.
- Neither nudge site may order work. They are read before the user's first
  message, where "dispatch this and carry on" has nothing to carry on with.
- If `_entry_tokens` and the server's serialization drift apart, the budget
  silently stops meaning anything —
  `test_entry_tokens_tracks_real_wire_size` pins them together.
- `folded` ≠ deleted. Any change that makes folding lossy breaks the premise
  that compaction is safe to run unattended.

## Measurement probes

Neither writes to a store beyond the derived index:

- `tests/measure_list_payload.py <memory_dir>...` — real serialized payload vs
  the estimator, and which population is exempt from trimming.
- `tests/simulate_list_policy.py <memory_dir>...` — compares listing policies
  against a real `index.db` (opened `mode=ro`) without changing behavior.
