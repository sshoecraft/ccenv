"""ccmemory MCP server — exposes the store as MCP tools.

Uses the official Python ``mcp`` SDK (same pattern as /src/influx_mcp). The
SDK handles protocol handshake, capability negotiation, and stdio framing,
so this module only declares tools and dispatches to the store.

Tools:
  - memory_search(query, n=5)
  - memory_get(name)
  - memory_write(name, type, description, body, tags?)
  - memory_stats()
  - memory_regen_index()
"""

from __future__ import annotations

import asyncio
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
    # For memory_write we want to create the project-local dir if it
    # doesn't exist yet (must_exist=False). For read tools the caller
    # will get an empty index, which is the correct behavior.
    d = paths.resolve_memory_dir(must_exist=False)
    if not d:
        raise RuntimeError("no memory dir resolvable (set CCMEMORY_DIR or run inside a project)")
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

    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        import mcp.types as types
    except ImportError:
        sys.stderr.write(
            "ccmemory mcp: the `mcp` package is not installed.\n"
            "Install with: pip install mcp\n"
        )
        return 1

    app = Server("ccmemory")

    @app.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="memory_search",
                description="Full-text search over project memory. Returns ranked list of {name, type, description, age_days, path}.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "search terms"},
                        "n": {"type": "integer", "description": "max results", "default": 5},
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="memory_get",
                description="Fetch one memory file's full contents by name.",
                inputSchema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            ),
            types.Tool(
                name="memory_write",
                description="Create or overwrite a memory file. Description is capped at 150 chars.",
                inputSchema={
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
            ),
            types.Tool(
                name="memory_stats",
                description="Counts by type, DB size, and DB path.",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="memory_regen_index",
                description="Regenerate MEMORY.md from frontmatter descriptions.",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @app.call_tool()
    async def call_tool(name: str, arguments: dict):
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

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())

    asyncio.run(_run())
    return 0
