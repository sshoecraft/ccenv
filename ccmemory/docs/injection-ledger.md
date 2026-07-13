# Injection ledger (session-scoped Read-hook dedup + budget)

## What it does

The `PreToolUse:Read` hook (`inject_handler`) surfaces relevant prior memory
as `additionalContext` whenever the model reads a project file. Before this,
it re-ran the same path-derived search on every Read and emitted whatever it
found, with no memory of what it had already shown. Since hook
`additionalContext` is permanent in the transcript, the same memory teaser
got re-injected every time a related file was read — measured at 55% of one
long ccloop session's context (`/src/aitrader`, 79 memories) and 2 memories
re-injected 5x each across 9 Reads in a separate session.

The injection ledger makes each memory eligible for injection **at most once
per session**, and caps total injection **per session**, not just per Read.

## Architecture

- **`Store.claim_injections(session_id, candidates, per_read_max, session_max,
  token_backstop)`** (`ccmemory/store.py`) — the only write path. One
  `BEGIN IMMEDIATE` transaction: reads the session's current claimed
  count/tokens from `injection_ledger`, then walks `candidates` (already
  ranked by `Store.search`) attempting
  `INSERT INTO injection_ledger ... ON CONFLICT(session_id, slug) DO NOTHING
  RETURNING slug` for each. A row returned means this call owns that slug;
  no row means another Read already claimed it this session. Stops after
  `per_read_max` claims or either budget is hit. Returns only the claimed
  subset, in ranked order — callers must emit exactly that, nothing more.
- **`injection_ledger` table** lives inside the existing per-project
  `index.db` (already gitignored, including `-wal`/`-shm`), not a separate
  file. It is session bookkeeping, not derived from the `.md` files the way
  `mem`/`mem_fts` are — but reusing the same file means it inherits the
  gitignore and the "delete to reset" escape hatch for free. If `index.db` is
  ever deleted, the only cost is a handful of sessions re-injecting once.
- **`inject_handler`** (`ccmemory/hooks.py`) — reads `session_id` from the
  hook payload (Claude Code's stdin JSON; MCP tool calls don't carry this —
  see "Why memory_get/memory_search don't participate" below). Missing
  `session_id` → inject nothing. Otherwise searches a wider candidate set
  (`INJECT_CANDIDATE_WIDTH = 10`, fixed) than it will emit, so already-claimed
  top hits don't make it falsely conclude "nothing relevant" — then calls
  `claim_injections` and emits only what's claimed.
- **`session_handler`** (SessionStart) — resets a session's ledger rows when
  the hook payload's `source` is `compact` or `clear` (the previously
  injected context is gone by then, so both re-injection eligibility and the
  budget should reset), and prunes ledger rows older than
  `LEDGER_RETENTION_DAYS` (30) on every SessionStart, regardless of source.

## Fail-shut, not fail-open

Every other ccmemory hook is fail-**open**: on error, allow the operation
(memory is a quality-of-life layer; it must never block real work). The
injection ledger inverts this for the *injection payload specifically*: on
any error — a missing `session_id`, a locked database past `busy_timeout`, a
corrupt index, anything — `inject_handler` emits **no** `additionalContext`
at all. It still returns 0 (the Read itself is never blocked). Falling back
to unranked/unbounded injection on a ledger failure would silently
reintroduce the exact bug this exists to fix, which is worse than one Read
missing its memory teasers.

## Repeat reads of the exact same file don't necessarily go silent

`claim_injections` walks the ranked candidate window (10) attempting a claim
on each; an already-claimed candidate is skipped, not treated as a stopping
condition. So re-reading the exact same file with several relevant memories
in its candidate pool can surface a *different*, previously-unseen subset on
the second read, then a third subset on the third, until the pool is
exhausted (or the session cap binds) — not silence after the first read.
Confirmed live against the real `/src/aitrader` store (79 memories): an exact
repeat Read never re-showed a slug already shown, but did surface 3 different
ones from further down the same ranked window. **The guarantee is "a given
slug is never shown twice this session," not "a given file only injects
once."** A small fixture with only one matching candidate in the whole store
can't distinguish the two — see `test_inject_never_repeats_a_slug_even_with_a_larger_pool`
in `tests/test_hooks.py` for the pool-sized version of this test.

