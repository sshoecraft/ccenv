# ccmemory

Persistent file-backed memory for Claude Code. Replaces the built-in
auto-memory system with markdown files of record, an SQLite/FTS5 search
index, an MCP server, and a small set of enforcement hooks. Single Python
package, two real dependencies (PyYAML, mcp).

## Why this exists

Claude Code ships an auto-memory system that loads a `MEMORY.md` file into
every session, with the model instructed to append a one-line pointer per
memory. Under autonomous loops (e.g. ccloop) with no enforcement, the file
grows monotonically — observed in one project it reached ~52 KB / 2 200-char
single lines / ~13 K wasted tokens of duplicated session summaries before a
human pruned it. Pruning by hand is a treadmill.

ccmemory replaces that with three structural fixes:

1. **Per-fact markdown files with YAML frontmatter are the source of truth.**
   The model writes them via an MCP tool; nothing else.
2. **`MEMORY.md` is generated, not authored.** A Stop hook regenerates it
   from each file's `description:` field with hard caps on per-entry and
   total length. A PreToolUse hook blocks direct edits to it. Bloat is
   structurally impossible because the index has no concept of "append."
3. **Full-text search replaces "load and grep."** The model queries an
   SQLite FTS5 index over the corpus via `memory_search`; bodies are
   fetched on demand via `memory_get`. A Read-injection hook proactively
   surfaces relevant prior memories as additional context whenever the
   model reads a project file.

## Install

```
pip3 install --user /path/to/ccmemory
```

Console script lands at `~/.local/bin/ccmemory` (PEP 668-friendly via
`--user`). Re-run the command after pulling source changes.

Then register with Claude Code, user scope so it's available from every
project:

```
claude mcp add -s user ccmemory ccmemory mcp
```

That's it. The MCP server's first boot autoinstalls four hooks into
`~/.claude/settings.json` and, in any project that has legacy memory at
`~/.claude/projects/<slug>/memory/`, auto-migrates that memory into a
project-local `.ccmemory/` directory.

Escape hatches:
- `CCMEMORY_NO_AUTOINSTALL=1` — skip hook autoinstall
- `CCMEMORY_NO_AUTOMIGRATE=1` — skip auto-migration

## Memory location: it travels with the repo

Memory is stored in `<cwd>/.ccmemory/` — inside the directory Claude Code was
started in, NOT in `~/.claude`. This means:

- Cloning the repo brings the memory with it
- Multiple machines / collaborators see the same memory
- Git diffs of individual session lessons are clean and incremental
- The SQLite index (`index.db`) is gitignored — it's a derived
  cache, regenerated locally on first use. ccmemory writes/refreshes the
  store's `.gitignore` automatically (also covering macOS `._*` sidecars),
  so no per-project setup is needed on any machine.

The anchor is the directory Claude Code was started in (CWD) — full stop.
ccmemory does **not** walk up the tree, does **not** hunt for `.git/` or
build-system markers, and reads **no** environment variable to relocate the
store. So an autonomous ccloop run dir gets its own store right where it runs,
and a session started in a subdirectory keeps its memories local to that subdir
(re-launching there finds them; they never leak up to a parent).

ccmemory resolves the memory dir in this order:

1. `<cwd>/.ccmemory/` — the directory Claude Code was started in
2. Legacy `~/.claude/projects/<slug>/memory/` — Claude Code's per-project
   path, a read-only fallback for un-migrated projects (the source the MCP
   server auto-copies into `.ccmemory/` on first boot)

The first time the MCP server boots in a project with legacy memory and
no `.ccmemory/`, ccmemory auto-migrates: copies the `.md` files, verifies
SHA-256 hashes, drops a `.gitignore`, writes a `.migrated-from` provenance
marker. The legacy source is preserved — delete it manually after you're
satisfied things work.

Manual migration: `ccmemory migrate [--from PATH --to PATH --dry-run --overwrite]`

## What gets installed

Four hooks land in `~/.claude/settings.json`, each fail-open:

| Event       | Matcher                  | Handler   | Purpose |
|-------------|--------------------------|-----------|---------|
| SessionStart| –                        | `session` | Inject memory protocol as additionalContext |
| Stop        | –                        | `stop`    | Regenerate `MEMORY.md` from frontmatter |
| PreToolUse  | `Write\|Edit\|NotebookEdit` | `guard`   | Block edits to `MEMORY.md` |
| PreToolUse  | `Read`                   | `inject`  | Surface relevant prior memories for the file being read |

