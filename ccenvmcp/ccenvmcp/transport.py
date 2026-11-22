"""Stdlib stdio JSON-RPC 2.0 transport + a FastMCP-compatible server.

This is a drop-in replacement for the slice of the official ``mcp`` SDK that
the ccenv tools-only servers (ccmemory, ccusage) actually use. The SDK
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
    notifications/cancelled    -> stops the call, if its tool is cancellable

Each tools/call runs in its own task, so a tool that blocks for hours (a wait,
a lock, a long command) does not stall pings or other calls. A sync tool still
runs start to finish on the event loop with no await in it, so sync tools never
overlap one another and need no thread safety; only async tools can interleave,
and only at their own awaits.

A tool that declares a parameter named ``ctx`` receives a ``Context`` and can
send progress notifications; Claude Code aborts a call that is silent for
1800 s, so a long wait reports progress well inside that. A tool registered
with ``cancellable=True`` is stopped when the client cancels its call or closes
stdin; every other tool runs to completion and its cancel notice is ignored.

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

#: A tool parameter with this name is given a Context instead of an argument.
CONTEXT_PARAM = "ctx"


class Context:
    """What a tool that declares ``ctx`` gets: progress reporting for its call."""

    def __init__(self, server, token):
        self.server = server
        self.token = token

    async def report_progress(self, progress, total=None, message=None):
        """Send notifications/progress for this call. A client that sent no
        progressToken gets nothing; this never raises, because a heartbeat must
        never end the wait it exists to protect."""
        if self.token is None:
            return
        params = {"progressToken": self.token, "progress": progress}
        if total is not None:
            params["total"] = total
        if message:
            params["message"] = message
        try:
            self.server._write({"jsonrpc": "2.0", "method": "notifications/progress",
                                "params": params})
        except Exception:
            log.exception("progress notification failed")


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
        if pname in ("self", CONTEXT_PARAM):
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
    __slots__ = ("name", "description", "schema", "func", "is_async", "wants_ctx",
                 "cancellable")

    def __init__(self, name, description, schema, func, is_async, cancellable=False):
        self.name = name
        self.description = description
        self.schema = schema
        self.func = func
        self.is_async = is_async
        self.wants_ctx = CONTEXT_PARAM in inspect.signature(func).parameters
        self.cancellable = cancellable


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
        # request id -> (task, tool) for every tools/call still running
        self.inflight: dict = {}

    def tool(self, func=None, *, name=None, description=None, schema=None,
             cancellable=False):
        def register(fn):
            tname = name or fn.__name__
            if description is not None:
                tdesc = description
            else:
                tdesc = inspect.getdoc(fn) or ""
            tschema = schema if schema is not None else build_input_schema(fn)
            self._tools[tname] = _Tool(
                tname, tdesc, tschema, fn, inspect.iscoroutinefunction(fn), cancellable
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
        if tool.wants_ctx:
            token = (params.get("_meta") or {}).get("progressToken")
            args = dict(args, **{CONTEXT_PARAM: Context(self, token)})
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

    async def answer(self, msg):
        """Handle one message and write its reply, if it has one."""
        try:
            resp = await self._handle(msg)
        except asyncio.CancelledError:
            return  # a cancelled call gets no reply
        except Exception:
            log.exception("dispatch failed")
            resp = self._error(msg.get("id"), -32603, "internal error")
        if resp is not None:
            self._write(resp)

    def start_call(self, msg):
        """Run a tools/call in its own task, so a long call blocks nothing else."""
        mid = msg.get("id")
        tool = self._tools.get((msg.get("params") or {}).get("name"))
        task = asyncio.ensure_future(self.answer(msg))
        self.inflight[mid] = (task, tool)
        task.add_done_callback(lambda done, mid=mid: self.inflight.pop(mid, None))

    def cancel_call(self, params):
        """notifications/cancelled: stop the call only if its tool allows it."""
        entry = self.inflight.get((params or {}).get("requestId"))
        if entry and entry[1] is not None and entry[1].cancellable:
            entry[0].cancel()

    async def finish(self):
        """stdin closed: the client is gone. Cancellable calls stop (so their
        cleanup runs); the others finish, though nobody will read the reply."""
        for task, tool in list(self.inflight.values()):
            if tool is not None and tool.cancellable:
                task.cancel()
        pending = [task for task, _ in self.inflight.values()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

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
            method = msg.get("method")
            if method == "tools/call" and "id" in msg:
                self.start_call(msg)
            elif method == "notifications/cancelled":
                self.cancel_call(msg.get("params"))
            else:
                await self.answer(msg)
        await self.finish()

    def run(self, transport=None):
        asyncio.run(self.run_stdio_async())
