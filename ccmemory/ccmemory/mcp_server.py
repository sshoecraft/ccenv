"""ccmemory MCP server — exposes the store as MCP tools.

Uses ``ccenvmcp`` (a stdlib-only, Python 3.9+ MCP shim) instead of the official
``mcp`` SDK, which requires Python >=3.10. The shim handles the JSON-RPC
handshake, capability negotiation, and stdio framing, so this module only
declares tools and dispatches to the store.

Tools:
  - memory_search(query, n=5)
  - memory_list(type?)
  - memory_get(name)
  - memory_write(name, type, description, body, tags?)
  - memory_stats()
  - memory_regen_index()
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from .store import (COMPILED_PREFIX, Store, DEFAULT_LIST_TOKEN_BUDGET,
                    LIST_ENVELOPE_TOKENS, list_entry_budget, list_token_budget)
from . import index_gen
from . import paths

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s", stream=sys.stderr)
log = logging.getLogger("ccmemory-mcp")


def _resolve_dir() -> Path:
    # For memory_write we create the startup-dir store if it doesn't exist yet
    # (must_exist=False). For read tools the caller gets an empty index, which
    # is the correct behavior. The anchor is just CWD, so this always resolves.
    d = paths.resolve_memory_dir(must_exist=False)
    d.mkdir(parents=True, exist_ok=True)
    # Self-heal the store's .gitignore so the derived index + macOS ._*
    # sidecars never leak into git. Runs on every project ccmemory touches,
    # on every machine — no per-project manual step. Idempotent.
    paths.ensure_gitignore(d)
    return d


def _list_note(memory_dir, counts: dict, *, include_folded: bool,
               type_filter: str | None = None) -> str:
    """What was withheld and what to do about it — carried IN-BAND.

    Two jobs. First, truncation must never be silent: a listing that quietly
    drops 1,500 entries reads as "that is everything this project knows".

    Second, it carries the compaction dispatch in-band. The caller is a model
    at session start that already reads this payload, and the SessionStart
    nudge does not reach every session, so the ask rides back here too: one
    background memory-compactor per group, then carry on. The compactors run
    as Agent-tool subagents of the live session, so the work never blocks the
    caller and never goes through `claude -p`. It is worded as the same request
    the SessionStart nudge makes, so a session that sees both dispatches once.
    """
    parts = []
    if counts["folded"]:
        parts.append(
            f"{counts['folded']} memories are already folded into `compiled-` "
            "articles and are not listed; they remain fully searchable via "
            "memory_search/memory_get, or pass include_folded=true."
        )
    # user/feedback overflow is reported on its own below, with its own remedy.
    raw_withheld = counts["withheld"] - counts.get("load_bearing_withheld", 0)
    if raw_withheld > 0:
        parts.append(
            f"{raw_withheld} further memories were withheld to stay within "
            "the listing token budget. The budget is spent in tiers: "
            "user/feedback first, then `compiled-` articles, then raw "
            "project/reference newest-first — so the withheld entries are the "
            "oldest raw notes. Reach them with memory_search(<topic>) or raise "
            "CCMEMORY_LIST_TOKEN_BUDGET."
        )
    try:
        from . import compile as compile_mod
        b = compile_mod.count_backlog(Path(memory_dir))
    except Exception:
        compile_mod = b = None

    # An overflowing first tier is the session's to fix, not the user's: the
    # user's only lever is an env var, and the real fixes — compacting the
    # behavior notes into always-listed articles, and merging those articles —
    # are dispatches the session can make itself. Only when neither is left
    # does the budget itself have to grow, and that is the one case worth
    # telling the user about.
    lb = counts.get("load_bearing_withheld")
    behavior = b["behavior_backlog"] if b else None
    merge = b.get("merge_backlog", 0) if b else 0
    if lb:
        lead = (
            f"{lb} user/feedback memories did not fit "
            + ("even in this filtered listing" if type_filter else
               "in the first budget tier")
            + ". They record behavior and corrections and have no topic to "
            "search for, so memory_search cannot recover them."
        )
        if behavior or merge:
            recover = "" if type_filter else (
                " Recover them now with memory_list(type=\"feedback\") and "
                "memory_list(type=\"user\").")
            work = " and ".join(filter(None, (
                f"{behavior} user/feedback notes are still uncompiled" if behavior else "",
                f"{merge} first-tier articles are due to merge" if merge else "")))
            parts.append(
                f"{lead}{recover} {work}, and that compaction is what makes "
                "room: memory_compaction_plan() lists the first-tier groups "
                "first, so they go in the first wave of the compactor dispatch. "
                "This resolves itself — do not report it to the user."
            )
        elif behavior == 0:
            parts.append(
                f"WARNING: {lead} Every user/feedback note is already compiled "
                "and no first-tier article can be merged, so compaction cannot "
                "make room. Tell the user: "
                f"CCMEMORY_LIST_TOKEN_BUDGET (currently {list_token_budget()}) "
                "is too small for this project's behavior memories, and raising "
                "it is the fix."
            )
        else:
            parts.append(
                f"{lead} Recover them with memory_list(type=\"feedback\") and "
                "memory_list(type=\"user\")."
            )
    if include_folded:
        parts.append("include_folded=true: folded memories are included in this listing.")

    if b is not None:
        # The overflow asks for the dispatch even under the threshold: the
        # threshold paces routine compaction, and a withheld correction is not
        # routine. The cooldown still applies — a compile pass that just ran
        # may still have agents in flight on these notes.
        overflow = bool(lb and (behavior or merge)) and not compile_mod.in_cooldown(b)
        if overflow or not compile_mod.nudge_suppressed(b):
            merging = (f", and {merge} first-tier articles are due to merge into "
                       "their topic-mates") if merge else ""
            parts.append(
                f"Compaction backlog: {b['backlog']} memories have never been "
                f"folded into a compiled- article{',' if merge else ', and'} "
                f"{b.get('retained', 0)} folded ones are still on disk awaiting "
                f"deletion{merging} (threshold {b['threshold']}). "
                "Do not stop what you are doing: unless you already dispatched "
                f"them this session, make {compile_mod.plan_wave_size()} background "
                f"{compile_mod.AGENT_CALL} calls, once — each agent claims groups "
                "from the stored plan until none are left, so there is nothing "
                "to relaunch — then carry straight on with the user's request. "
                "Do not wait on the agents and do not compact inline."
            )

    return " ".join(parts)


def _text(s: str) -> list[dict]:
    # The MCP SDK wraps the returned list into the `content` field on
    # the response; do NOT pre-wrap or it becomes double-encoded.
    return [{"type": "text", "text": s}]


def _err(s: str) -> list[dict]:
    return [{"type": "text", "text": s}]


def build_app():
    """Construct the ccenvmcp app with all tools registered.

    Separated from ``serve()`` (which also performs boot-time self-install and
    runs the stdio loop) so the tool surface can be exercised in tests.
    """
    from ccenvmcp import FastMCP

    app = FastMCP("ccmemory")

    # Schemas are hand-written (rather than introspected) to preserve the
    # memory_write `type` enum, per-field descriptions, and defaults exactly.
    SCHEMAS = {
        "memory_search": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search terms"},
                "n": {"type": "integer", "description": "max results", "default": 5},
            },
            "required": ["query"],
        },
        "memory_list": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["user", "feedback", "project", "reference"], "description": "optional type filter"},
                "include_folded": {"type": "boolean", "description": "also return memories already folded into a compiled- article (default false)", "default": False},
                "limit": {"type": "integer", "description": "max entries to return; 0 = budget-bounded only", "default": 0},
            },
        },
        "memory_get": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        "memory_write": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "kebab-case slug, used as filename"},
                "type": {"type": "string", "enum": ["user", "feedback", "project", "reference"]},
                "description": {"type": "string", "description": "one-line summary for the index"},
                "body": {"type": "string", "description": "markdown body"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name", "type", "description", "body"],
        },
        "memory_delete": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "slug of a raw note a compiled- article folds, or of an article a merge absorbed"},
                "expect_hash": {"type": "string", "description": "required for a compiled- article: its hash from the merge group's `hashes`"},
            },
            "required": ["name"],
        },
        "memory_stats": {"type": "object", "properties": {}},
        "memory_regen_index": {"type": "object", "properties": {}},
        "memory_compaction_plan": {
            "type": "object",
            "properties": {
                "seed": {"type": "string", "description": "return only the group seeded by this slug"},
                "take": {"type": "boolean", "description": "a compactor asking for work: claim and return the next unclaimed group", "default": False},
            },
        },
    }

    def dispatch(name: str, arguments: dict):
        try:
            d = _resolve_dir()
        except RuntimeError as e:
            return _err(str(e))

        try:
            if name == "memory_search":
                q = arguments.get("query") or ""
                n = int(arguments.get("n") or 5)
                with Store(d) as s:
                    s.reindex()
                    results = s.search(q, limit=n)
                return _text(json.dumps(results, indent=2, default=str))

            if name == "memory_list":
                type_filter = arguments.get("type") or None
                include_folded = bool(arguments.get("include_folded") or False)
                limit = int(arguments.get("limit") or 0)
                entry_budget = list_entry_budget()
                with Store(d) as s:
                    s.reindex()
                    results, counts = s.list_all(
                        type_filter=type_filter,
                        include_folded=include_folded,
                        token_budget=entry_budget,
                        limit=limit,
                    )
                    note = _list_note(d, counts, include_folded=include_folded,
                                      type_filter=type_filter)
                payload = {**counts, "note": note, "memories": results}
                # Compact separators, not indent=2. Pretty-printing cost ~96
                # chars per entry in indentation and line breaks alone — 29% of
                # the payload on mxfs — for a document only a model reads.
                return _text(json.dumps(payload, separators=(",", ":"), default=str))

            if name == "memory_get":
                slug = arguments.get("name") or ""
                with Store(d) as s:
                    s.reindex()
                    m = s.get(slug)
                if not m:
                    return _err(f"not found: {slug}")
                return _text(m.path.read_text(encoding="utf-8"))

            if name == "memory_write":
                slug = arguments["name"]
                mtype = arguments["type"]
                desc = arguments["description"]
                body = arguments["body"]
                tags = arguments.get("tags") or []
                if slug.startswith(COMPILED_PREFIX):
                    from . import compile as compile_mod
                    size = len(body.strip().encode("utf-8"))
                    ceiling = compile_mod.article_max_bytes()
                    if size > ceiling:
                        return _err(
                            f"refused: compiled- article {slug} is {size} bytes, over the "
                            f"{ceiling}-byte ceiling (CCMEMORY_ARTICLE_MAX_BYTES). memory_get "
                            "returns a whole article, so split it by subtopic into compiled- "
                            "articles each under the ceiling, of the same type, each citing "
                            "as [[slug]] every input whose facts it holds. Nothing was written.")
                cap = index_gen.DEFAULT_DESC_CAP
                if len(desc) > cap:
                    desc = desc[: cap - 1].rstrip() + "…"
                front = ["---", f"name: {slug}", f"description: {desc}", "metadata:", f"  type: {mtype}"]
                if tags:
                    front.append("tags: [" + ", ".join(tags) + "]")
                front.append("---")
                d.mkdir(parents=True, exist_ok=True)
                path = d / f"{slug}.md"
                path.write_text("\n".join(front) + "\n\n" + body.strip() + "\n", encoding="utf-8")
                with Store(d) as s:
                    s.reindex()
                return _text(f"wrote {path}")

            if name == "memory_delete":
                slug = arguments.get("name") or ""
                with Store(d) as s:
                    try:
                        result = s.delete_folded(
                            slug, expect_hash=arguments.get("expect_hash") or None)
                    except ValueError as e:
                        return _err(str(e))
                return _text(json.dumps(result, indent=2))

            if name == "memory_stats":
                with Store(d) as s:
                    s.reindex()
                    st = s.stats()
                    # Surface listing pressure BEFORE it hurts: an unbounded
                    # store degrades silently until session-start cost is
                    # already unpayable.
                    full, _ = s.list_all(include_folded=True)
                    st["folded"] = len(s.folded_names())
                    # Both token figures are payload costs, envelope included,
                    # so they are directly comparable to each other and to
                    # list_budget. Mixing entries-only and payload-inclusive
                    # numbers in the same object invites exactly the wrong read.
                    st["list_tokens_unbounded"] = (
                        sum(s._entry_tokens(e) for e in full) + LIST_ENVELOPE_TOKENS)
                    budget = list_token_budget()
                    bounded, counts = s.list_all(token_budget=list_entry_budget())
                    # Must be the cost of the payload memory_list actually
                    # ships, envelope included — this field exists to be
                    # trusted as a budget check, and reporting entries-only
                    # is what let a 14.9k listing self-report as 10.4k.
                    st["list_tokens_actual"] = (
                        sum(s._entry_tokens(e) for e in bounded) + LIST_ENVELOPE_TOKENS)
                    st["list_budget"] = budget
                    st["list_counts"] = counts
                    return _text(json.dumps(st, indent=2))

            if name == "memory_regen_index":
                result = index_gen.write(d)
                return _text(json.dumps(result, indent=2))

            if name == "memory_compaction_plan":
                from . import compile as compile_mod
                plan = compile_mod.compaction_plan(d, seed=arguments.get("seed") or None,
                                                   take=bool(arguments.get("take")))
                return _text(json.dumps(plan, indent=2, default=str))

            return _err(f"unknown tool: {name}")
        except Exception as e:
            log.exception("tool %s failed", name)
            return _err(f"{type(e).__name__}: {e}")

    @app.tool(
        name="memory_search",
        description="Full-text search over project memory. Returns ranked list of {name, type, description, age_days, path}.",
        schema=SCHEMAS["memory_search"],
    )
    def memory_search(**kwargs):
        return dispatch("memory_search", kwargs)

    @app.tool(
        name="memory_list",
        description="List memories (metadata only — name, type, description, age), newest first. Use when you need the inventory, not a ranked subset. Bounded by a token budget spent in tiers, newest-first inside each: user/feedback, then `compiled-` articles, then raw project/reference. EVERY tier is trimmable, including the first — `load_bearing_withheld` in the returned counts says how many user/feedback memories did not fit. Passing a type filter (user|feedback|project|reference) gives that one type the entire budget, which is how you recover them. Memories already folded into a `compiled-` article are omitted (they stay searchable) unless include_folded=true. The returned `note` field states exactly what was withheld — read it.",
        schema=SCHEMAS["memory_list"],
    )
    def memory_list(**kwargs):
        return dispatch("memory_list", kwargs)

    @app.tool(
        name="memory_get",
        description="Fetch one memory file's full contents by name.",
        schema=SCHEMAS["memory_get"],
    )
    def memory_get(**kwargs):
        return dispatch("memory_get", kwargs)

    @app.tool(
        name="memory_write",
        description=(
            "Create or overwrite a memory file. Description is capped at 150 chars. A compiled- "
            "article over the size ceiling (CCMEMORY_ARTICLE_MAX_BYTES, default 24 KiB) is refused: "
            "split it into several compiled- articles."
        ),
        schema=SCHEMAS["memory_write"],
    )
    def memory_write(**kwargs):
        return dispatch("memory_write", kwargs)

    @app.tool(
        name="memory_delete",
        description=(
            "Delete a raw note that a compiled- article has folded in — the compaction step that "
            "removes a source once the article holding it has been verified. Links to it elsewhere "
            "become plain `slug` text. Refuses any note no article folds. A compiled- article goes "
            "only as the absorbed half of a merge group: another article must cite it and "
            "`expect_hash` must match the hash the plan gave, or it is refused."
        ),
        schema=SCHEMAS["memory_delete"],
    )
    def memory_delete(**kwargs):
        return dispatch("memory_delete", kwargs)

    @app.tool(
        name="memory_stats",
        description="Counts by type, DB size, DB path, and memory_list cost (bounded vs unbounded tokens, folded count).",
        schema=SCHEMAS["memory_stats"],
    )
    def memory_stats(**kwargs):
        return dispatch("memory_stats", kwargs)

    @app.tool(
        name="memory_regen_index",
        description="Regenerate MEMORY.md from frontmatter descriptions.",
        schema=SCHEMAS["memory_regen_index"],
    )
    def memory_regen_index(**kwargs):
        return dispatch("memory_regen_index", kwargs)

    @app.tool(
        name="memory_compaction_plan",
        description=(
            "Partition the uncompiled memory backlog, plus every article whose folded sources are "
            "still on disk (kind 'backfill') and, when the first listing tier is over its budget "
            "share, first-tier articles to merge (kind 'merge'), into disjoint compactor groups. "
            "The plan is stored and reused for the whole run, so groups do not shift while it is worked. "
            "Returns {backlog, retained, merge, group_count, group_size, wave, agent_groups, "
            "groups:[{seed, kind, size, names}]}. A compactor passes `take: true` to claim and get "
            "the next unclaimed group; status 'done' means none is left. Pass `seed` to fetch one "
            "group; status 'done' means that group is already compiled — write nothing."
        ),
        schema=SCHEMAS["memory_compaction_plan"],
    )
    def memory_compaction_plan(**kwargs):
        return dispatch("memory_compaction_plan", kwargs)

    return app


def serve() -> int:
    # Autoinstall hooks on MCP server boot. This is ccmemory's real entry
    # point (the user "runs" ccmemory by having Claude Code spawn the MCP
    # server), so it's the natural choke point for self-install — same
    # logic as /src/ccloop's runner calling ensure_registered() at start,
    # just applied to ccmemory's actual entry point instead of a CLI.
    from . import installer, migrate as migrate_mod
    installer.autoinstall_quiet()
    # Same pattern for the legacy-dir → project-local-dir migration.
    migrate_mod.automigrate_quiet()

    app = build_app()
    app.run()
    return 0
