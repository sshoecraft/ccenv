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


# --- concurrent dispatch, progress, cancellation ---------------------------

def capture(app):
    """Replace the app's stdout writer with a list."""
    out = []
    app._write = out.append
    return out


def call(mid, name, args=None, token=None):
    params = {"name": name, "arguments": args or {}}
    if token is not None:
        params["_meta"] = {"progressToken": token}
    return {"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": params}


def test_ctx_is_not_in_the_schema_and_reports_progress_only_with_a_token():
    app = FastMCP("t")

    @app.tool()
    async def wait(seconds: int, ctx) -> str:
        await ctx.report_progress(1, total=2, message="half")
        return "done"

    tool = _run(app._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
    schema = tool["result"]["tools"][0]["inputSchema"]
    assert "ctx" not in schema["properties"] and schema["required"] == ["seconds"]
    out = capture(app)
    _run(app._handle(call(2, "wait", {"seconds": 1}, token="tok")))
    assert out == [{"jsonrpc": "2.0", "method": "notifications/progress",
                    "params": {"progressToken": "tok", "progress": 1, "total": 2,
                               "message": "half"}}]
    out.clear()
    _run(app._handle(call(3, "wait", {"seconds": 1})))
    assert out == [], "no token, no notification"


def test_a_tool_without_ctx_sends_no_progress():
    app = FastMCP("t")

    @app.tool()
    def plain() -> str:
        return "x"

    out = capture(app)
    _run(app.answer(call(1, "plain", token="tok")))
    assert [m.get("method") for m in out] == [None], "only the reply"


def test_a_long_call_does_not_block_other_calls():
    app = FastMCP("t")
    gate = asyncio.Event()

    @app.tool()
    async def slow() -> str:
        await gate.wait()
        return "slow"

    @app.tool()
    def quick() -> str:
        return "quick"

    async def scenario():
        out = capture(app)
        app.start_call(call(1, "slow"))
        app.start_call(call(2, "quick"))
        await asyncio.sleep(0.05)
        assert [m["id"] for m in out] == [2], "quick answered while slow waits"
        gate.set()
        await asyncio.sleep(0.05)
        assert [m["id"] for m in out] == [2, 1]
        assert not app.inflight

    _run(scenario())


def test_sync_tools_run_one_at_a_time_in_order():
    app = FastMCP("t")
    trace = []

    @app.tool()
    def step(n: int) -> int:
        trace.append(("in", n))
        trace.append(("out", n))
        return n

    async def scenario():
        out = capture(app)
        for n in range(5):
            app.start_call(call(n, "step", {"n": n}))
        await asyncio.sleep(0.05)
        assert [m["id"] for m in out] == list(range(5))

    _run(scenario())
    assert trace == [(kind, n) for n in range(5) for kind in ("in", "out")]


def test_cancel_stops_only_cancellable_tools():
    app = FastMCP("t")
    cleaned = []

    @app.tool(cancellable=True)
    async def queue() -> str:
        try:
            await asyncio.sleep(30)
        finally:
            cleaned.append("queue")
        return "never"

    @app.tool()
    async def stubborn() -> str:
        await asyncio.sleep(0.1)
        return "finished"

    async def scenario():
        out = capture(app)
        app.start_call(call(1, "queue"))
        app.start_call(call(2, "stubborn"))
        await asyncio.sleep(0.02)
        app.cancel_call({"requestId": 1})
        app.cancel_call({"requestId": 2})
        await asyncio.sleep(0.3)
        assert cleaned == ["queue"]
        assert [m["id"] for m in out] == [2], "a cancelled call gets no reply"

    _run(scenario())


def test_closing_stdin_cancels_cancellable_calls():
    app = FastMCP("t")
    cleaned = []

    @app.tool(cancellable=True)
    async def queue() -> str:
        try:
            await asyncio.sleep(30)
        finally:
            cleaned.append("queue")

    async def scenario():
        capture(app)
        app.start_call(call(1, "queue"))
        await asyncio.sleep(0.02)
        await asyncio.wait_for(app.finish(), 5)

    _run(scenario())
    assert cleaned == ["queue"]


SERVER = r'''
import asyncio
from ccenvmcp import FastMCP
app = FastMCP("pipe")

@app.tool()
async def nap(seconds: float, ctx) -> str:
    await ctx.report_progress(0)
    await asyncio.sleep(seconds)
    return "rested"

app.run()
'''


def test_over_a_real_pipe_ping_is_answered_during_a_long_call():
    import json, os, subprocess, sys, time
    from pathlib import Path
    src = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ, PYTHONPATH=src)
    proc = subprocess.Popen([sys.executable, "-c", SERVER], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, env=env)
    try:
        def send(obj):
            proc.stdin.write(json.dumps(obj) + "\n")
            proc.stdin.flush()

        send(call(1, "nap", {"seconds": 1.5}, token="t1"))
        send({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        started = time.time()
        seen = [json.loads(proc.stdout.readline()) for _ in range(2)]
        assert time.time() - started < 1.0, "progress and ping came before the nap ended"
        assert {"jsonrpc": "2.0", "id": 2, "result": {}} in seen
        assert any(m.get("method") == "notifications/progress" for m in seen)
        last = json.loads(proc.stdout.readline())
        assert last["id"] == 1 and "rested" in last["result"]["content"][0]["text"]
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)
