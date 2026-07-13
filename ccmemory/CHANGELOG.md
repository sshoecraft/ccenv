# ccmemory changelog

Per the global rule: patch = fix, minor = feature, major = breaking.

## v0.12.0

Fixes unbounded context growth from the `PreToolUse:Read` inject hook: it
re-surfaced the same memory teaser on every Read with no memory of what it
had already shown, permanently bloating the transcript (measured: 55% of one
long ccloop session's context on `/src/aitrader`'s 79-memory store; 9 related
Reads in a separate session re-injected 2 memories 5x each).

Adds a session-scoped **injection ledger**: a new `injection_ledger` table in
the existing `index.db`, claimed atomically per Read via `INSERT ... ON
CONFLICT DO NOTHING RETURNING` inside a `BEGIN IMMEDIATE` transaction
(`Store.claim_injections`). `inject_handler` now searches a wider candidate
set (10) and emits only what it successfully claims — at most
`CCMEMORY_INJECT_TOP_N` (default 3) per Read, capped session-wide at
`CCMEMORY_INJECT_SESSION_MAX` unique slugs (default 20) and
`CCMEMORY_INJECT_TOKEN_BACKSTOP` estimated tokens (default 4000). A missing
`session_id` or any ledger error fails **shut** (no injection at all) rather
than falling back to the unbounded behavior being fixed.

`session_handler` (SessionStart) now resets a session's ledger rows when
`source` is `compact` or `clear` (the injected context is gone by then, so
re-injection is correct), and prunes ledger rows older than 30 days — a
rolling retention window, not a session-lifetime guarantee, since nothing
reliably signals a session can no longer be resumed.

`Store` now opens with `isolation_level=None` and sets `journal_mode=WAL` /
`busy_timeout=3000` / `synchronous=NORMAL`, so concurrent hook subprocesses
claiming against the same `index.db` serialize on the WAL writer lock
instead of racing a read-modify-write. `reindex()` now computes its upsert/
delete sets before opening the write transaction, so the lock is held only
for the DB mutations, not the full memory_dir filesystem walk.

Deliberately out of scope: unifying `memory_get`/`memory_search` into the
same ledger. The MCP `initialize` handshake and tool calls carry no session
identity (only hooks receive `session_id`, via stdin), so there is no correct
way to attribute an MCP tool call to a hook's ledger — this is a protocol
gap, not a deferred nice-to-have. Revisit only if Claude Code ever exposes
session identity to MCP servers.

Verified against ccloop: each relay mints a fresh `session_id` (`--session-id`,
never `--resume`), so the ledger resets for free on every relay with no
ccloop-specific code. ccloop also sets `DISABLE_AUTO_COMPACT=1`, meaning
nothing reclaims wasted context until the hard wall — which is exactly why
the session-wide cap matters more there than in an interactive session.

New tests (9): cross-Read dedup, cross-file dedup, dedup against a larger
candidate pool (a repeat read surfaces fresh slugs rather than going silent
— confirmed live against the real aitrader store), per-session cap, per-Read
cap, fail-shut on missing session_id, compact/clear reset, prune retention,
and atomic-claim idempotency. 60 tests total, all green.

## v0.11.0

Memory anchors to the directory Claude Code was started in (CWD) — nothing
else. `project_root()` no longer walks up the tree and no longer hunts for
`.git/` or build-system markers.

The old resolver walked up from CWD looking for `.git/`, then for
`pyproject.toml` / `package.json` / `Makefile` / `Cargo.toml` / `go.mod`, and
only fell back to CWD if it found none of them. That silently broke the
autonomous-runner case: a ccloop run dir (e.g. aitrader's `<data_dir>/run`,
which holds `CLAUDE.md` + `.claude/settings.json` but no `.git` and no build
files) matched nothing, so the walk ran off the top of `$HOME`,
`project_root()` returned `None`, and `memory_write` failed with "no memory
dir resolvable" — never creating `.ccmemory/` anywhere. It also meant a
session started in a subdirectory had its memory captured by a parent repo
root instead of staying local to that subdir.

Now the anchor is exactly the directory the session started in:

- A ccloop/autonomous run dir gets its own `.ccmemory/` right where it runs.
- A session started in a subdirectory keeps its memories local to that subdir;
  re-launching there later finds them, and they never leak up to a parent.
