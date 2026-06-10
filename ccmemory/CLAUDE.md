# ccmemory

Persistent, per-project memory for Claude Code. Markdown files of record +
SQLite/FTS5 index + enforcement hooks + MCP server + LLM-driven knowledge
compiler. Proper Python package, two real dependencies (PyYAML, mcp).

## Status

Early but functional. 22-test pytest suite passing; wired and connected
to real Claude Code (user-scope MCP + autoinstalled hooks). Ranking
heuristics are not benchmarked — they're informed guesses lifted from
Shepherd that produce sensible results on the MXFS corpus.

## Install

```
pip3 install --user /src/ccmemory
```

That puts `ccmemory` on PATH (`~/.local/bin/ccmemory`) and pulls deps
(PyYAML, mcp). Re-run after pulling source changes.

Then register with Claude Code (one-time, user scope so it's available
from every project):

```
claude mcp add -s user ccmemory ccmemory mcp
```

That's it. The MCP server's first boot autoinstalls the four hooks into
`~/.claude/settings.json`. No `ccmemory install` step needed.

To disable autoinstall (debugging), set `CCMEMORY_NO_AUTOINSTALL=1`.

## Why this exists

Claude Code's built-in memory ships a `MEMORY.md` file loaded into every
session, with the model told to "add a one-line pointer." Under
autonomous loops (ccloop) with no enforcement, the file grows
monotonically — observed: 52KB / 2,200-char single lines / ~13K wasted
tokens of duplicated session summaries. Pruning by hand is a treadmill.

The fix: **stop hand-maintaining MEMORY.md. Generate it.** Per-fact `.md`
files with YAML frontmatter are source of truth. A Stop hook
regenerates MEMORY.md from each file's `description:` with hard caps. A
PreToolUse hook blocks direct edits. A SessionStart hook injects the new
memory protocol as additionalContext, overriding Claude's built-in
auto-memory instructions. Discipline is structural, not aspirational.

## Architecture

```
.md files (frontmatter + body)          ← source of truth
        │
        ▼
 ccmemory.store                          ← SQLite + FTS5 + BM25 + recency
        │
        ├── ccmemory CLI (ops only)      ← mcp | hook | install | uninstall | status | compile
        ├── ccmemory.installer           ← atomic settings.json updates (ccloop pattern)
        ├── ccmemory.hooks               ← session / stop / guard / inject handlers
        ├── ccmemory.mcp_server          ← memory_search / get / write / stats / regen_index
        └── ccmemory.compile             ← LLM knowledge compiler (claude -p)
```

### Surface (just enough, no more)

**MCP tools** — what the model actually uses:
- `memory_search(query, n=5)`
- `memory_get(name)`
- `memory_write(name, type, description, body, tags?)`
- `memory_stats()`
- `memory_regen_index()`

**Hooks** — automatic behaviors registered in settings.json:

| Event | Matcher | Subcommand | Purpose |
|---|---|---|---|
| SessionStart | – | `session` | Inject memory protocol as additionalContext |
| Stop | – | `stop` | Regenerate MEMORY.md (skipped if `.ccmemory-skip-regen` present) |
| PreToolUse | `Write\|Edit\|NotebookEdit` | `guard` | Block edits to MEMORY.md |
| PreToolUse | `Read` | `inject` | Surface relevant prior lessons inline |

All hooks are fail-open. Exceptions log to stderr, return 0.

**CLI** — ops-only:
```
ccmemory mcp                  run the MCP server (used by claude mcp config)
ccmemory hook {session|stop|guard|inject}   hook entry (called by Claude Code)
ccmemory install [--settings]               manual install (normally autoinstalled)
ccmemory uninstall [--settings]             remove hooks (preserves foreign)
ccmemory status                             show install state
ccmemory compile [--topic --max --dry-run]  LLM-compile session lessons
```

There are deliberately NO `search`/`get`/`stats`/`reindex` CLI
subcommands. Those live on the MCP tool surface, which is where the
model actually queries memory. Humans poking the corpus directly is not
the intended workflow.

### Autoinstall pattern (lifted from ccloop)

`mcp_server.serve()` calls `installer.autoinstall_quiet()` first thing.
When Claude Code spawns the MCP server, hooks self-register. Same logic
as `/src/ccloop`'s runner calling `ensure_registered()` at start —
just applied to ccmemory's actual entry point (the MCP server) instead
of a CLI.

