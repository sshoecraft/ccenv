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

from .store import COMPILED_PREFIX, Store

#: Types a compile pass will ingest, and therefore the only types compaction
#: can ever retire from a listing. MUST stay complementary to
#: Store.ALWAYS_LIST_TYPES — a type in neither tuple lands in a backlog that
#: nothing can drain and that the nudge will complain about forever.
#:
#: ``reference`` was added in 0.19.0. Before that, _select filtered to
#: ``project`` alone while count_backlog counted every type, so mxfs carried a
#: backlog of 186 of which only 42 were actionable: a hard floor of 144 against
#: a threshold of 20. The nudge fired every session and no amount of compacting
#: could ever silence it.
COMPILABLE_TYPES = ("project", "reference")

# Default uncompiled-backlog count at/above which the SessionStart hook
# suggests running the compile-memories skill. Matches the default
# max_inputs batch size: "more raw notes than one compile pass folds in".
DEFAULT_THRESHOLD = 20

#: Quiet window after a compile pass before the nudge may fire again.
#: Override with CCMEMORY_COMPILE_COOLDOWN (seconds; 0 disables the guard).
DEFAULT_COOLDOWN_SECONDS = 900


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


def _newest_compiled_mtime(memory_dir: Path) -> float | None:
    mts = [p.stat().st_mtime for p in memory_dir.rglob("*.md")
           if _is_compiled(p) and not p.name.startswith("._")]
    return max(mts) if mts else None


def count_backlog(memory_dir: Path) -> dict[str, Any]:
    """Count raw memories that no compiled article cites.

    Counted from the wikilink edges a compile pass writes, NOT from mtimes.
    The previous definition — raw memories newer than the most recent compiled
    article — assumed every pass covers everything older than itself. It
    doesn't: on a 1,695-memory store that heuristic reported 249 while the
    true never-cited count was 431. The 182-memory gap was permanently
    invisible to the nudge, because those notes are older than the newest
    article but were never actually folded into any of them.

    Citation is the right signal and it's already recorded: a compile pass
    wikilinks the inputs it folded, so an uncited raw memory is exactly one
    that has never been compiled. This still quiets down after compaction
    (citing an input retires it) without ever going quiet about work that was
    genuinely skipped.

    Counts only ``COMPILABLE_TYPES`` — what a compile pass can actually act on.
    Counting types _select will never ingest produces a backlog with a floor
    above the threshold, so the nudge fires every session and compacting cannot
    silence it. mxfs sat at a floor of 144 against a threshold of 20 for the
    entire life of the feature. An unsilenceable alarm is a broken alarm.
    """
    newest = _newest_compiled_mtime(memory_dir)
    with Store(memory_dir) as s:
        s.reindex()
        cited = s.cited_names()
        placeholders = ",".join("?" * len(COMPILABLE_TYPES))
        raw = {r["name"] for r in s.db.execute(
            f"SELECT name FROM mem WHERE type IN ({placeholders}) "
            "AND name NOT LIKE 'compiled-%'", COMPILABLE_TYPES)}
    return {
        "backlog": len(raw - cited),
        "total_raw": len(raw),
        "has_compiled": newest is not None,
        "threshold": threshold(),
        # Seconds since the most recent compiled article was written, or None
        # if none exists. The nudge sites use this as a stampede guard: several
        # concurrent sessions all seeing the same backlog would otherwise each
        # dispatch a compactor for the same notes.
        "since_compiled": (time.time() - newest) if newest is not None else None,
    }


def cooldown_seconds() -> int:
    """Quiet window after a compile pass, via CCMEMORY_COMPILE_COOLDOWN.

    Defaults to 15 minutes. A compactor that folds a handful of notes may not
    push the backlog under the threshold on a large store, so without this the
    nudge would re-fire immediately and every session would keep dispatching
    agents at a backlog that is already being worked.
    """
    raw = os.environ.get("CCMEMORY_COMPILE_COOLDOWN")
    if raw is None:
        return DEFAULT_COOLDOWN_SECONDS
    try:
        v = int(raw)
    except ValueError:
        return DEFAULT_COOLDOWN_SECONDS
    return max(0, v)


def nudge_suppressed(b: dict[str, Any]) -> bool:
    """True when a backlog dict should NOT produce a compaction nudge."""
    if b["backlog"] < b["threshold"]:
        return True
    since = b.get("since_compiled")
    return since is not None and since < cooldown_seconds()


def _build_input(memories: list[dict]) -> str:
    chunks = []
    for m in memories:
        body = Path(m["path"]).read_text(encoding="utf-8", errors="replace")
        chunks.append(f"\n========== {m['name']} ({m.get('type') or '-'}, age {m['age_days']:.0f}d) ==========\n{body}\n")
    return "\n".join(chunks)


def _select(memory_dir: Path, *, topic: str | None, max_inputs: int) -> list[dict]:
    with Store(memory_dir) as s:
        s.reindex()
        cited = s.cited_names()
        if topic:
            picks = [p for p in s.search(topic, limit=max_inputs * 3)
                     if not p["name"].startswith(COMPILED_PREFIX)]
            # Never-compiled notes first: recompiling an already-folded note
            # adds an article without retiring anything, which is how the
            # backlog grew while 120 compile passes ran.
            picks.sort(key=lambda p: p["name"] in cited)
            picks = picks[:max_inputs]
        else:
            picks = []
            placeholders = ",".join("?" * len(COMPILABLE_TYPES))
            for row in s.db.execute(
                "SELECT name, path, type, description, mtime FROM mem "
                f"WHERE type IN ({placeholders}) AND name NOT LIKE 'compiled-%' "
                "ORDER BY mtime DESC",
                COMPILABLE_TYPES,
            ):
                if row["name"] in cited:
                    continue
                if len(picks) >= max_inputs:
                    break
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