- `project_root()` is renamed `startup_dir()` and `project_memory_dir()` is
  renamed `startup_memory_dir()` — the old names implied the "go find the
  project" semantics that caused the bug.
- `PROJECT_MARKERS` and the walk-up loop are gone.
- **Both directory-relocation env vars are removed entirely:**
  `CCMEMORY_PROJECT_ROOT` and `CCMEMORY_DIR` no longer exist anywhere in the
  code. The store location is CWD, period — nothing overrides it. (Behavior
  toggles `CCMEMORY_NO_AUTOMIGRATE` and `CCMEMORY_COMPILE_THRESHOLD` are
  unaffected; they don't move the store.)
- The legacy `~/.claude/projects/<slug>/memory/` read fallback stays as a
  back-compat source for un-migrated projects (the MCP server still
  auto-copies it into `<cwd>/.ccmemory/` on first boot).

## v0.10.0

Memory compaction no longer uses `claude -p`. Anthropic is moving the Agent
SDK / `claude -p` / Claude Code GitHub Actions off subscription usage onto a
separate metered monthly credit pool (full API rates, no rollover). The old
`compile` path shelled out to a headless `claude -p` subprocess, so every run
would burn that credit. Compaction now runs in the LIVE interactive session,
which is unaffected by the change.

- New `compile-memories` skill (installed to `~/.claude/skills/compile-memories/`).
  It reads raw memories via the ccmemory MCP tools (`memory_list`/`search`/
  `get`), synthesizes one dense deduplicated `compiled-<topic>` article using
  the same compiler prompt as before, and writes it with `memory_write`.
  Zero `claude -p`, zero metered credit. Its description carries trigger
  conditions so it auto-activates when relevant.
- SessionStart hook now appends a one-line compaction nudge when the
  *uncompiled backlog* (raw memories newer than the most recent
  `compiled-*` article) reaches a threshold (`CCMEMORY_COMPILE_THRESHOLD`,
  default 20). Counting the backlog rather than the total keeps the nudge from
  firing forever — compiled articles are additive and never delete raw notes.
  Under threshold it injects nothing.
- `compile.py` no longer calls any LLM. It exposes `count_backlog()` (for the
  hook) and `compile_status()` (for the CLI), plus the shared `COMPILER_PROMPT`.
  `_resolve_claude_bin` / the `subprocess` call / `CCMEMORY_CLAUDE_BIN` are gone.
- `ccmemory compile` no longer compiles — it reports the backlog, threshold,
  and candidate input names, and points at the skill. `--dry-run` removed
  (the command is read-only now).

## v0.9.0

SessionStart protocol now MANDATES `memory_list()` as the first tool call of
every session. v0.7.0 added `memory_list` and steered the model toward it via
decision rules, but a real failure mode persisted: concept and behavior
memories (user preferences, conventions, decisions, cross-cutting invariants)
are not tied to any file path, so the PreToolUse-on-Read auto-injection never
surfaces them. The model only learned of their existence if it independently
decided to query — which it usually didn't, because it didn't know there was
anything to query. Result: lessons captured into memory were re-derived from
scratch in subsequent sessions, and corrections the user already applied got
re-litigated. Fix: the SESSION_PROTOCOL text now opens with "REQUIRED first
action of every session: call memory_list() once before responding to the
user's first message" with an explanation of why path-tied auto-injection
alone is insufficient. The decision rules for list/search/get still apply
for the rest of the session.

## v0.7.0

`memory_list` MCP tool + SessionStart protocol rewrite to steer the model
toward it. `memory_search` requires a non-empty query (BM25 has nothing to
match on otherwise) — but "show me every memory in this project" is a real
workflow that no amount of clever search-term juggling can satisfy. Observed
in a live session: Sonnet 4.6 asked "what memories do you have?" went
straight to `memory_search` with the query `*` and "ccenv project", both
returning empty results. Added `Store.list_all(type_filter=None)` returning
the same dict shape as `search()` minus bm25/score, sorted by mtime DESC
(newest first), with optional type filter. Exposed as `memory_list` MCP
tool. The SessionStart protocol's "search first, get second" section was
rewritten to "pick the right tool" — three-way decision tree (`list` for
inventory questions, `search` for topic queries, `get` for bodies) with an
explicit quick-decision line, so the model picks list for "what do you
have" without needing a prompt. 2 new tests (44 total, all passing).

## v0.6.2

Index scan skips macOS AppleDouble sidecars. `_iter_md_files` rglob'd
`*.md` and matched `._<name>.md` sidecars (created by the OS on xattr-less
volumes next to every real file), indexing them as null-type junk rows
that polluted search. Now skips any name starting with `._`. The v0.6.1
gitignore keeps sidecars out of *git*; this keeps them out of the *index*.
1 new test (42 total, all passing).

## v0.6.1

Store hygiene, self-healing everywhere. The v0.6.0 "index is gitignored"
promise was only wired into the *migrate* path, so stores created by
`memory_write` (the common case) got no `.gitignore` and leaked the
derived DB — and, on filesystems that can't store xattrs natively
(NFS/SMB/some bind mounts), macOS `._*` AppleDouble sidecars too. Fix:
`paths.ensure_gitignore()` is the one source of truth for the store's
`.gitignore` (index cache + `._*` + `.DS_Store`), called from
`mcp_server._resolve_dir()` so EVERY project ccmemory touches self-heals
on EVERY machine — no per-project manual step (the projects live on
other hosts we can't reach). It is idempotent and append-only for
pre-existing/foreign gitignores. `migrate.py` now delegates to the same
helper. Renamed the index file `.memory_index.db` → `index.db` (the
leading dot was redundant inside the already-hidden `.ccmemory/` and
produced the confusing `._.memory_index.db` sidecar); `Store` deletes
any legacy `.memory_index.db` on init so stores self-migrate (the index
is a rebuildable cache). 2 new tests (41 total, all passing).

## v0.6.0

Portability. Memory now lives at `<project_root>/.ccmemory/` instead of
`~/.claude/projects/<slug>/memory/` — travels with the repo, survives
clones, supports collaboration. SQLite index gitignored as a derived
cache; `.md` files are the git-friendly source of truth. Added
`paths.py` (single shared resolver: env > git root > project markers >
CWD) and `migrate.py` (SHA-256-verified copy from legacy dir to
project-local). Auto-migration fires on MCP server boot when project
has legacy memory but no `.ccmemory/`; source preserved, never deleted.
`ccmemory migrate` / `ccmemory where` CLI subcommands. 17 new tests
(39 total, all passing).

## v0.5.0

SessionStart protocol rewrite. Observed in a live MXFS session that the
model went straight to `memory_get(name=...)` without searching first —
driven by the protocol leading with `memory_get(name="sess91_lessons")`
as a side-by-side example. Rewrote the protocol to (a) explicitly
contrast metadata-cost vs body-cost between search and get, (b) prohibit
guess-then-get ("never guess a memory name and call `memory_get`
directly"), (c) clarify that the Read-inject hook already covers
file-tied queries automatically. Token efficiency: search returns ~150
chars per hit, get returns the full 5-10KB body.

## v0.4.0

Proper Python package. `pip install .` install path; console script
entry replaces `bin/` shim. 22-test pytest suite.
Authors/license/classifiers/dev deps in pyproject.toml. Verified
end-to-end against MXFS: registered at user scope, hooks self-heal on
path change, MCP tool calls return clean JSON payloads.

## v0.3.0

Corrected entry-point placement. Autoinstall moved from CLI to
`mcp_server.serve()` (the real entry point — user runs ccmemory by
having Claude Code spawn the MCP server, not by typing `ccmemory` at a
shell). Added 4th hook: SessionStart for additionalContext protocol
injection. Stripped user-facing CLI commands (search/get/stats/
reindex/regen-index removed) — those belong on the MCP surface. Added
`.ccmemory-skip-regen` sentinel.

## v0.2.0

Shipped the rest of the surface. Installer (ccloop pattern), three hook
handlers folded into `ccmemory hook <name>`, MCP server with JSON-RPC
transport via official `mcp` SDK, LLM compiler via `claude -p`,
autoinstall on every CLI entry.

## v0.1.0

Initial scaffold. Store + CLI + index generator working; MCP transport
and Read-inject hook were stubs.
