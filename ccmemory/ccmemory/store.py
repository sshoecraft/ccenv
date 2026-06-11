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
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

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
        changed = 0
        existing = {row["name"]: row["content_hash"] for row in self.db.execute("SELECT name, content_hash FROM mem")}

        for md in self._iter_md_files():
            mem = _parse_file(md)
            if mem is None:
                continue
            seen.add(mem.name)
            if not force and existing.get(mem.name) == mem.content_hash:
                continue
            self._upsert(mem)
            changed += 1

        removed = 0
        for name in list(existing):
            if name not in seen:
                self.db.execute("DELETE FROM mem WHERE name = ?", (name,))
                self.db.execute("DELETE FROM mem_edges WHERE src_name = ? OR dst_name = ?", (name, name))
                removed += 1

        self.db.commit()
        total = self.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
        return changed, removed, total

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

    def list_all(self, *, type_filter: str | None = None) -> list[dict]:
        """Return all memories' metadata, newest first. No query, no ranking.

        Same dict shape as ``search()`` minus bm25/score: caller gets
        name/path/type/description/age_days. For "what memories exist" —
        the answer search() can't give without a non-empty query.
        """
        if type_filter:
            rows = self.db.execute(
                "SELECT name, path, type, description, mtime FROM mem WHERE type = ? ORDER BY mtime DESC",
                (type_filter,),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT name, path, type, description, mtime FROM mem ORDER BY mtime DESC"
            ).fetchall()
        now = time.time()
        return [
            {
                "name": r["name"],
                "path": r["path"],
                "type": r["type"],
                "description": r["description"],
                "age_days": max(0.0, (now - r["mtime"]) / 86400.0),
            }
            for r in rows
        ]
