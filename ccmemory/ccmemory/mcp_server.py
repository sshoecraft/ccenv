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
import os
import sys
from pathlib import Path

from .store import Store
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


#: Token ceiling for one memory_list payload. The session protocol makes
#: memory_list the mandatory first call of EVERY session, so its cost is paid
#: before the user's first message and, under ccloop, again on every relay. An
#: unbounded listing measured ~171k tokens (86% of a 200k window) on a
#: 1,695-memory store. Small stores never reach this and are unaffected.
DEFAULT_LIST_TOKEN_BUDGET = 6000


#: Tokens held back from the entry budget for the payload envelope — the
#: counts object and the `note`, which runs to ~760 chars on a store with
#: folded/withheld/COMPACTION-DUE clauses all firing. Budgeting only the
#: entries left this unmodelled and every listing overshot by ~210 tokens.
LIST_ENVELOPE_TOKENS = 300


def list_token_budget() -> int:
    """Per-call memory_list token ceiling; 0 disables bounding entirely."""
    raw = os.environ.get("CCMEMORY_LIST_TOKEN_BUDGET")
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return DEFAULT_LIST_TOKEN_BUDGET


def _list_note(memory_dir, counts: dict, *, include_folded: bool) -> str:
    """What was withheld and what to do about it — carried IN-BAND.

    Two jobs. First, truncation must never be silent: a listing that quietly
    drops 1,500 entries reads as "that is everything this project knows".

    Second, this is where the compaction directive goes. The SessionStart
    reminder that suggests compacting is demonstrably ignored, and the MCP
    server has no model of its own to compact with (the claude -p path was
    removed from compile.py because it bills metered credit). But the caller
    of memory_list IS a model, at session start, with the skill available and
    free to run. So the ask rides back on the payload it already reads.
    """
    parts = []
    if counts["folded"]:
        parts.append(
            f"{counts['folded']} memories are already folded into `compiled-` "
            "articles and are not listed; they remain fully searchable via "
            "memory_search/memory_get, or pass include_folded=true."
        )
    if counts["withheld"]:
        parts.append(
            f"{counts['withheld']} further memories were withheld to stay within "
            "the listing token budget. The budget is spent in tiers: "
            "user/feedback first, then `compiled-` articles, then raw "
            "project/reference newest-first — so the withheld entries are the "
            "oldest raw notes. Reach them with memory_search(<topic>) or raise "
            "CCMEMORY_LIST_TOKEN_BUDGET."
        )
    if counts.get("load_bearing_withheld"):
        parts.append(
            f"WARNING: {counts['load_bearing_withheld']} user/feedback memories did "
            "not fit even in the first budget tier. These record behavior and "
            "corrections and have no topic to search for, so unlike the notes "
            "above they are NOT recoverable with memory_search — you are missing "
            "instructions you cannot know to ask about. Raise "
            "CCMEMORY_LIST_TOKEN_BUDGET, or call memory_list(type=\"feedback\")."
        )
    if include_folded:
        parts.append("include_folded=true: folded memories are included in this listing.")

    try:
        from . import compile as compile_mod
        b = compile_mod.count_backlog(Path(memory_dir))
        if not compile_mod.nudge_suppressed(b):
            parts.append(
                f"COMPACTION DUE: {b['backlog']} memories have never been folded "
                f"into a compiled- article (threshold {b['threshold']}). Do NOT "
                "stop the user's task to do this yourself — dispatch it: "
                "Agent(subagent_type=\"memory-compactor\", prompt=\"Compact this "
                "project's memory backlog.\"). It runs in the background on "
                "sonnet and reads the memory bodies into its own context, not "
                "yours. Until it runs this backlog keeps growing and this "
                "listing keeps degrading."
            )
    except Exception:
        pass

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
        "memory_stats": {"type": "object", "properties": {}},
        "memory_regen_index": {"type": "object", "properties": {}},
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
                budget = list_token_budget()
                entry_budget = max(1, budget - LIST_ENVELOPE_TOKENS) if budget else 0
                with Store(d) as s:
                    s.reindex()
                    results, counts = s.list_all(
                        type_filter=type_filter,
                        include_folded=include_folded,
                        token_budget=entry_budget,
                        limit=limit,
                    )
                    note = _list_note(d, counts, include_folded=include_folded)
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
                    entry_budget = max(1, budget - LIST_ENVELOPE_TOKENS) if budget else 0
                    bounded, counts = s.list_all(token_budget=entry_budget)
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
        description="List memories (metadata only — name, type, description, age), newest first. Use when you need the inventory, not a ranked subset. Always returns every user/feedback/reference memory in full; project notes fill a token budget, newest-first. Memories already folded into a `compiled-` article are omitted (they stay searchable) unless include_folded=true. The returned `note` field states exactly what was withheld — read it. Optional type filter (user|feedback|project|reference).",
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
        description="Create or overwrite a memory file. Description is capped at 150 chars.",
        schema=SCHEMAS["memory_write"],
    )
    def memory_write(**kwargs):
        return dispatch("memory_write", kwargs)

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
