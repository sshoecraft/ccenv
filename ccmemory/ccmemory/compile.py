"""Memory compaction — backlog detection + the compiler prompt.

Pattern from claude-memory-compiler: raw per-session lessons accumulate
faster than humans can curate them. Periodically a compiler reads N raw
memories and produces one structured, cross-referenced knowledge article
named ``compiled-<topic>`` (written via ``memory_write``, so it lives at the
memory-dir root alongside the raw notes). The article replaces its inputs: it
is a lossless merge, and once it is verified against each input that input is
deleted (``Store.delete_folded``). Compaction used to leave the inputs in
place, which made every pass grow the store — on mxfs 638 of 650 raw notes sat
on disk beside the articles that already held them.

This module used to shell out to ``claude -p`` (Claude Code headless mode).
That path was removed on the premise that ``claude -p`` would move onto a
metered API-rate credit pool. Anthropic paused that change on the day it was
due and it never took effect: ``claude -p`` draws from the subscription's
usage limits. The removal stands for a different reason — compaction is done
by ``memory-compactor`` subagents dispatched in the background from the live
session, which need no separate process at all. This module calls no LLM; it
only (a) detects how big the uncompiled backlog is, so the nudge sites can ask
for the dispatch, and (b) partitions the backlog into groups and exposes the
compiler prompt the agents and the ``compile-memories`` skill use.

``COMPILER_PROMPT`` is the single source of truth for the synthesis rules —
the ``compile-memories`` skill embeds the same text. Keep them in sync.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .store import COMPILED_PREFIX, Store, list_entry_budget

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

# Default uncompiled-backlog count at/above which the nudge sites ask for the
# background compactor dispatch. Matches the default
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

#: Where one compaction run's plan is kept, in the memory dir. Every compactor
#: in the run works from it, so the groups cannot shift under them. Recomputed
#: plans did: each merge changes the store and with it the BM25 scores the
#: clustering runs on. On mxfs's first merge run, two compactors were told
#: their seed was `done` before doing anything, and one group listed an
#: article another group had absorbed half a second earlier.
PLAN_FILE = "compaction-plan.json"

#: One claim file per group, under a directory per stored plan. Created with
#: O_EXCL, so exactly one compactor gets each group.
CLAIMS_DIR = "compaction-claims"

#: Seconds a stored plan is used before it is recomputed regardless.
DEFAULT_PLAN_TTL = 3600

#: Seconds before a claim whose compactor never finished can be taken over.
DEFAULT_CLAIM_LEASE = 1800

#: Groups one compactor works before it stops: what fits in one agent's
#: context. A merge group runs 17-27 KB of articles in and about as much out.
DEFAULT_AGENT_GROUPS = 6

#: Ceiling on the body bytes of any ``compiled-`` article, enforced where one is
#: written: memory_write refuses a bigger one and says to split it. memory_get
#: returns a whole article, so its size is what every fetch costs. 24 KiB is
#: the largest article mxfs's compactors had produced before merging existed
#: (24,151 bytes). The ceiling used to bound only what a merge was handed, and
#: two merges came out at 25,833 and 26,729 bytes; backfill and note groups
#: had no bound at all.
DEFAULT_ARTICLE_MAX_BYTES = 24 * 1024

#: How much larger a merged article comes out than its inputs together:
#: citations, a wider opening line, structure. The inputs are packed to the
#: ceiling divided by this. Packing the inputs to the ceiling itself let the
#: output exceed it. On mxfs's first merges outputs ran 2% to 9.3% over their
#: inputs, the largest 24,464 -> 26,729 bytes, past a 24,576 ceiling.
MERGE_GROWTH = 1.10

#: How good a BM25 match has to be, as a fraction of the seed note's match
#: against its own text, to count as a topic-mate rather than an incidental
#: word overlap. Measured on this repo's store: a seed scored -35.7 against
#: itself, its one real topic-mate -19.3, the next genuinely related note -6.9,
#: and everything below that was unrelated. Without a floor the group is just
#: "the twelve notes that share the most common words", which is an assorted
#: batch wearing a topic label.
CLUSTER_FLOOR = 0.15


#: The dispatch every nudge site asks for, made ``wave`` times and once. Each
#: agent claims groups from the stored plan itself until none are left. It
#: used to be one agent per group, relaunched by the session as each finished.
#: The session stops doing that once it is busy with the user's work: mxfs's
#: dispatched 13 of 28 groups, then went back to 4Kn and never launched
#: another.
AGENT_CALL = ('Agent(subagent_type="memory-compactor", '
              'prompt="Compact memory groups from the plan.")')


COMPILER_PROMPT = """\
You are merging raw memory files into a single knowledge article that
REPLACES them: once it is written and verified, the inputs are deleted, so
anything the article leaves out is lost. Read the inputs below. Produce ONE
markdown article that:

