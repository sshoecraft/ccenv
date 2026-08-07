"""
Storage backend: markdown files of record + SQLite FTS5 index.

Design notes (early, NOT proven):
- .md files in the memory dir are the source of truth. SQLite is purely a
  rebuildable derived index. Delete the DB and `reindex` reconstructs it.
- FTS5 with BM25 ranking, plus a recency bonus so a recent lesson outranks
  an old one on an equal text match. Pattern lifted from /src/shepherd/rag.
- Per-project: one DB per memory dir (no global index in v0).
- No embeddings. If we ever need semantic recall we can add a parallel
  vector column or a sidecar — but BM25 has to demonstrably fall short first.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

try:
    import yaml
except ImportError:
    yaml = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS mem (
    name           TEXT PRIMARY KEY,
    path           TEXT NOT NULL,
    type           TEXT,
    description    TEXT,
    body           TEXT,
    tags           TEXT,
    mtime          REAL NOT NULL,
    content_hash   TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
    name, description, tags, body,
    content='mem', content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON mem BEGIN
  INSERT INTO mem_fts(rowid, name, description, tags, body)
  VALUES (new.rowid, new.name, new.description, new.tags, new.body);
END;
CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON mem BEGIN
  INSERT INTO mem_fts(mem_fts, rowid, name, description, tags, body)
  VALUES('delete', old.rowid, old.name, old.description, old.tags, old.body);
END;
CREATE TRIGGER IF NOT EXISTS mem_au AFTER UPDATE ON mem BEGIN
  INSERT INTO mem_fts(mem_fts, rowid, name, description, tags, body)
  VALUES('delete', old.rowid, old.name, old.description, old.tags, old.body);
  INSERT INTO mem_fts(rowid, name, description, tags, body)
  VALUES (new.rowid, new.name, new.description, new.tags, new.body);
END;

CREATE TABLE IF NOT EXISTS mem_edges (
    src_name  TEXT NOT NULL,
    dst_name  TEXT NOT NULL,
    PRIMARY KEY (src_name, dst_name)
);

CREATE TABLE IF NOT EXISTS injection_ledger (
    session_id  TEXT    NOT NULL,
    slug        TEXT    NOT NULL,
    injected_at INTEGER NOT NULL DEFAULT (unixepoch()),
    tokens      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, slug)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_ledger_injected_at ON injection_ledger (injected_at);
"""

STOP_WORDS = frozenset("""
a an and or the of to in on at for is are was were be been being it this that
these those with from as by if then else when how what why which who whom
do does did has have had will would shall should can could may might must
not no nor so but i you he she we they me him her us them my your his their
""".split())

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|[^\]]*)?\]\]")


@dataclass
class Memory:
    name: str
    path: Path
    type: str | None
    description: str | None
    body: str
    tags: list[str]
    mtime: float
    content_hash: str

    @property
    def age_days(self) -> float:
        return max(0.0, (time.time() - self.mtime) / 86400.0)


def _parse_file(path: Path) -> Memory | None:
    raw = path.read_text(encoding="utf-8", errors="replace")
    m = FRONTMATTER_RE.match(raw)
    meta: dict = {}
    body = raw
    if m:
        front = m.group(1)
        body = raw[m.end():]
        if yaml is not None:
            try:
                parsed = yaml.safe_load(front) or {}
                if isinstance(parsed, dict):
                    meta = parsed
            except yaml.YAMLError:
                meta = _parse_frontmatter_fallback(front)
        else:
            meta = _parse_frontmatter_fallback(front)

    name = str(meta.get("name") or path.stem)
    mtype = meta.get("type")
    if isinstance(mtype, dict):
        mtype = mtype.get("type")
    metadata = meta.get("metadata") or {}
    if not mtype and isinstance(metadata, dict):
        mtype = metadata.get("type")

    description = meta.get("description")
    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return Memory(
        name=name,
        path=path,
        type=mtype,
        description=str(description) if description else None,
        body=body.strip(),
        tags=list(tags),
        mtime=path.stat().st_mtime,
        content_hash=h,
    )