Foreign hooks (e.g. ccloop's own Stop/PostToolUse entries) are preserved.
Installer self-heals on path changes — moving the `ccmemory` binary
rewrites the registered commands automatically on next MCP server boot.

## Memory file format

```markdown
---
name: short-kebab-case-slug
description: one-line summary, ranked by FTS5 and used in the generated MEMORY.md
metadata:
  type: user | feedback | project | reference
tags: [optional, list]
---

Body — free-form markdown. `[[wikilinks]]` are extracted into the mem_edges
graph table for future neighbor queries.
```

`type` follows Claude Code's auto-memory taxonomy:
- **user** — preferences, role, working style
- **feedback** — corrections, validated approaches, rules to follow
- **project** — ongoing work, decisions, session lessons
- **reference** — pointers to external systems

## MCP tools

| Tool                  | Returns           | Use |
|-----------------------|-------------------|-----|
| `memory_search(query, n=5)` | ranked metadata: name, description, age, path | discovery — cheap, no bodies |
| `memory_get(name)`    | full body of one memory | fetch after search has identified the name |
| `memory_write(name, type, description, body, tags?)` | path of written file | create or overwrite a memory |
| `memory_stats()`      | counts by type, DB size | introspection |
| `memory_regen_index()`| index regeneration result | manual MEMORY.md regen |

The default model-facing protocol (injected at SessionStart) tells the
model to search first, get second — search returns ~150 chars per hit,
get returns the full 5-10 KB body. Inject often covers file-tied
discovery without any tool call.

## CLI

Ops-only — day-to-day memory access goes through MCP tools, not CLI.

```
ccmemory mcp                              run the MCP server (stdio)
ccmemory hook {session|stop|guard|inject} hook entry (called by Claude Code)
ccmemory install [--settings PATH]        manual install (normally autoinstalled)
ccmemory uninstall [--settings PATH]      remove hooks (preserves foreign)
ccmemory status                           show install state
ccmemory where                            show resolved project + memory dir
ccmemory migrate [--from --to --dry-run]  copy legacy memory into project (auto-runs on first boot)
ccmemory compile [--topic --max --dry-run] LLM-compile session lessons
```

There is deliberately no `search` / `get` / `stats` / `reindex` CLI
subcommand. Those live on the MCP surface.

## Escape hatch: protect a hand-curated MEMORY.md

If a project already has a hand-curated `MEMORY.md` (e.g. with structure,
headers, prose context the generator can't reproduce), drop a sentinel
file in the memory directory:

```
touch /path/to/memory/.ccmemory-skip-regen
```

The Stop hook will detect it and no-op for that project. Remove the file
to re-enable regeneration.

## Architecture

```
.md files (frontmatter + body)          source of truth
        |
        v
 ccmemory.store                          SQLite + FTS5 + BM25 + recency
        |
        +-- ccmemory CLI                 mcp | hook | install | uninstall | status | compile
        +-- ccmemory.installer           atomic settings.json updates (ccloop pattern)
        +-- ccmemory.hooks               session / stop / guard / inject handlers
        +-- ccmemory.mcp_server          memory_search / get / write / stats / regen_index
        +-- ccmemory.compile             LLM knowledge compiler (claude -p)
```

### Why FTS5 over vector embeddings

Lexical wins for code-shaped queries (error codes, function names, error
class names). Zero models to host, zero refresh on write, zero API calls.
If a query class demonstrably under-recalls (e.g. paraphrased conceptual
search), add a parallel vector column without changing the file-of-truth
contract.

### Ranking

Standard FTS5 BM25 plus a recency bonus: `score = bm25 - 2.0 * exp(-age_days / 30)`.
Stop-words stripped, terms OR-joined for broad recall. Result: a recent
session's lesson outranks a 90-day-old one on equal text match.

## Tests

```
cd ccmemory && python3 -m pytest -q
```

22 tests covering store reindex/search/recency, installer install/uninstall
round-trips with foreign-hook preservation, hook handler outputs, fail-open
behavior, and sentinel detection.

## Dependencies

- Python 3.9+
- `pyyaml >= 6.0`
- `ccenvmcp` — the bundle's stdlib-only MCP shim (installed first by the
  top-level `install.sh`; replaces the official `mcp` SDK, which requires
  Python 3.10+). Not a declared dependency — see `ccenvmcp/docs/mcp.md`.
- `pytest >= 7.0` (dev only)

Standard library: `sqlite3` (with FTS5 built in), `argparse`, `json`,
`pathlib`, `subprocess`.

## Status

Early but functional. Verified end-to-end on a real Claude Code project:
hooks autoinstall on MCP server boot, MCP tool calls return clean JSON,
the model adopted `memory_write` immediately and `MEMORY.md` Reads dropped
to zero post-install. The 22-test pytest suite covers the boring failure
modes. Ranking heuristics are not benchmarked.

## License

MIT — see [LICENSE](LICENSE).
