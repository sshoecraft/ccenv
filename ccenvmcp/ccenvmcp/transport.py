"""Stdlib stdio JSON-RPC 2.0 transport + a FastMCP-compatible server.

This is a drop-in replacement for the slice of the official ``mcp`` SDK that
the ccenv tools-only servers (ccmemory, ccusage, ccteam) actually use. The SDK
requires Python >=3.10 (it pulls in pydantic v2 / anyio); this module is pure
stdlib and runs on 3.9 — the system Python on Debian 11, RHEL/Alma/Rocky 9,
Ubuntu 20.04, and Raspberry Pi OS.

MCP stdio framing is newline-delimited JSON-RPC 2.0 over stdin/stdout. For a
tools-only server the protocol surface is tiny:

    initialize                 -> echo protocolVersion + capabilities/serverInfo/instructions
    notifications/initialized  -> (notification, no reply)
    tools/list                 -> {tools: [{name, description, inputSchema}]}
    tools/call                 -> {content: [...], isError}
    ping                       -> {}

All diagnostics go to stderr — stdout is reserved for protocol frames.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import sys

log = logging.getLogger("ccenvmcp")

PROTOCOL_VERSION = "2024-11-05"


# --------------------------------------------------------------------------
# Annotation -> JSON Schema (3.9-safe).
#
# Under `from __future__ import annotations` (which all ccenv packages use),
# function annotations are STRINGS, e.g. "int | None", "list[str]". Evaluating
# "int | None" at runtime raises TypeError on 3.9, so we must NOT call
# typing.get_type_hints(); we parse the annotation text ourselves. We also
# handle real type objects (str, dict, ...) for modules without the future
# import.
# --------------------------------------------------------------------------

_BASE = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "dict": {"type": "object"},
    "list": {"type": "array"},
    "Dict": {"type": "object"},
    "List": {"type": "array"},
    "Any": {},
    "object": {},
}


def _token_to_schema(token: str) -> dict:
    token = token.strip().replace("typing.", "")

    # Union via PEP 604 ("X | None") — drop None, recurse on the remainder.
    if "|" in token:
        parts = [p.strip() for p in token.split("|")]
        parts = [p for p in parts if p not in ("None", "NoneType")]
        if len(parts) == 1:
            return _token_to_schema(parts[0])
        return {}  # genuine multi-type union: leave unconstrained

    # Optional[X] -> X
    m = re.match(r"^Optional\[(.+)\]$", token)
    if m:
        return _token_to_schema(m.group(1))

    # Union[...] -> drop None, recurse if single remainder
    m = re.match(r"^Union\[(.+)\]$", token)
    if m:
        inner = [p.strip() for p in m.group(1).split(",")]
        inner = [p for p in inner if p not in ("None", "NoneType")]
        if len(inner) == 1:
            return _token_to_schema(inner[0])
        return {}

    # list[X] / List[X]
    m = re.match(r"^(?:list|List)\[(.+)\]$", token)
    if m:
        return {"type": "array", "items": _token_to_schema(m.group(1))}

    # dict[K, V] / Dict[K, V] -> object (keys are strings in JSON anyway)
    if re.match(r"^(?:dict|Dict)\[", token):
        return {"type": "object"}

    return dict(_BASE.get(token, {}))


def _annotation_token(annotation) -> str:
    if isinstance(annotation, str):
        return annotation.strip()
    name = getattr(annotation, "__name__", None)
    return name or str(annotation)


def build_input_schema(func) -> dict:
    """Build a JSON Schema object for a tool from its signature.

    Required iff the parameter has no default. ``self``/``*args``/``**kwargs``
    are skipped. Unknown annotations produce an unconstrained property.
    """
    sig = inspect.signature(func)
    properties: dict = {}
    required: list = []
    for pname, p in sig.parameters.items():
        if pname == "self":
            continue
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if p.annotation is inspect.Parameter.empty:
            properties[pname] = {}
        else:
            properties[pname] = _token_to_schema(_annotation_token(p.annotation))
        if p.default is inspect.Parameter.empty:
            required.append(pname)
    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _normalize_content(result):
    """Coerce a tool's return value into MCP ``content`` blocks."""
    if isinstance(result, str):
        return [{"type": "text", "text": result}]
    # Already a list of content blocks ({"type": ...}) — pass through.
    if isinstance(result, list) and result and all(
        isinstance(x, dict) and "type" in x for x in result
    ):
        return result
    if isinstance(result, (dict, list)):
        return [{"type": "text", "text": json.dumps(result, default=str)}]
    if result is None:
        return [{"type": "text", "text": ""}]
    return [{"type": "text", "text": str(result)}]


