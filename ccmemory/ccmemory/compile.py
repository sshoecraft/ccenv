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
#: can ever retire from a listing. Every type is in it: a type left out is a
#: type nothing can drain, and the count only ever goes up.
#:
#: ``reference`` was added in 0.19.0. Before that, _select filtered to
#: ``project`` alone while count_backlog counted every type, so mxfs carried a
#: backlog of 186 of which only 42 were actionable: a hard floor of 144 against
#: a threshold of 20. The nudge fired every session and no amount of compacting
#: could ever silence it.
#:
#: ``user``/``feedback`` were added in 0.20.0 for the same reason, one tier up.
#: They are exempt from *trimming* priority, not from compaction — but nothing
#: distinguished the two, so they accumulated without limit until they
#: overflowed their own budget tier and started reporting
#: ``load_bearing_withheld`` on every listing, permanently. They compact into
#: an article of an always-listed type and nothing else; see ``group_pool``
#: and ``Store.folded_names``.
COMPILABLE_TYPES = ("project", "reference", "feedback", "user")

#: Which listing tier a note compacts within. Groups never mix the two, because
#: the group decides the article's type and an article can only be one type. A
#: behavior note folded into a `type: project` article would be represented by
#: something that can be budget-trimmed, which is worse than never folding it.
BEHAVIOR_POOL = "behavior"
KNOWLEDGE_POOL = "knowledge"

#: metadata.type the compiled article must carry, per pool.
POOL_ARTICLE_TYPE = {BEHAVIOR_POOL: "feedback", KNOWLEDGE_POOL: "project"}


def group_pool(note_type: str | None) -> str:
    """The pool a raw note compacts within, from its own type."""
    return BEHAVIOR_POOL if Store._is_always_listed(note_type) else KNOWLEDGE_POOL

# Default uncompiled-backlog count at/above which the SessionStart hook
# suggests running the compile-memories skill. Matches the default
# max_inputs batch size: "more raw notes than one compile pass folds in".
DEFAULT_THRESHOLD = 20

#: Quiet window after a compile pass before the nudge may fire again.
#: Override with CCMEMORY_COMPILE_COOLDOWN (seconds; 0 disables the guard).
DEFAULT_COOLDOWN_SECONDS = 900

#: Notes per compaction group. One group is one background compactor run, so
#: this is also the ceiling on how many memory bodies a single subagent has to
#: read — the reason it is well under the threshold rather than equal to it.
DEFAULT_GROUP_SIZE = 12

#: Groups dispatched concurrently. The caller fires this many compactors, then
#: the next batch as they finish, until the plan is exhausted.
DEFAULT_WAVE = 6

#: Below this, a cluster is not a topic. Such notes are pooled into
#: ``assorted`` groups rather than left out of the plan: a note no plan covers
#: is a note nothing can ever retire, and enough of them put a permanent floor
#: under the backlog — the failure COMPILABLE_TYPES documents above.
MIN_CLUSTER = 3

#: Seeds the nudge names inline before deferring to the plan tool for the rest.
NUDGE_SEED_CAP = 24

#: How good a BM25 match has to be, as a fraction of the seed note's match
#: against its own text, to count as a topic-mate rather than an incidental
#: word overlap. Measured on this repo's store: a seed scored -35.7 against
#: itself, its one real topic-mate -19.3, the next genuinely related note -6.9,
#: and everything below that was unrelated. Without a floor the group is just
#: "the twelve notes that share the most common words", which is an assorted
#: batch wearing a topic label.
CLUSTER_FLOOR = 0.15


#: The dispatch every nudge site asks for. One agent per group, addressed by
#: seed slug — see ``compaction_plan`` for why seeds and not indices.
AGENT_CALL = ('Agent(subagent_type="memory-compactor", '
              'prompt="Compact memory group seeded by `<seed>`.")')


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
     type: <the group's article_type — `feedback` for a group of user/feedback
            notes, `project` otherwise. Copy it; do not decide it.>
   tags: [compiled, <topic-tags>]
   ---