1. Identifies the central topic the inputs share.
2. Keeps every concrete fact from every input — decisions, lessons, failure
   modes, and the specifics behind them: exact quotes, numbers, versions,
   paths, commands, error text, measured results, and who said what.
   Deduplicate only where two inputs say the same thing; never trade a
   specific for a summary of it. Order chronologically when timing matters.
3. Cites every input by its literal slug as a wikilink (e.g.
   `[[sess79_lessons]]`), next to the facts it contributed.
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

5. Is terse in its prose, never in its facts. Engineering prose, no
   platitudes, no headers like "## Summary".

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


def plan_ttl() -> int:
    """Seconds a stored plan stays in use, via CCMEMORY_COMPILE_PLAN_TTL."""
    return env_int("CCMEMORY_COMPILE_PLAN_TTL", DEFAULT_PLAN_TTL)


def claim_lease() -> int:
    """Seconds an unfinished claim holds its group, via CCMEMORY_COMPILE_CLAIM_LEASE."""
    return env_int("CCMEMORY_COMPILE_CLAIM_LEASE", DEFAULT_CLAIM_LEASE)


def agent_groups() -> int:
    """Groups one compactor works before stopping, via CCMEMORY_COMPILE_AGENT_GROUPS."""
    return env_int("CCMEMORY_COMPILE_AGENT_GROUPS", DEFAULT_AGENT_GROUPS)