def _parse_frontmatter_fallback(front: str) -> dict:
    # Used only if PyYAML missing. Handles flat scalar k: v lines and `key:`/`  type: x` 1-deep.
    out: dict = {}
    current_key: str | None = None
    for line in front.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] not in " \t" and ":" in line:
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip()
            if not v:
                current_key = k
                out[k] = {}
            else:
                out[k] = v
                current_key = None
        elif current_key and ":" in line:
            k, _, v = line.partition(":")
            out.setdefault(current_key, {})
            if isinstance(out[current_key], dict):
                out[current_key][k.strip()] = v.strip()
    return out


#: Name prefix marking a memory as a compaction article rather than a raw note.
#: Defined here rather than in compile.py because compile.py imports Store —
#: the listing needs the prefix to tier articles and cannot import back.
COMPILED_PREFIX = "compiled-"

#: Filename of the derived SQLite index inside a .ccmemory/ store. No leading
#: dot — the store dir is already hidden, so dot-hiding the file was redundant
#: and produced the confusing ._.memory_index.db sidecar on xattr-less volumes.
INDEX_DB_NAME = "index.db"

#: Pre-0.6.1 index filename. We delete it on init so stores self-migrate to the
#: new name (the index is a rebuildable cache — nothing is lost).
LEGACY_INDEX_DB_NAME = ".memory_index.db"


