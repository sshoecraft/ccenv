# ccenvmcp

A tiny, **stdlib-only**, Python **3.9+** MCP server shim. It reproduces the
exact slice of the official `mcp` PyPI SDK that the ccenv tools-only servers
(`ccmemory`, `ccusage`, `ccteam`) consume — without the SDK's `requires-python
>= 3.10` floor.

## Why this exists

Every release of the official `mcp` SDK requires Python `>=3.10` (it pulls in
pydantic v2 + anyio). But Python **3.9** is the *system* interpreter on a large
swath of current stable servers:

- Debian 11 / Raspberry Pi OS (3.9.2)
- RHEL / AlmaLinux / Rocky 9 (3.9)
- Ubuntu 20.04 (3.9)

`ccenv` is meant to drop onto any of those without installing a second
interpreter (pyenv/conda/uv). The only thing forcing 3.10 across the three MCP
servers was the SDK itself — `nats-py` needs only `>=3.7`, `watchfiles>=0.21`
resolves to a 3.9-compatible build, and all packages already use
`from __future__ import annotations` so PEP 604 unions are never evaluated. So
we replaced the SDK with this shim and lowered every server to `>=3.9`.

## Public API (FastMCP-compatible)

```python
from ccenvmcp import FastMCP

app = FastMCP("myserver")                 # or FastMCP(name=..., instructions=...)

@app.tool()
def hello(name: str, loud: bool = False) -> str:
    "Say hello."                          # docstring -> tool description
    return ("HELLO " + name) if loud else ("hello " + name)

@app.tool()
async def slow(x: int | None = None) -> dict:   # async handlers supported
    ...

app.run()                                 # sync entry (asyncio.run inside)
# or, from within an existing event loop:
await app.run_stdio_async()
```

Explicit overrides (used by `ccmemory` to keep its hand-written schemas, incl.
an `enum`, byte-for-byte):

```python
@app.tool(name="memory_write", description="...", schema={...})
def memory_write(...): ...
```

When `schema=` is given, signature introspection is skipped.

## Schema introspection — the 3.9 pitfall

Tool `inputSchema` is built from `inspect.signature()` + raw `__annotations__`.

**We never call `typing.get_type_hints()`.** Under `from __future__ import
annotations`, annotations are strings like `"int | None"` / `"list[str]"`.
Evaluating `"int | None"` at runtime on Python 3.9 raises
`TypeError: unsupported operand type(s) for |`. So `build_input_schema()` parses
the annotation *text* instead (see `transport.py`):

| annotation        | JSON Schema                                  |
|-------------------|----------------------------------------------|
| `str`             | `{"type": "string"}`                         |
| `int`             | `{"type": "integer"}`                        |
| `float`           | `{"type": "number"}`                         |
| `bool`            | `{"type": "boolean"}`                        |
| `dict` / `dict[..]` | `{"type": "object"}`                       |
| `list`            | `{"type": "array"}`                          |
| `list[str]`       | `{"type": "array", "items": {"type":"string"}}` |
| `X \| None`, `Optional[X]` | underlying type of `X`               |
| unknown / `Any`   | `{}` (unconstrained)                          |

A parameter is **required** iff it has no default. Real type objects (for
modules without the future import, e.g. `ccusage`) are handled too.

## Wire protocol

MCP stdio framing: newline-delimited JSON-RPC 2.0 over stdin/stdout. Diagnostics
go to **stderr** only; stdout carries protocol frames exclusively.

| Method                      | Behavior |
|-----------------------------|----------|
| `initialize`                | echo client `protocolVersion` (fallback `2024-11-05`); reply `{capabilities:{tools:{}}, serverInfo:{name,version}, instructions?}` |
| `notifications/initialized` | notification — no reply |
| `tools/list`                | `{tools: [{name, description, inputSchema}]}` |
| `tools/call`                | dispatch (await if coroutine); reply `{content:[...], isError}` |
| `ping`                      | `{}` |
| unknown (request)           | JSON-RPC error `-32601` |

Return-value normalization for `tools/call`:

- `str` → one text content block
- a list already shaped like content blocks (`[{"type": ...}, ...]`) → passed through
- any other `dict`/`list` → JSON-encoded into one text block
- tool exceptions → `{content:[text], isError:true}` (in-band, not a JSON-RPC error)

## Consumers

- `ccmemory` — low-level style, 5 tools with explicit `schema=`/`description=`.
- `ccusage` — 2 sync tools, `app.run()`.
- `ccteam` — 9 async tools, `instructions=`, `await app.run_stdio_async()`.

## Distribution

`ccenvmcp` is installed from local source by the top-level `install.sh` **before**
the packages that import it. It is intentionally **not** listed in any package's
`dependencies` (the repo installs everything from local source, never PyPI, so a
declared dependency would trigger a failing PyPI lookup). Install ordering
guarantees it is present on the shared `--user` site at import time.
