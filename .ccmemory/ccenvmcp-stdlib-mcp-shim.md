---
name: ccenvmcp-stdlib-mcp-shim
description: ccenvmcp: stdlib-only Python 3.9+ FastMCP-compatible shim replacing the mcp SDK across ccmemory/ccusage/ccteam so the bundle installs on 3.9.
metadata:
  type: project
---

As of 2026-06-13, the repo has a new foundation package `ccenvmcp/` — a ~250-line, stdlib-only, Python 3.9+ MCP server shim that reproduces the FastMCP slice the tools-only servers use. It exists because **every** release of the official `mcp` SDK requires Python >=3.10, which locked the bundle out of stock 3.9 systems (Debian 11 / Raspberry Pi OS, RHEL/Alma/Rocky 9, Ubuntu 20.04). The `mcp` SDK was the ONLY hard 3.9 blocker — `nats-py` needs only >=3.7, `watchfiles>=0.21` resolves to a 3.9-compatible abi3 build, and all packages already use `from __future__ import annotations`.

API: `from ccenvmcp import FastMCP`. Supports `FastMCP(name, instructions=...)`, `@app.tool()` on sync OR async funcs, `@app.tool(name=, description=, schema=)` explicit overrides, `app.run()` (sync) and `await app.run_stdio_async()` (async). Wire protocol = newline-delimited JSON-RPC 2.0 over stdio: initialize / notifications/initialized / tools/list / tools/call / ping.

KEY 3.9 PITFALL (encoded in `ccenvmcp/ccenvmcp/transport.py`): do NOT call `typing.get_type_hints()` to build schemas. Under `from __future__ import annotations` the hints are strings like `"int | None"`; evaluating that on 3.9 raises TypeError. The shim parses annotation TEXT instead (`_token_to_schema`), handling `X | None`/`Optional[X]`/`list[str]`. Tests in `ccenvmcp/tests/test_transport.py`.

Consumers: ccmemory (low-level style, 5 tools, explicit schema= to preserve the memory_write `type` enum; `serve()` split into `build_app()`+`serve()`), ccusage (2 sync tools), ccteam (9 async tools, instructions=). All three had `mcp` removed from deps and requires-python lowered to >=3.9. Versions bumped: ccmemory 0.7.0, ccusage 0.2.0, ccteam 0.3.0.

Distribution: ccenvmcp is installed FIRST by top-level install.sh and is intentionally NOT a declared dependency (repo installs from local source, never PyPI — a declared dep would trigger a failing PyPI lookup). Install ordering guarantees it is importable from the shared --user site. See `ccenvmcp/docs/mcp.md`. Related: [[ccmemory-debian-build-unknown-install-layout]], [[no-per-component-venvs]].