5. Be terse. Engineering prose, no platitudes, no headers like "## Summary".

Output ONLY the article markdown. No explanation before or after."""


def env_int(name: str, default: int, minimum: int = 1) -> int:
    """Positive-int env override, falling back to ``default`` on junk input."""
    raw = os.environ.get(name)
    if raw:
        try:
            return max(minimum, int(raw))
        except ValueError:
            pass
    return default


def threshold() -> int:
    """Backlog count at/above which compaction is suggested (env-overridable)."""
    return env_int("CCMEMORY_COMPILE_THRESHOLD", DEFAULT_THRESHOLD)


def plan_group_size() -> int:
    """Notes per compaction group, via CCMEMORY_COMPILE_GROUP_SIZE."""
    return env_int("CCMEMORY_COMPILE_GROUP_SIZE", DEFAULT_GROUP_SIZE)


def plan_wave_size() -> int:
    """Compactors dispatched concurrently, via CCMEMORY_COMPILE_WAVE."""
    return env_int("CCMEMORY_COMPILE_WAVE", DEFAULT_WAVE)


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
        # folded, not merely cited: a behavior note cited by a `type: project`
        # article is not represented anywhere its own tier can see, so it has
        # not actually been retired and still needs a compile pass.
        cited = s.folded_names()
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
    return env_int("CCMEMORY_COMPILE_COOLDOWN", DEFAULT_COOLDOWN_SECONDS, 0)


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
        cited = s.folded_names()
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


def cluster_query(note: dict) -> str:
    """Search terms that pull a note's topic-mates out of the index."""
    return note["name"].replace("-", " ") + " " + (note.get("description") or "")


def pending_notes(s: Store) -> list[dict]:
    """Every uncompiled note a compile pass can act on, newest first.

    Keyed on ``folded_names`` for the same reason ``count_backlog`` is: a note
    is pending until an article that can actually represent it in the listing
    cites it.
    """
    cited = s.folded_names()
    placeholders = ",".join("?" * len(COMPILABLE_TYPES))
    now = time.time()
    out = []
    for row in s.db.execute(
        "SELECT name, path, type, description, mtime FROM mem "
        f"WHERE type IN ({placeholders}) AND name NOT LIKE 'compiled-%' "
        "ORDER BY mtime DESC",
        COMPILABLE_TYPES,
    ):
        if row["name"] in cited:
            continue
        out.append({
            "name": row["name"], "path": row["path"], "type": row["type"],
            "description": row["description"],
            "age_days": max(0.0, (now - row["mtime"]) / 86400.0),
        })
    return out


def _cluster(s: Store, notes: list[dict], size: int) -> list[list[str]]:
    """Greedy BM25 clustering over one pool's notes, newest note as each seed.

    Candidates are restricted to ``notes``: the index covers the whole store,
    so without that restriction a behavior note would pull project notes into
    its group and the group could no longer name one article type.
    """
    by_name = {n["name"]: n for n in notes}
    claimed: set[str] = set()
    clusters: list[list[str]] = []
    for note in notes:
        if note["name"] in claimed:
            continue
        claimed.add(note["name"])
        group = [note["name"]]
        # recency_weight=0: this is a similarity question, and the default
        # weighting reorders by age, which is how unrelated-but-recent
        # notes were landing in topic groups.
        hits = s.search(cluster_query(note), limit=size * 4, recency_weight=0.0)
        floor = next((h["bm25"] for h in hits if h["name"] == note["name"]), None)
        if floor is None:
            floor = hits[0]["bm25"] if hits else 0.0
        # FTS5 bm25() is negative, better matches more so.
        floor *= CLUSTER_FLOOR
        for hit in hits:
            if len(group) >= size:
                break
            cand = hit["name"]
            if cand in claimed or cand not in by_name or hit["bm25"] > floor:
                continue
            claimed.add(cand)
            group.append(cand)
        clusters.append(group)
    return clusters


