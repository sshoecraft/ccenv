"""Memory compaction — backlog detection + the compiler prompt.

Pattern from claude-memory-compiler: raw per-session lessons accumulate
faster than humans can curate them. Periodically a compiler reads N raw
memories and produces one structured, cross-referenced knowledge article
named ``compiled-<topic>`` (written via ``memory_write``, so it lives at the
memory-dir root alongside the raw notes). The raw inputs stay where they
are — the compiled article is an additional, denser entry.

This module used to shell out to ``claude -p`` (Claude Code headless mode).
That path was removed: ``claude -p`` / the Agent SDK draws from a metered
monthly credit pool (full API rates, no rollover) rather than the
subscription, so every compile run cost real money. Compaction now runs in
the live INTERACTIVE session via the ``compile-memories`` skill, which is
unaffected by that billing change. This module no longer calls any LLM; it
only (a) detects how big the uncompiled backlog is, so the SessionStart hook
can nudge, and (b) selects + formats the candidate inputs and exposes the
compiler prompt the skill uses.

``COMPILER_PROMPT`` is the single source of truth for the synthesis rules —
the ``compile-memories`` skill embeds the same text. Keep them in sync.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from .store import Store


# Marker that identifies an already-compiled article (memory_write has no
# subdir support, so compiled articles live at the memory-dir root under this
# name prefix rather than in a compiled/ subdirectory).
COMPILED_PREFIX = "compiled-"

# Default uncompiled-backlog count at/above which the SessionStart hook
# suggests running the compile-memories skill. Matches the default
# max_inputs batch size: "more raw notes than one compile pass folds in".
DEFAULT_THRESHOLD = 20


COMPILER_PROMPT = """\
You are compiling raw per-session memory files into a single dense knowledge
article. Read the inputs below. Produce ONE markdown article that:

1. Identifies the central topic the inputs share.
2. Extracts every decision, lesson, and recurring failure mode — deduplicated
   and chronologically ordered when timing matters.
3. Cross-references the source sessions using the literal slugs you see in
   the input (e.g. `[[sess79_lessons]]`).
4. Ends with a YAML frontmatter block at the very TOP of the article in this
   exact format:

   ---
   name: compiled-<short-kebab-topic>
   description: one-line summary suitable for a memory index (<150 chars)
   metadata:
     type: project
   tags: [compiled, <topic-tags>]
   ---

5. Be terse. Engineering prose, no platitudes, no headers like "## Summary".

Output ONLY the article markdown. No explanation before or after."""


def threshold() -> int:
    """Backlog count at/above which compaction is suggested (env-overridable)."""
    raw = os.environ.get("CCMEMORY_COMPILE_THRESHOLD")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_THRESHOLD


def _is_compiled(p: Path) -> bool:
    return p.name.startswith(COMPILED_PREFIX)


def _raw_memory_files(memory_dir: Path) -> list[Path]:
    """Raw (uncompiled) memory files: *.md under the memory dir, excluding the
    generated index, AppleDouble sidecars, and already-compiled articles."""
    out = []
    for p in memory_dir.rglob("*.md"):
        if p.name == "MEMORY.md" or p.name.startswith("._") or _is_compiled(p):
            continue
        out.append(p)
    return out


def _newest_compiled_mtime(memory_dir: Path) -> float | None:
    mts = [p.stat().st_mtime for p in memory_dir.rglob("*.md")
           if _is_compiled(p) and not p.name.startswith("._")]
    return max(mts) if mts else None


def count_backlog(memory_dir: Path) -> dict[str, Any]:
    """Count raw memories not yet folded into a compiled article.

    The backlog is raw memories newer than the most recent compiled article
    (or every raw memory when nothing has been compiled yet). Counting the
    backlog rather than the total is what keeps the SessionStart nudge from
    firing forever — compiled articles are additive and never delete the raw
    files, so a total-count check would never quiet down after compaction.
    """
    newest = _newest_compiled_mtime(memory_dir)
    raw = _raw_memory_files(memory_dir)
    if newest is None:
        backlog = len(raw)
    else:
        backlog = sum(1 for p in raw if p.stat().st_mtime > newest)
    return {
        "backlog": backlog,
        "total_raw": len(raw),
        "has_compiled": newest is not None,
        "threshold": threshold(),
    }


def _build_input(memories: list[dict]) -> str:
    chunks = []
    for m in memories:
        body = Path(m["path"]).read_text(encoding="utf-8", errors="replace")
        chunks.append(f"\n========== {m['name']} ({m.get('type') or '-'}, age {m['age_days']:.0f}d) ==========\n{body}\n")
    return "\n".join(chunks)


def _select(memory_dir: Path, *, topic: str | None, max_inputs: int) -> list[dict]:
    with Store(memory_dir) as s:
        s.reindex()
        if topic:
            picks = [p for p in s.search(topic, limit=max_inputs) if not p["name"].startswith(COMPILED_PREFIX)]
        else:
            picks = []
            for row in s.db.execute(
                "SELECT name, path, type, description, mtime FROM mem "
                "WHERE type = 'project' ORDER BY mtime DESC LIMIT ?",
                (max_inputs,),
            ):
                if row["name"].startswith(COMPILED_PREFIX):
                    continue
                age_days = max(0.0, (time.time() - row["mtime"]) / 86400.0)
                picks.append({
                    "name": row["name"], "path": row["path"], "type": row["type"],
                    "description": row["description"], "age_days": age_days, "score": 0.0, "bm25": 0.0,
                })
    return picks


def compile_status(
    memory_dir: Path,
    *,
    topic: str | None = None,
    max_inputs: int = 20,
) -> dict[str, Any]:
    """Report the compaction backlog and the candidate input batch — no LLM.

    This is the non-metered replacement for the old ``claude -p`` run. It does
    not produce an article; it shows what the ``compile-memories`` skill would
    work on. Run that skill inside an interactive session to actually compile
    (free — no ``claude -p``, no Agent-SDK credit burn).
    """
    backlog = count_backlog(memory_dir)
    picks = _select(memory_dir, topic=topic, max_inputs=max_inputs)
    over = backlog["backlog"] >= backlog["threshold"]
    return {
        "status": "ok",
        "topic": topic,
        **backlog,
        "over_threshold": over,
        "candidate_count": len(picks),
        "candidate_names": [p["name"] for p in picks],
        "how": "Run the compile-memories skill in an interactive Claude session to "
               "compile these into a `compiled-<topic>` article (no claude -p / no metered credit).",
    }