class _Tool:
    __slots__ = ("name", "description", "schema", "func", "is_async")

    def __init__(self, name, description, schema, func, is_async):
        self.name = name
        self.description = description
        self.schema = schema
        self.func = func
        self.is_async = is_async


class FastMCP:
    """Minimal FastMCP-compatible tools-only server over stdio.

    Compatible surface:
      - ``FastMCP(name)`` / ``FastMCP(name=..., instructions=...)``
      - ``@app.tool()`` on sync or async functions
      - ``@app.tool(name=..., description=..., schema=...)`` explicit overrides
      - ``app.run()`` (sync entry) and ``await app.run_stdio_async()`` (async)
    """

    def __init__(self, name="mcp", instructions="", version=None):
        self.name = name
        self.instructions = instructions or ""
        if version is None:
            try:
                from . import __version__ as version
            except Exception:
                version = "0.1.0"
        self.version = version
        self._tools: dict = {}

    def tool(self, func=None, *, name=None, description=None, schema=None):
        def register(fn):
            tname = name or fn.__name__
            if description is not None:
                tdesc = description
            else:
                tdesc = inspect.getdoc(fn) or ""
            tschema = schema if schema is not None else build_input_schema(fn)
            self._tools[tname] = _Tool(
                tname, tdesc, tschema, fn, inspect.iscoroutinefunction(fn)
            )
            return fn

        # Support both @app.tool() and (defensively) @app.tool
        if func is not None:
            return register(func)
        return register

    # -- protocol -----------------------------------------------------------

    def _ok(self, mid, result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def _error(self, mid, code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    async def _handle(self, msg):
        method = msg.get("method")
        mid = msg.get("id")
        is_request = "id" in msg

        if method == "initialize":
            params = msg.get("params") or {}
            result = {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": self.name, "version": self.version},
            }
            if self.instructions:
                result["instructions"] = self.instructions
            return self._ok(mid, result)

        if method == "notifications/initialized":
            return None

        if method == "ping":
            return self._ok(mid, {})

        if method == "tools/list":
            tools = [
                {"name": t.name, "description": t.description, "inputSchema": t.schema}
                for t in self._tools.values()
            ]
            return self._ok(mid, {"tools": tools})

        if method == "tools/call":
            return await self._call_tool(mid, msg.get("params") or {})

        if is_request:
            return self._error(mid, -32601, "method not found: %s" % method)
        return None

    async def _call_tool(self, mid, params):
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = self._tools.get(name)
        if tool is None:
            return self._ok(
                mid,
                {"content": [{"type": "text", "text": "unknown tool: %s" % name}],
                 "isError": True},
            )
        try:
            if tool.is_async:
                result = await tool.func(**args)
            else:
                result = tool.func(**args)
        except Exception as exc:  # tool errors are reported in-band, not as JSON-RPC errors
            log.exception("tool %s failed", name)
            return self._ok(
                mid,
                {"content": [{"type": "text", "text": "%s: %s" % (type(exc).__name__, exc)}],
                 "isError": True},
            )
        return self._ok(mid, {"content": _normalize_content(result), "isError": False})

    # -- transport ----------------------------------------------------------

    def _write(self, obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    async def _stdin_reader(self):
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        return reader

    async def run_stdio_async(self):
        reader = await self._stdin_reader()
        while True:
            line = await reader.readline()
            if not line:  # EOF — client closed stdin
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                log.warning("dropping non-JSON line on stdin")
                continue
            try:
                resp = await self._handle(msg)
            except Exception:
                log.exception("dispatch failed")
                resp = self._error(msg.get("id"), -32603, "internal error")
            if resp is not None:
                self._write(resp)

    def run(self, transport=None):
        asyncio.run(self.run_stdio_async())