`_is_ours` distinguishes ccmemory hooks from ccloop hooks by binary name
and the literal `hook` middle word in the command (e.g. `ccmemory hook
stop` vs `ccloop keepgoing`), so the two installers coexist cleanly.

### The escape hatch

A `.ccmemory-skip-regen` sentinel file in the memory dir disables the
Stop-hook regeneration for that project. Use this when the project's
`MEMORY.md` is hand-curated and you don't want it overwritten.

### What was stolen from where

- **/src/ccloop install.py**: atomic settings.json writes with
  timestamped backups, `_is_ours` foreign-hook detection, self-healing
  for relocated executables, autoinstall-on-entry pattern.
- **/src/shepherd/rag/sqlite_backend.cpp**: SQLite FTS5 + BM25 +
  time-recency ranking. No embeddings.
- **basic-memory**: `.md` files as durable truth; SQLite is a
  rebuildable index. YAML frontmatter conventions.
- **claude-mem**: PreToolUse-on-Read context injection; fail-open hook
  architecture.
- **claude-memory-compiler**: LLM-driven compilation of raw per-session
  lessons into denser, cross-referenced knowledge articles.
- **/src/influx_mcp**: MCP server pattern using the official Python
  `mcp` SDK.

### Why FTS5 over vector embeddings

Lexical wins for code-shaped queries (`bnobt`, `ISTALE_CAW`, error
codes, function names) and costs nothing at query time. Zero models to
host, zero refresh on write, zero API calls. If a query class
demonstrably under-recalls (e.g. paraphrased conceptual search), add a
parallel vector column without changing the file-of-truth contract.

## Memory file format

```markdown
---
name: short-kebab-case-slug
description: one-line summary, used in MEMORY.md and ranked by FTS5
metadata:
  type: user | feedback | project | reference
tags: [optional, list]
---

Body — free-form markdown. `[[wikilinks]]` are extracted into mem_edges.
```

## Tests

```
cd /src/ccmemory && python3 -m pytest -q
```

22 tests covering store reindex/search/recency, installer
install/uninstall round-trips + foreign-hook preservation + relocated-
executable self-heal, hook handler outputs + fail-open + sentinel.

## Real findings from MXFS smoke test

- **Read-injection produces relevant hits** on first try (`xfs_inode.c`
  → `sess45_lessons` / `sess79_lessons` / `sess47_lessons`, all real
  prior investigations into that file). Query construction is dumb
  (filename stem + parent dir tokens) — may want to enrich later.
- **Installer correctly preserves foreign hooks** (e.g. ccloop's
  `Stop`/`PostToolUse`), idempotent on re-install, clean uninstall.
- **Index regeneration truncates at default caps.** 112 memories ×
  150-char descriptions = ~20KB but default `file_cap=12000`. Open
  question (below).
- **MXFS's MEMORY.md is hand-curated; sentinel honored** —
  `.ccmemory-skip-regen` present, Stop hook no-ops, file preserved.

## Open questions (NOT decided)

- Description cap and file cap defaults — depend on real corpus shape
- `archived:` frontmatter flag for index exclusion — described, not
  implemented
- Tag-column FTS5 weight boost — schema is there, BM25 still uniform
- Cross-project search (union view) — out of scope v0.x
- Should `memory_write` MCP tool reject the call instead of silently
  truncating an over-cap description?
- Does the model actually obey SessionStart's protocol over Claude's
  built-in auto-memory instructions? — needs a real session to verify.

## Versioning

Per global rules: patch = fix, minor = feature, major = breaking.

## Architecture history

- **v0.1.0**: initial scaffold. Store + CLI + index generator working;
  MCP transport and Read-inject hook were stubs.
- **v0.2.0**: shipped the rest of the surface. Installer (ccloop
  pattern), three hook handlers folded into `ccmemory hook <name>`,
  MCP server with JSON-RPC transport via official `mcp` SDK, LLM
  compiler via `claude -p`, autoinstall on every CLI entry.
- **v0.3.0**: corrected entry-point placement. Autoinstall moved from
  CLI to `mcp_server.serve()` (the real entry point — user runs
  ccmemory by having Claude Code spawn the MCP server, not by typing
  `ccmemory` at a shell). Added 4th hook: SessionStart for
  additionalContext protocol injection. Stripped user-facing CLI
  commands (search/get/stats/reindex/regen-index removed) — those
  belong on the MCP surface. Added `.ccmemory-skip-regen` sentinel.