class Store:
    """SQLite FTS5-backed index over a directory of memory .md files."""

    def __init__(self, memory_dir: Path, db_path: Path | None = None):
        self.memory_dir = Path(memory_dir)
        if db_path:
            self.db_path = Path(db_path)
        else:
            self.db_path = self.memory_dir / INDEX_DB_NAME
            self._drop_legacy_index()
        # isolation_level=None (autocommit) so hooks/claim_injections can issue
        # explicit BEGIN IMMEDIATE for lock-free-until-needed writer semantics
        # (see _write_txn). Every write path below opts into a transaction
        # explicitly; nothing relies on sqlite3's implicit-transaction default.
        self.db = sqlite3.connect(self.db_path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout = 3000")
        self.db.execute("PRAGMA synchronous = NORMAL")
        # journal_mode=WAL is persisted in the file itself; only touch it if
        # it isn't already set, since changing it requires an exclusive lock
        # that a plain read of the current mode does not.
        if self.db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @contextmanager
    def _write_txn(self):
        """BEGIN IMMEDIATE / COMMIT, rolling back on any error.

        BEGIN IMMEDIATE takes the single WAL writer lock up front (bounded by
        the busy_timeout pragma above) instead of risking a deferred-read
        transaction upgrading to a writer mid-flight under concurrent hook
        subprocesses.
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _drop_legacy_index(self):
        """Remove a pre-0.6.1 ``.memory_index.db`` (and its WAL/SHM/journal and
        any ._* sidecar) so the store self-migrates to ``index.db``. The index
        is a derived cache; deleting it just forces one rebuild. Best-effort."""
        legacy = self.memory_dir / LEGACY_INDEX_DB_NAME
        for suffix in ("", "-journal", "-wal", "-shm"):
            for p in (legacy.with_name(legacy.name + suffix),
                      legacy.with_name("._" + legacy.name + suffix)):
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass

    def reindex(self, *, force: bool = False) -> tuple[int, int, int]:
        """Walk memory_dir, upsert changed files, drop missing rows.

        Returns (changed, removed, total_indexed).
        """
        seen: set[str] = set()
        existing = {row["name"]: row["content_hash"] for row in self.db.execute("SELECT name, content_hash FROM mem")}

        # All filesystem I/O and parsing happens before the write lock is
        # taken, so BEGIN IMMEDIATE below holds it only for the DB mutations
        # themselves — concurrent hook claim_injections() calls aren't blocked
        # for the duration of a full memory_dir walk.
        to_upsert: list[Memory] = []
        for md in self._iter_md_files():
            mem = _parse_file(md)
            if mem is None:
                continue
            seen.add(mem.name)
            if not force and existing.get(mem.name) == mem.content_hash:
                continue
            to_upsert.append(mem)

        to_remove = [name for name in existing if name not in seen]

        with self._write_txn():
            for mem in to_upsert:
                self._upsert(mem)
            for name in to_remove:
                self.db.execute("DELETE FROM mem WHERE name = ?", (name,))
                self.db.execute("DELETE FROM mem_edges WHERE src_name = ? OR dst_name = ?", (name, name))

        total = self.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
        return len(to_upsert), len(to_remove), total

    def _iter_md_files(self) -> Iterator[Path]:
        # Skip MEMORY.md itself — that's a generated index, not a memory.
        # Skip macOS AppleDouble sidecars (._*.md): on filesystems that can't
        # store xattrs natively the OS writes a ._<name> file next to every
        # real file, and rglob would otherwise index them as null-type junk.
        for p in sorted(self.memory_dir.rglob("*.md")):
            if p.name == "MEMORY.md" or p.name.startswith("._"):
                continue
            yield p

    def _upsert(self, m: Memory):
        self.db.execute(
            """
            INSERT INTO mem(name, path, type, description, body, tags, mtime, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                path=excluded.path, type=excluded.type, description=excluded.description,
                body=excluded.body, tags=excluded.tags, mtime=excluded.mtime,
                content_hash=excluded.content_hash
            """,
            (m.name, str(m.path), m.type, m.description, m.body, ",".join(m.tags), m.mtime, m.content_hash),
        )
        self.db.execute("DELETE FROM mem_edges WHERE src_name = ?", (m.name,))
        for dst in set(WIKILINK_RE.findall(m.body)):
            self.db.execute("INSERT OR IGNORE INTO mem_edges(src_name, dst_name) VALUES (?, ?)", (m.name, dst.strip()))

    def search(self, query: str, *, limit: int = 10, recency_weight: float = 2.0, half_life_days: float = 30.0) -> list[dict]:
        terms = [t for t in re.findall(r"[A-Za-z0-9_]+", query.lower()) if t not in STOP_WORDS and len(t) > 1]
        if not terms:
            return []
        fts_query = " OR ".join(terms)
        rows = self.db.execute(
            """
            SELECT m.name, m.path, m.type, m.description, m.mtime,
                   bm25(mem_fts) AS rank
            FROM mem_fts
            JOIN mem m ON m.rowid = mem_fts.rowid
            WHERE mem_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit * 3),
        ).fetchall()

        now = time.time()
        scored = []
        for r in rows:
            age_days = max(0.0, (now - r["mtime"]) / 86400.0)
            recency = recency_weight * math.exp(-age_days / half_life_days)
            score = r["rank"] - recency  # lower is better in FTS5 BM25; recency reduces it further
            scored.append({
                "name": r["name"],
                "path": r["path"],
                "type": r["type"],
                "description": r["description"],
                "age_days": age_days,
                "bm25": r["rank"],
                "score": score,
            })
        scored.sort(key=lambda x: x["score"])
        return scored[:limit]

    def get(self, name_or_path: str) -> Memory | None:
        row = self.db.execute("SELECT path FROM mem WHERE name = ?", (name_or_path,)).fetchone()
        if row:
            return _parse_file(Path(row["path"]))
        p = Path(name_or_path)
        if not p.is_absolute():
            p = self.memory_dir / name_or_path
        if p.exists():
            return _parse_file(p)
        return None

    def stats(self) -> dict:
        by_type = {row["type"] or "untyped": row["n"] for row in self.db.execute("SELECT type, COUNT(*) AS n FROM mem GROUP BY type")}
        total = self.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {"total": total, "by_type": by_type, "db_bytes": db_size, "db_path": str(self.db_path)}

    def all_memories(self) -> Iterable[sqlite3.Row]:
        return self.db.execute("SELECT name, path, type, description, mtime FROM mem ORDER BY type, name")

    def folded_names(self) -> set[str]:
        """Raw slugs already represented by a ``compiled-`` article.

        Compaction is additive by design (see compile.py): a compile pass
        writes a ``compiled-<topic>`` article that wikilinks its inputs and
        leaves the raw files alone. That means the raw notes it folded in are
        already represented in a denser form — but nothing ever stopped
        listing them, so every compile pass made ``list_all`` BIGGER. The
        wikilinks recorded in ``mem_edges`` are the retirement record we
        already have; this reads it.

        ``ALWAYS_LIST_TYPES`` and untyped memories are NEVER folded, whatever
        cites them — the same rule that gives them first claim on the budget.
        They carry behavior, conventions and preferences: the exact thing the
        session-start listing exists to surface, and the exact thing no other
        retrieval path reaches. There are few enough of them that keeping all
        of them costs nothing — 20 entries on mxfs's 1,848-memory store.

        Keep that tuple SMALL. Every type listed there is a type nothing can
        ever retire; ``reference`` sat in it until 0.19.0 and grew to 160
        permanently-pinned entries.
        """
        return {n for n in self.cited_names()
                if not self._is_always_listed(self._row_type(n))}

    def cited_names(self) -> set[str]:
        """Every existing raw memory cited by a ``compiled-`` article.

        No type exclusions — this is the raw "has this been compiled at all?"
        signal, which is what ``compile.count_backlog`` needs. ``folded_names``
        layers the listing policy on top.
        """
        rows = self.db.execute(
            """
            SELECT DISTINCT e.dst_name
            FROM mem_edges e
            JOIN mem c ON c.name = e.src_name
            JOIN mem d ON d.name = e.dst_name
            WHERE c.name LIKE 'compiled-%'
              AND d.name NOT LIKE 'compiled-%'
            """
        ).fetchall()
        return {r["dst_name"] for r in rows}

    def _row_type(self, name: str) -> str:
        row = self.db.execute("SELECT type FROM mem WHERE name = ?", (name,)).fetchone()
        return (row["type"] or "") if row else ""

    def raw_names(self) -> set[str]:
        """Every indexed memory that is not itself a ``compiled-`` article."""
        return {r["name"] for r in self.db.execute(
            "SELECT name FROM mem WHERE name NOT LIKE 'compiled-%'")}

    #: Types never folded, and given first claim on the listing budget. These
    #: are the memories that aren't reachable any other way: the PreToolUse
    #: auto-injection only fires on a file Read, so behavior/preference/convention
    #: memories are invisible unless the listing carries them.
    #:
    #: ``reference`` was in this tuple until 0.19.0 and is the reason the
    #: listing broke. It was exempt from folding AND from trimming, while
    #: compile.py would only ever ingest ``project`` — so nothing in the system
    #: could retire a reference memory, at any point, ever. It is a durable
    #: *fact* about the environment, which is what BM25 search retrieves well;
    #: it does not need pinning the way a behavioral correction does. On mxfs
    #: the pinned set had reached 160 entries / ~14.9k tokens and had crowded
    #: every project note out of the listing entirely.
    #:
    #: Kept complementary to compile.COMPILABLE_TYPES — every type must be one
    #: or the other, or memories land in a backlog nothing can drain.
    ALWAYS_LIST_TYPES = ("user", "feedback")

    #: Cumulative share of the listing budget available after each tier fills.
    #: Cumulative, so a tier that underspends donates the remainder downward
    #: instead of stranding it. Order is "what can a session not recover any
    #: other way":
    #:   1. user/feedback (and untyped) — behavior, preferences, corrections
    #:   2. ``compiled-`` articles — the dense representative of everything
    #:      folded. Ranking these purely by mtime put 2 of mxfs's 132 articles
    #:      in the listing: 1,494 notes were retired in favour of articles that
    #:      were then themselves withheld, so the session saw neither.
    #:   3. raw project/reference, newest-first — recent working context.
    LIST_TIER_SHARES = (0.25, 0.70, 1.00)

    @classmethod
    def _is_always_listed(cls, mtype: str | None) -> bool:
        """One predicate for both exemptions — never folded, budgeted first.

        Untyped memories count: an unclassified memory is not evidence that it
        is unimportant, and there are never many of them.
        """
        return not mtype or mtype in cls.ALWAYS_LIST_TYPES

    #: Chars of JSON envelope every listing entry pays regardless of content,
    #: in the compact wire format mcp_server emits:
    #: ``{"name":"","type":"","description":"","age_days":0.0},``
    #: — braces, keys, quotes, separators, and the 1-decimal age float.
    #: Measured against real payloads, not guessed. The previous constant (30)
    #: modelled name+description only and under-counted by 1.42x, which is how
    #: a 6,000-token budget shipped a 14,921-token listing while reporting
    #: 10,490. test_entry_tokens_tracks_real_wire_size guards the drift.
    ENTRY_ENVELOPE_CHARS = 56

    @classmethod
    def _entry_tokens(cls, entry: dict) -> int:
        # Same ceil(chars/4) estimator claim_injections uses, applied to the
        # serialized weight of one listing entry.
        chars = (len(entry["name"] or "")
                 + len(entry["type"] or "")
                 + len(entry["description"] or "")
                 + cls.ENTRY_ENVELOPE_CHARS)
        return max(1, -(-chars // 4))

    def list_all(
        self,
        *,
        type_filter: str | None = None,
        include_folded: bool = False,
        token_budget: int = 0,
        limit: int = 0,
    ) -> tuple[list[dict], dict]:
        """Return metadata for memories, newest first, plus what was withheld.

        Returns ``(entries, counts)`` where counts has total/shown/folded/
        withheld. ``path`` is deliberately NOT included: it was 43% of the
        payload on a large store and nothing can use it — ``memory_get`` keys
        on name.

        Bounded by construction. An unbounded listing is not viable at scale:
        on a 1,695-memory store it came to ~171k tokens, and the session
        protocol makes this the mandatory first call of every session.

        The budget is spent across three tiers with CUMULATIVE caps
        (``LIST_TIER_SHARES``), newest-first inside each tier. Cumulative caps
        mean an underspending tier donates its remainder to the tiers below it,
        so no share is ever stranded.

        Every tier is trimmable, including the first. Before 0.19.0 the
        always-listed types were charged against ``spent`` but never trimmed,
        which meant the budget could not bind: once they alone exceeded it the
        loop broke on the very first project note. mxfs listed 180 exempt
        entries for ~14.9k tokens against a 6k budget and withheld 100% of its
        project notes AND 100% of its compiled articles — a listing that was
        simultaneously way over budget and empty of anything current.

        ``token_budget``/``limit`` of 0 mean unbounded. ``include_folded``
        brings back memories already covered by a compiled article.
        """
        if type_filter:
            rows = self.db.execute(
                "SELECT name, type, description, mtime FROM mem WHERE type = ? ORDER BY mtime DESC",
                (type_filter,),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT name, type, description, mtime FROM mem ORDER BY mtime DESC"
            ).fetchall()

        now = time.time()
        entries = [
            {
                "name": r["name"],
                "type": r["type"],
                "description": r["description"],
                # 1 decimal: the raw float serializes as 0.9319928526712788 —
                # 18 chars of noise per entry that nothing reads at that
                # precision, and that ENTRY_ENVELOPE_CHARS would have to model.
                "age_days": round(max(0.0, (now - r["mtime"]) / 86400.0), 1),
            }
            for r in rows
        ]
        total = len(entries)

        folded_count = 0
        if not include_folded:
            folded = self.folded_names()
            if folded:
                kept = [e for e in entries if e["name"] not in folded]
                folded_count = total - len(kept)
                entries = kept

        tiers = (
            [e for e in entries if self._is_always_listed(e["type"])],
            [e for e in entries if not self._is_always_listed(e["type"])
             and e["name"].startswith(COMPILED_PREFIX)],
            [e for e in entries if not self._is_always_listed(e["type"])
             and not e["name"].startswith(COMPILED_PREFIX)],
        )

        shown: list[dict] = []
        spent = 0
        for tier, share in zip(tiers, self.LIST_TIER_SHARES):
            cap = int(token_budget * share) if token_budget else 0
            for e in tier:
                if limit and len(shown) >= limit:
                    break
                if token_budget:
                    cost = self._entry_tokens(e)
                    if spent + cost > cap:
                        break
                    spent += cost
                shown.append(e)

        # How much of tier 1 did not fit. Reported separately because it is the
        # one loss the caller cannot compensate for with memory_search: a
        # behavioral correction has no topic to search for.
        load_bearing_withheld = len(tiers[0]) - sum(
            1 for e in shown if self._is_always_listed(e["type"]))

        # Restore newest-first across the whole result; the tier split is a
        # budgeting device, not an ordering the caller should see.
        shown.sort(key=lambda e: e["age_days"])

        counts = {
            "total": total,
            "shown": len(shown),
            "folded": folded_count,
            "withheld": total - folded_count - len(shown),
            "load_bearing_withheld": load_bearing_withheld,
        }
        return shown, counts

    def claim_injections(
        self,
        session_id: str,
        candidates: list[dict],
        *,
        per_read_max: int,
        session_max: int,
        token_backstop: int,
    ) -> list[dict]:
        """Atomically claim up to ``per_read_max`` not-yet-injected candidates
        against this session's ledger, in ranked order, honoring the
        session-wide ``session_max`` slug count and ``token_backstop``
        estimated-token ceiling.

        Each candidate dict must carry ``name`` (the memory slug) and
        ``line`` (the exact text that will be emitted, used for the token
        estimate). Returns the subset actually claimed, in ranked order —
        callers must emit only what's returned here, since a claim that
        raises mid-transaction rolls back and grants nothing.

        Raises on any DB error. Callers must treat that as fail-shut (inject
        nothing) rather than fall back to unranked/unbounded injection.
        """
        claimed: list[dict] = []
        with self._write_txn():
            row = self.db.execute(
                "SELECT COUNT(*), COALESCE(SUM(tokens), 0) FROM injection_ledger WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            session_count, session_tokens = row[0], row[1]

            for cand in candidates:
                if len(claimed) >= per_read_max:
                    break
                if session_count >= session_max or session_tokens >= token_backstop:
                    break
                est = max(1, -(-len(cand["line"]) // 4))  # ceil(chars / 4)
                if session_tokens + est > token_backstop:
                    continue
                cur = self.db.execute(
                    """
                    INSERT INTO injection_ledger(session_id, slug, injected_at, tokens)
                    VALUES (?, ?, unixepoch(), ?)
                    ON CONFLICT(session_id, slug) DO NOTHING
                    RETURNING slug
                    """,
                    (session_id, cand["name"], est),
                )
                if cur.fetchone() is not None:
                    claimed.append(cand)
                    session_count += 1
                    session_tokens += est
        return claimed

    def reset_session_ledger(self, session_id: str) -> int:
        """Delete all ledger rows for one session (post compact/clear — the
        injected context is gone, so re-injection + a fresh budget are
        correct). Returns the number of rows deleted."""
        with self._write_txn():
            cur = self.db.execute("DELETE FROM injection_ledger WHERE session_id = ?", (session_id,))
            return cur.rowcount

    def prune_ledger(self, max_age_days: int = 30) -> int:
        """Delete ledger rows older than ``max_age_days``. A rolling
        retention window, not a session-lifetime guarantee — see
        docs/injection-ledger.md. Returns the number of rows deleted."""
        with self._write_txn():
            cur = self.db.execute(
                "DELETE FROM injection_ledger WHERE injected_at < unixepoch() - ?",
                (max_age_days * 86400,),
            )
            return cur.rowcount
