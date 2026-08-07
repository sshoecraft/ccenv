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

## Compaction (folding)

A `compiled-<topic>` article wikilinks the raw notes it absorbed. Those
wikilinks are stored in `mem_edges`, and `Store.folded_names()` reads them to
omit cited notes from the listing — the article now represents them. **Folding
never deletes anything**; folded notes stay fully reachable via
`memory_search` / `memory_get` / `memory_list(include_folded=true)`.

`compile.COMPILABLE_TYPES` (`project`, `reference`) is what a compile pass can
ingest, and therefore the only thing compaction can retire. It **must stay
complementary to `Store.ALWAYS_LIST_TYPES`** — a type in neither tuple can be
neither retired from the listing nor drained from the backlog.
`test_compilable_and_always_listed_types_are_complementary` enforces this.

`count_backlog` counts uncited memories of compilable types only. Counting
types that `_select` will never ingest produces a backlog with a floor above
the threshold, i.e. a nudge that fires forever and cannot be satisfied.

Compaction is **not automatic**. `claude -p` was removed (it bills metered
credit), so nothing in this module runs a model. The backlog surfaces as text
in two places — the SessionStart nudge (`hooks._compaction_nudge`) and the
`COMPACTION DUE` clause in the `memory_list` note — and a model in an
interactive session acts on it by invoking the `compile-memories` skill.

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

### Invariants worth not re-deriving

- Keep `ALWAYS_LIST_TYPES` **small**. Every type in it is a type nothing can
  ever retire.
- Any budget must be able to trim every tier, or it is not a budget.
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