- **v0.4.0**: proper Python package. `pip install .` install path;
  console script entry replaces `bin/` shim. 22-test pytest suite. Authors/license/classifiers/dev deps in pyproject.toml.
  Verified end-to-end against MXFS: registered at user scope, hooks
  self-heal on path change, MCP tool calls return clean JSON payloads.
- **v0.5.0**: SessionStart protocol rewrite. Observed in a live MXFS
  session that the model went straight to `memory_get(name=...)`
  without searching first — driven by the protocol leading with
  `memory_get(name="sess91_lessons")` as a side-by-side example.
  Rewrote the protocol to (a) explicitly contrast metadata-cost vs
  body-cost between search and get, (b) prohibit guess-then-get
  ("never guess a memory name and call `memory_get` directly"),
  (c) clarify that the Read-inject hook already covers file-tied
  queries automatically. Token efficiency: search returns ~150 chars
  per hit, get returns the full 5-10KB body.
- **v0.6.0**: portability. Memory now lives at
  ``<project_root>/.ccmemory/`` instead of
  ``~/.claude/projects/<slug>/memory/`` — travels with the repo,
  survives clones, supports collaboration. SQLite index gitignored as
  a derived cache; `.md` files are the git-friendly source of truth.
  Added ``paths.py`` (single shared resolver: env > git root >
  project markers > CWD) and ``migrate.py`` (SHA-256-verified copy
  from legacy dir to project-local). Auto-migration fires on MCP
  server boot when project has legacy memory but no `.ccmemory/`;
  source preserved, never deleted. ``ccmemory migrate`` / ``ccmemory
  where`` CLI subcommands. 17 new tests (39 total, all passing).
- **v0.6.1**: store hygiene, self-healing everywhere. The v0.6.0
  "index is gitignored" promise was only wired into the *migrate* path,
  so stores created by ``memory_write`` (the common case) got no
  ``.gitignore`` and leaked the derived DB — and, on filesystems that
  can't store xattrs natively (NFS/SMB/some bind mounts), macOS ``._*``
  AppleDouble sidecars too. Fix: ``paths.ensure_gitignore()`` is the one
  source of truth for the store's ``.gitignore`` (index cache + ``._*``
  + ``.DS_Store``), called from ``mcp_server._resolve_dir()`` so EVERY
  project ccmemory touches self-heals on EVERY machine — no per-project
  manual step (the projects live on other hosts we can't reach). It is
  idempotent and append-only for pre-existing/foreign gitignores.
  ``migrate.py`` now delegates to the same helper. Renamed the index
  file ``.memory_index.db`` → ``index.db`` (the leading dot was
  redundant inside the already-hidden ``.ccmemory/`` and produced the
  confusing ``._.memory_index.db`` sidecar); ``Store`` deletes any
  legacy ``.memory_index.db`` on init so stores self-migrate (the index
  is a rebuildable cache). 2 new tests (41 total, all passing).
- **v0.6.2**: index scan skips macOS AppleDouble sidecars. ``_iter_md_files``
  rglob'd ``*.md`` and matched ``._<name>.md`` sidecars (created by the OS on
  xattr-less volumes next to every real file), indexing them as null-type junk
  rows that polluted search. Now skips any name starting with ``._``. The
  v0.6.1 gitignore keeps sidecars out of *git*; this keeps them out of the
  *index*. 1 new test (42 total, all passing).

## Layout

```
ccmemory/
  __init__.py
  paths.py        project-root + memory-dir resolver
  store.py        FTS5 store, schema, search, file scan
  index_gen.py    capped MEMORY.md generator
  installer.py    settings.json install/uninstall
  hooks.py        hook handlers
  mcp_server.py   MCP server (uses official mcp SDK)
  migrate.py      legacy → project-local memory migration
  compile.py      LLM knowledge compiler
  cli.py          argparse entry (ops only)
tests/
  conftest.py
  test_store.py
  test_installer.py
  test_hooks.py
  test_paths.py
  test_migrate.py
pyproject.toml
README.md
CLAUDE.md
LICENSE
.gitignore
```
