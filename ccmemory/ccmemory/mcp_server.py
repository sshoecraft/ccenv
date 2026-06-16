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
                with Store(d) as s:
                    s.reindex()
                    results = s.list_all(type_filter=type_filter)
                return _text(json.dumps(results, indent=2, default=str))

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
                    return _text(json.dumps(s.stats(), indent=2))

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
        description="List all memories (metadata only — name, type, description, age, path), newest first. Use when you need every memory, not a ranked subset. Optional type filter (user|feedback|project|reference).",
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
        description="Counts by type, DB size, and DB path.",
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