## Caps (env-overridable)

| Knob | Default | Meaning |
|------|---------|---------|
| `CCMEMORY_INJECT_TOP_N` | 3 | max teasers emitted per Read (existing knob; semantics now "per Read, after dedup") |
| `CCMEMORY_INJECT_SESSION_MAX` | 20 | max unique slugs injected, for the life of one session |
| `CCMEMORY_INJECT_TOKEN_BACKSTOP` | 4000 | defensive est.-token session ceiling; rarely binds since descriptions are capped at 120 chars (~57 tokens/teaser ⇒ 20 hits ≈ 1140 tokens) |

## Why WAL + `isolation_level=None`

Concurrent tool calls mean concurrent hook subprocesses can call
`claim_injections`/`reindex` against the same `index.db` at the same moment.
`Store.__init__` now opens with `isolation_level=None` (Python's sqlite3
autocommit mode) so every write path can issue its own explicit
`BEGIN IMMEDIATE` (via the private `_write_txn` contextmanager) rather than
fighting the module's implicit-transaction default. `BEGIN IMMEDIATE` takes
the single WAL writer lock up front — no deferred-read-transaction-upgrades-
to-writer race — and concurrent writers simply wait up to `busy_timeout`
(3000ms) instead of raising immediately. `journal_mode=WAL` is a durable
property of the database file itself, so it's only *set* (an operation that
needs a brief exclusive lock) when a fresh connection observes the current
mode isn't already `wal` — reissuing the same PRAGMA on every hook
invocation would be needless and could itself contend for that lock.
`reindex()` now computes its full upsert/delete set from the filesystem walk
**before** opening the write transaction, so the writer lock is held only
for the actual DB mutations, not the whole walk-and-parse.

## Why a 30-day rolling window, not a session-lifetime guarantee

An earlier draft pruned ledger rows older than 7 days on every Read. That's
wrong: it silently breaks the "at most once per session" guarantee for any
session alive longer than the prune window (or resumed after it), since a
pruned row makes its slug eligible for re-injection again — while the ledger
still (correctly) refuses to double-count it against the session cap in the
meantime. There is no reliable signal for "this session_id can never be
resumed again," so an exact session-lifetime guarantee isn't achievable
without inventing one. Pruning instead runs at SessionStart (off the Read hot
path, once per relay/session) with a 30-day window — long enough that no
realistic ccloop iteration (hours) or interactive `--resume` gap crosses it in
practice, short enough that ledger growth stays bounded to roughly a month of
sessions × ≤20 tiny rows each.

## Why memory_get/memory_search don't participate

`memory_get(slug)` puts a memory's full body in the transcript; a later Read
hook re-teasing that same slug is redundant in the same spirit this ledger
fixes. But MCP tool calls carry no session identity — the `initialize`
handshake exposes only `protocolVersion`/`capabilities`/`clientInfo`/
`serverInfo` (see `ccenvmcp/docs/mcp.md`), and tool-call arguments don't
either. Only hooks receive `session_id`, via Claude Code's hook stdin JSON.
There is no reliable way to attribute an MCP tool call to the same ledger
namespace a hook uses — process-lifetime-as-session-proxy assumptions and
"most recent session" heuristics both break under concurrent sessions in one
project directory, and a false suppression (silently withholding memory the
model explicitly asked for) is a worse failure than the redundancy it would
prevent. This is a protocol gap, not a deferred nice-to-have — revisit only
if Claude Code ever exposes session identity to MCP servers.

## History

- v0.12.0 — introduced the injection ledger, atomic per-Read claiming,
  session-wide caps, fail-shut behavior, WAL mode, and SessionStart
  compact/clear reset + 30-day prune.