def _group(pool: str, names: list[str], kind: str) -> dict[str, Any]:
    return {
        "seed": names[0],
        "kind": kind,
        "size": len(names),
        "names": names,
        "pool": pool,
        # The compactor writes this into the article's frontmatter. A behavior
        # group compiles to `type: feedback` so the article lands in the same
        # listing tier as the notes it retires; see Store.folded_names.
        "article_type": POOL_ARTICLE_TYPE[pool],
    }


def compaction_plan(
    memory_dir: Path,
    *,
    size: int | None = None,
    seed: str | None = None,
) -> dict[str, Any]:
    """Partition the whole uncompiled backlog into disjoint groups — no LLM.

    One group is one compactor run. Without this, every compactor picked its
    own cluster out of ``memory_list()``, which meant (a) only one could run at
    a time, because two started together would converge on the same obvious
    cluster and write two articles about it, and (b) draining a backlog needed
    one session per group. A 227-note backlog against a per-run yield of ~9
    notes is 25 sessions of nudging, while ordinary use adds notes the whole
    time — it never converges, and the partitioning ends up being done by hand.

    Groups are addressed by ``seed``, the slug of the note the group was built
    around, and NOT by index. The plan is recomputed from whatever is still
    uncompiled, so indices shift as concurrent compactors retire notes;
    a seed does not. A seed that no longer appears means that group is already
    compiled, which is exactly what its agent needs to be told.

    Clustering is greedy over the existing BM25 index: take the newest
    unclaimed note as a seed, pull its best-matching unclaimed topic-mates up
    to ``size``, repeat. Deterministic, so every caller computing the plan over
    the same backlog gets the same partition.

    Coverage is total. Clusters below ``MIN_CLUSTER`` are pooled into
    ``assorted`` groups instead of being dropped: an article lumping loosely
    related notes together reads worse than a focused one, but a note left out
    of every plan can never be cited, never retires from the listing, and
    raises the floor of a backlog that the nudge then complains about forever.
    """
    size = size or plan_group_size()
    with Store(memory_dir) as s:
        s.reindex()
        pending = pending_notes(s)
        # Cluster each pool on its own. A group's notes decide the article's
        # metadata.type, so a group spanning both pools would have to pick one
        # and mis-file the other half.
        clusters = {
            pool: _cluster(s, [n for n in pending if group_pool(n["type"]) == pool], size)
            for pool in (BEHAVIOR_POOL, KNOWLEDGE_POOL)
        }

    groups: list[dict[str, Any]] = []
    for pool, pool_clusters in clusters.items():
        tail: list[str] = []
        for c in pool_clusters:
            if len(c) >= MIN_CLUSTER:
                groups.append(_group(pool, c, "topic"))
            else:
                tail.extend(c)
        for i in range(0, len(tail), size):
            groups.append(_group(pool, tail[i:i + size], "assorted"))

    payload: dict[str, Any] = {
        "status": "ok",
        "backlog": len(pending),
        "threshold": threshold(),
        "group_size": size,
        "wave": plan_wave_size(),
        "group_count": len(groups),
        "how": "One background memory-compactor per group, `wave` at a time. Each agent "
               "is given its group's seed slug and compiles that group and nothing else. "
               "The article it writes must carry the group's `article_type` in its "
               "frontmatter: a behavior group (user/feedback notes) compiles to "
               "`feedback` so the article is listed in the same first-claim tier as the "
               "notes it retires, everything else to `project`.",
    }
    if seed is None:
        payload["groups"] = groups
        return payload

    payload["seed"] = seed
    match = [g for g in groups if g["seed"] == seed]
    payload["groups"] = match
    if not match:
        payload["status"] = "done"
        payload["how"] = (
            f"No group is seeded by `{seed}` — it has already been folded into a "
            "compiled- article. Write nothing and report that. Do NOT pick a "
            "different group: another agent owns it."
        )
    return payload


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
