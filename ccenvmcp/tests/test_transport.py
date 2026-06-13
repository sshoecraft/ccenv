"""Tests for the ccenvmcp stdio JSON-RPC shim.

These exercise schema introspection (the 3.9-sensitive part) and the
request/response dispatch without needing a real stdio pipe.
"""

from __future__ import annotations

import asyncio

from ccenvmcp import FastMCP, build_input_schema
from ccenvmcp.transport import _token_to_schema, _normalize_content


def test_token_mapping():
    assert _token_to_schema("str") == {"type": "string"}
    assert _token_to_schema("int") == {"type": "integer"}
    assert _token_to_schema("float") == {"type": "number"}
    assert _token_to_schema("bool") == {"type": "boolean"}
    assert _token_to_schema("dict") == {"type": "object"}
    assert _token_to_schema("list") == {"type": "array"}


def test_token_unions_and_generics():
    # PEP 604 union with None -> underlying type, 3.9-safe (string parse)
    assert _token_to_schema("int | None") == {"type": "integer"}
    assert _token_to_schema("str | None") == {"type": "string"}
    assert _token_to_schema("Optional[int]") == {"type": "integer"}
    assert _token_to_schema("list[str]") == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert _token_to_schema("dict[str, int]") == {"type": "object"}
    # genuine multi-type union -> unconstrained
    assert _token_to_schema("int | str") == {}
    # unknown -> unconstrained
    assert _token_to_schema("SomeClass") == {}


def test_build_input_schema_required_vs_optional():
    # This module uses `from __future__ import annotations`, so these hints are
    # stored as strings ("list[str]", "int | None") — exactly the 3.9 scenario.
    def fn(paths: list[str], mode: str = "exclusive", timeout_ms: int | None = None):
        ...

    schema = build_input_schema(fn)
    assert schema["type"] == "object"
    assert schema["properties"]["paths"] == {"type": "array", "items": {"type": "string"}}
    assert schema["properties"]["mode"] == {"type": "string"}
    assert schema["properties"]["timeout_ms"] == {"type": "integer"}
    # only the param without a default is required
    assert schema["required"] == ["paths"]


def test_build_input_schema_no_params():
    def fn():
        ...

    schema = build_input_schema(fn)
    assert schema == {"type": "object", "properties": {}}


def test_normalize_content_passthrough():
    assert _normalize_content("hi") == [{"type": "text", "text": "hi"}]
    blocks = [{"type": "text", "text": "x"}]
    assert _normalize_content(blocks) is blocks
    out = _normalize_content({"a": 1})
    assert out[0]["type"] == "text" and '"a": 1' in out[0]["text"]


def _run(coro):
    return asyncio.run(coro)


def test_dispatch_initialize_and_list():
    app = FastMCP("t", instructions="hello instructions")

    @app.tool()
    def echo(msg: str) -> str:
        "Echo the message back."
        return msg

    init = _run(app._handle({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-01-01", "capabilities": {}},
    }))
    assert init["result"]["protocolVersion"] == "2025-01-01"
    assert init["result"]["serverInfo"]["name"] == "t"
    assert init["result"]["instructions"] == "hello instructions"

    # notification -> no reply
    assert _run(app._handle({"jsonrpc": "2.0", "method": "notifications/initialized"})) is None

    listed = _run(app._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
    tools = listed["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "echo"
    assert tools[0]["description"] == "Echo the message back."
    assert tools[0]["inputSchema"]["required"] == ["msg"]


def test_dispatch_tool_call_sync_and_async():
    app = FastMCP("t")

    @app.tool()
    def add(a: int, b: int) -> dict:
        return {"sum": a + b}

    @app.tool()
    async def aadd(a: int, b: int) -> dict:
        await asyncio.sleep(0)
        return {"sum": a + b}

    r1 = _run(app._handle({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "add", "arguments": {"a": 2, "b": 3}},
    }))
    assert r1["result"]["isError"] is False
    assert '"sum": 5' in r1["result"]["content"][0]["text"]

    r2 = _run(app._handle({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "aadd", "arguments": {"a": 10, "b": 1}},
    }))
    assert '"sum": 11' in r2["result"]["content"][0]["text"]


def test_dispatch_tool_error_is_in_band():
    app = FastMCP("t")

    @app.tool()
    def boom() -> str:
        raise ValueError("kaboom")

    r = _run(app._handle({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "boom", "arguments": {}},
    }))
    assert r["result"]["isError"] is True
    assert "ValueError: kaboom" in r["result"]["content"][0]["text"]


def test_explicit_schema_override_preserves_enum():
    app = FastMCP("t")
    explicit = {
        "type": "object",
        "properties": {"type": {"type": "string", "enum": ["a", "b"]}},
        "required": ["type"],
    }

    @app.tool(name="w", description="hand written", schema=explicit)
    def w(**kwargs):
        return "ok"

    listed = _run(app._handle({"jsonrpc": "2.0", "id": 6, "method": "tools/list"}))
    t = listed["result"]["tools"][0]
    assert t["name"] == "w"
    assert t["description"] == "hand written"
    assert t["inputSchema"]["properties"]["type"]["enum"] == ["a", "b"]


def test_unknown_method_errors():
    app = FastMCP("t")
    r = _run(app._handle({"jsonrpc": "2.0", "id": 7, "method": "bogus/method"}))
    assert r["error"]["code"] == -32601