def article_max_bytes() -> int:
    """Body bytes a compiled- article may hold, via CCMEMORY_ARTICLE_MAX_BYTES."""
    return env_int("CCMEMORY_ARTICLE_MAX_BYTES", DEFAULT_ARTICLE_MAX_BYTES)


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
        raw = {r["name"]: r["type"] for r in s.db.execute(
            f"SELECT name, type FROM mem WHERE type IN ({placeholders}) "
            "AND name NOT LIKE 'compiled-%'", COMPILABLE_TYPES)}
        merge = sum(len(g["names"]) for g in merge_groups(s, plan_group_size()))
    pending = set(raw) - cited
    return {
        "backlog": len(pending),
        # Folded notes still on disk: an article already holds them, and the
        # backfill groups exist to verify that and delete them. They are work
        # for the compactor exactly as the backlog is.
        "retained": len(set(raw) & cited),
        # Uncompiled user/feedback notes. When the first listing tier
        # overflows, compacting these makes room in it.
        "behavior_backlog": sum(
            1 for n in pending if group_pool(raw[n]) == BEHAVIOR_POOL),
        # First-tier articles the merge groups would absorb. The other way to
        # make room there, and the only one left once every behavior note is
        # compiled. Zero here and in behavior_backlog means compaction cannot
        # help and the budget is too small.
        "merge_backlog": merge,
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


def in_cooldown(b: dict[str, Any]) -> bool:
    """True when a compiled article was written within the cooldown window."""
    since = b.get("since_compiled")
    return since is not None and since < cooldown_seconds()


def nudge_suppressed(b: dict[str, Any]) -> bool:
    """True when a backlog dict should NOT produce a compaction nudge.

    Retained notes count toward the threshold with the backlog: a store whose
    notes are all folded but still on disk has nothing left to compile and
    everything left to delete, and without this it never asks for the backfill.
    Articles due to merge count for the same reason: a fully compacted store
    has nothing else left to ask for.
    """
    work = b["backlog"] + b.get("retained", 0) + b.get("merge_backlog", 0)
    return work < b["threshold"] or in_cooldown(b)


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


def behavior_excess(s: Store) -> int:
    """Tokens by which the first listing tier exceeds its share of the budget.

    ``list_all`` lets that tier spend budget the lower tiers leave unused, so
    an excess is not yet a withheld entry. It is budget the lower tiers are
    owed, and the moment they grow into it, feedback entries are withheld. Zero
    or less means the tier fits its share.
    """
    budget = list_entry_budget()
    if not budget:
        return 0
    folded = s.folded_names()
    cost = sum(
        Store._entry_tokens(dict(r))
        for r in s.db.execute("SELECT name, type, description FROM mem")
        if Store._is_always_listed(r["type"]) and r["name"] not in folded)
    return cost - int(budget * Store.LIST_TIER_SHARES[0])


def pack(names: list[str], sizes: dict[str, int], limit: int, size: int) -> list[list[str]]:
    """Split ``names`` in order into runs of at most ``size`` and ``limit`` bytes."""
    runs: list[list[str]] = []
    total = 0
    for n in names:
        if runs and len(runs[-1]) < size and total + sizes[n] <= limit:
            runs[-1].append(n)
            total += sizes[n]
        else:
            runs.append([n])
            total = sizes[n]
    return runs


def merge_groups(s: Store, size: int) -> list[dict[str, Any]]:
    """Merge groups over first-tier articles, enough to clear ``behavior_excess``.

    Compaction folds notes into articles and never touched an article again,
    so the first tier only ever grew. Once every behavior note was compiled the
    tier had no drain at all: mxfs reached 120 feedback articles, 8,339 tokens
    against a 4,925-token share, and the note blamed the budget.

    A merge group is one existing article (``article``, also the seed) that
    stays and is rewritten in place, plus the articles it absorbs (``names``).
    Each absorbed article is deleted with the content hash in ``hashes``.
    Members are topic-mates where the clusterer finds them, packed under
    ``article_max_bytes() / MERGE_GROWTH`` so the merged article stays under the
    ceiling itself. Leftovers that cluster with nothing are packed into
    ``assorted`` merges, but only when the topical ones cannot clear the excess
    on their own.

    Excluded: articles a backfill group owns (two agents on one article
    overwrite each other), and articles written inside the cooldown window,
    which a compactor may still be rewriting.
    """
    excess = behavior_excess(s)
    if excess <= 0:
        return []
    owned = set(s.folded_by())
    cutoff = time.time() - cooldown_seconds()
    rows = {
        r["name"]: r for r in s.db.execute(
            "SELECT name, type, description, mtime, content_hash, "
            "length(CAST(body AS BLOB)) AS bytes FROM mem "
            "WHERE name LIKE 'compiled-%' ORDER BY mtime DESC")
        if Store._is_always_listed(r["type"]) and r["name"] not in owned
        and r["mtime"] < cutoff
    }
    sizes = {n: r["bytes"] for n, r in rows.items()}
    limit = int(article_max_bytes() / MERGE_GROWTH)

    topical: list[list[str]] = []
    loose: list[str] = []
    for c in _cluster(s, [dict(r) for r in rows.values()], size):
        for run in pack(c, sizes, limit, size):
            if len(run) > 1:
                topical.append(run)
            else:
                loose.extend(run)
    candidates = [(run, False) for run in topical] + [
        (run, True) for run in pack(loose, sizes, limit, size)]

    groups: list[dict[str, Any]] = []
    for run, assorted in candidates:
        if excess <= 0:
            break
        if len(run) < 2:
            continue
        seed, absorbed = run[0], run[1:]
        groups.append({
            "seed": seed,
            "kind": "merge",
            "assorted": assorted,
            "article": seed,
            "size": len(absorbed),
            "names": absorbed,
            "hashes": {n: rows[n]["content_hash"] for n in absorbed},
            "pool": BEHAVIOR_POOL,
            "article_type": rows[seed]["type"] or POOL_ARTICLE_TYPE[BEHAVIOR_POOL],
        })
        excess -= sum(Store._entry_tokens(dict(rows[n])) for n in absorbed)
    return groups


def build_groups(s: Store, size: int) -> list[dict[str, Any]]:
    """Partition the whole uncompiled backlog into disjoint groups — no LLM.

    One group is one compactor's unit of work. Without this, every compactor
    picked its own cluster out of ``memory_list()``, which meant (a) only one
    could run at a time, because two started together would converge on the
    same obvious cluster and write two articles about it, and (b) draining a
    backlog needed one session per group. A 227-note backlog against a per-run
    yield of ~9 notes is 25 sessions of nudging, while ordinary use adds notes
    the whole time — it never converges, and the partitioning ends up being
    done by hand.

    Clustering is greedy over the existing BM25 index: take the newest
    unclaimed note as a seed, pull its best-matching unclaimed topic-mates up
    to ``size``, repeat.

    Coverage is total. Clusters below ``MIN_CLUSTER`` are pooled into
    ``assorted`` groups instead of being dropped: an article lumping loosely
    related notes together reads worse than a focused one, but a note left out
    of every plan can never be cited, never retires from the listing, and
    raises the floor of a backlog that the nudge then complains about forever.
    """
    pending = pending_notes(s)
    # Cluster each pool on its own. A group's notes decide the article's
    # metadata.type, so a group spanning both pools would have to pick one
    # and mis-file the other half.
    clusters = {
        pool: _cluster(s, [n for n in pending if group_pool(n["type"]) == pool], size)
        for pool in (BEHAVIOR_POOL, KNOWLEDGE_POOL)
    }
    # Articles that still have folded sources on disk. Each is one backfill
    # group, never split: two agents rewriting the same article would
    # overwrite each other.
    backfill = [
        (article, names, s._row_type(article))
        for article, names in sorted(s.folded_by().items())
    ]
    merges = merge_groups(s, size)

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
        # Merges right behind the behavior notes: both make room in the first
        # listing tier, the one place a withheld entry cannot be searched for.
        if pool == BEHAVIOR_POOL:
            groups.extend(merges)

    # After the new-note groups: an unfolded note is missing from the listing,
    # a retained one is only taking space.
    for article, names, article_type in backfill:
        pool = group_pool(article_type)
        groups.append({
            "seed": article,
            "kind": "backfill",
            "size": len(names),
            "article": article,
            "names": names,
            "pool": pool,
            # Rewrite the article under its existing type; the retirement test
            # already passed for these notes under that type.
            "article_type": article_type or POOL_ARTICLE_TYPE[pool],
        })

    # Last: an article over the ceiling costs on every fetch but loses nothing.
    # memory_write now refuses to make one, so these predate the ceiling. One
    # owned by a backfill is left to it, since the backfill rewrite has to
    # split it anyway.
    owned = {a for a, _, _ in backfill}
    for article, (article_type, size_bytes) in sorted(oversized(s).items()):
        if article in owned:
            continue
        pool = group_pool(article_type)
        groups.append({
            "seed": article,
            "kind": "split",
            "size": 0,
            "article": article,
            "names": [],
            "bytes": size_bytes,
            "pool": pool,
            "article_type": article_type or POOL_ARTICLE_TYPE[pool],
        })
    return groups


def oversized(s: Store) -> dict[str, tuple[str | None, int]]:
    """``compiled-`` articles over ``article_max_bytes()``: name → (type, bytes)."""
    limit = article_max_bytes()
    return {
        r["name"]: (r["type"], r["bytes"]) for r in s.db.execute(
            "SELECT name, type, length(CAST(body AS BLOB)) AS bytes FROM mem "
            "WHERE name LIKE 'compiled-%'")
        if r["bytes"] > limit
    }


def outstanding(g: dict[str, Any], present: set[str], folded: set[str],
                big: set[str]) -> dict[str, Any] | None:
    """What is still to do in stored group ``g``, or None once it is done.

    A note in a topic or assorted group is done once an article folds it. A
    backfill source or an absorbed article is done once it is deleted. A merge
    whose surviving article is gone has nothing left to merge into. A split is
    done once its article is under the ceiling.
    """
    if g["kind"] == "split":
        return g if g["article"] in big else None
    if g["kind"] in ("backfill", "merge") and g["article"] not in present:
        return None
    if g["kind"] in ("backfill", "merge"):
        left = [n for n in g["names"] if n in present]
    else:
        left = [n for n in g["names"] if n in present and n not in folded]
    if not left:
        return None
    out = {**g, "names": left, "size": len(left)}
    if "hashes" in g:
        out["hashes"] = {n: g["hashes"][n] for n in left}
    return out


def load_plan(memory_dir: Path) -> dict[str, Any] | None:
    """The stored plan, or None when there is none or it has expired."""
    try:
        plan = json.loads((memory_dir / PLAN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(plan, dict) or time.time() - plan.get("created", 0) > plan_ttl():
        return None
    return plan


def save_plan(memory_dir: Path, groups: list[dict[str, Any]]) -> dict[str, Any]:
    """Store ``groups`` as the run's plan and drop the claims of earlier plans."""
    plan = {"id": f"{time.time_ns():x}", "created": time.time(), "groups": groups}
    path = memory_dir / PLAN_FILE
    staged = memory_dir / f"{PLAN_FILE}.{os.getpid()}"
    staged.write_text(json.dumps(plan), encoding="utf-8")
    os.replace(staged, path)
    claims = memory_dir / CLAIMS_DIR
    if claims.is_dir():
        for old in claims.iterdir():
            if old.name != plan["id"]:
                shutil.rmtree(old, ignore_errors=True)
    return plan


def claim(memory_dir: Path, plan_id: str, seed: str) -> bool:
    """Take group ``seed`` of plan ``plan_id``; False if another compactor holds it.

    O_EXCL makes the create atomic, so two compactors asking at once never both
    win. A claim older than ``claim_lease()`` belonged to a compactor that never
    finished: it is renamed aside first, which only one taker can do, then
    created fresh.
    """
    held = memory_dir / CLAIMS_DIR / plan_id / seed
    held.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            os.close(os.open(held, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            pass
        if attempt or time.time() - held.stat().st_mtime < claim_lease():
            return False
        stale = held.with_name(f"{seed}.stale.{os.getpid()}")
        try:
            os.rename(held, stale)
        except FileNotFoundError:
            return False
        stale.unlink(missing_ok=True)
    return False


def compaction_plan(
    memory_dir: Path,
    *,
    size: int | None = None,
    seed: str | None = None,
    take: bool = False,
) -> dict[str, Any]:
    """The run's plan: stored groups still outstanding, or a fresh partition.

    Computed once per run by ``build_groups`` and stored (``PLAN_FILE``), then
    served from storage until every group in it is done or it expires. A
    recomputed plan shifts as the run changes the store, which is how agents
    were told their seed was done before starting and how two groups ended up
    naming one article.

    ``take=True`` is how a compactor gets work: it returns the first
    outstanding group no other compactor has claimed, and claims it. So the
    session dispatches ``wave`` agents once, and they drain the plan between
    them; nothing waits on the session to launch more.

    ``seed`` returns that one group, for a caller addressing a group directly.
    A seed with nothing outstanding means that group is already compiled.
    """
    size = size or plan_group_size()
    with Store(memory_dir) as s:
        s.reindex()
        present = {r["name"] for r in s.db.execute("SELECT name FROM mem")}
        folded = s.folded_names()
        big = set(oversized(s))
        plan = load_plan(memory_dir)
        groups = []
        for g in (plan or {}).get("groups", []):
            left = outstanding(g, present, folded, big)
            if left is not None:
                groups.append(left)
        if not groups:
            groups = build_groups(s, size)
            plan = save_plan(memory_dir, groups) if groups else None
        backlog = len(pending_notes(s))

    payload: dict[str, Any] = {
        "status": "ok",
        "backlog": backlog,
        "retained": sum(g["size"] for g in groups if g["kind"] == "backfill"),
        "merge": sum(g["size"] for g in groups if g["kind"] == "merge"),
        "threshold": threshold(),
        "group_size": size,
        "wave": plan_wave_size(),
        "agent_groups": agent_groups(),
        "group_count": len(groups),
        "how": "Dispatch `wave` background memory-compactor agents, once, each with "
               f"{AGENT_CALL}. Each agent calls memory_compaction_plan(take=true), which "
               "hands it one unclaimed group and claims it, compiles that group, and "
               "takes the next, until status is `done` or it has compiled "
               "`agent_groups` groups. "
               "The article it writes must carry the group's `article_type` in its "
               "frontmatter: a behavior group (user/feedback notes) compiles to "
               "`feedback` so the article is listed in the same first-claim tier as the "
               "notes it retires, everything else to `project`. A `backfill` group is an "
               "existing article (`article`, also its seed) whose folded sources are "
               "still on disk: fold back anything the article dropped, rewrite it under "
               "the same name, then delete the sources. A `merge` group is an existing "
               "first-tier article (`article`, also its seed) and the articles it absorbs "
               "(`names`): fold every absorbed article into it, rewrite it under the same "
               "name and type, then delete each absorbed article with "
               "memory_delete(name, expect_hash=hashes[name]). A `split` group is an "
               "existing article (`article`) over the size ceiling: rewrite it under its "
               "name holding part of its content, and write the rest to new compiled- "
               "articles of the same type, each under the ceiling, each keeping the "
               "[[slug]] citations of the facts it holds; it deletes nothing. Every "
               "other group ends with each verified source removed via memory_delete.",
    }
    if take:
        payload["groups"] = []
        for g in groups:
            if claim(memory_dir, plan["id"], g["seed"]):
                payload["groups"] = [g]
                return payload
        payload["status"] = "done"
        payload["how"] = (
            "Every group in the plan is compiled or claimed by another compactor. "
            "Take nothing more: report what you compiled and stop."
        )
        return payload
    if seed is None:
        payload["groups"] = groups
        return payload

    payload["seed"] = seed
    match = [g for g in groups if g["seed"] == seed]
    payload["groups"] = match
    if not match:
        payload["status"] = "done"
        payload["how"] = (
            f"No group is seeded by `{seed}` — it has already been compiled and "
            "its sources deleted. Write nothing and report that. Do NOT pick a "
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

    This replaced the old ``claude -p`` run. It does not produce an article; it
    shows what a compile pass would work on. The compaction itself runs inside
    a Claude session, as background ``memory-compactor`` agents or the
    ``compile-memories`` skill.
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
        "how": "A Claude session compiles these into `compiled-<topic>` articles: it "
               "dispatches memory-compactor agents when the backlog is over threshold, "
               "or run the compile-memories skill to do it inline.",
    }
