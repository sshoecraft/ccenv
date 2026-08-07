"""MCP tool surface: memory_list bounding, payload shape, and the in-band note."""

import json

import pytest

from ccmemory import mcp_server
from .conftest import write_memory


def call(tool: str, **kwargs) -> str:
    app = mcp_server.build_app()
    return app._tools[tool].func(**kwargs)[0]["text"]


def call_json(tool: str, **kwargs):
    return json.loads(call(tool, **kwargs))


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("CCMEMORY_LIST_TOKEN_BUDGET", raising=False)
    monkeypatch.delenv("CCMEMORY_COMPILE_THRESHOLD", raising=False)


def test_budget_default_and_env(monkeypatch):
    assert mcp_server.list_token_budget() == mcp_server.DEFAULT_LIST_TOKEN_BUDGET
    monkeypatch.setenv("CCMEMORY_LIST_TOKEN_BUDGET", "1234")
    assert mcp_server.list_token_budget() == 1234
    monkeypatch.setenv("CCMEMORY_LIST_TOKEN_BUDGET", "garbage")
    assert mcp_server.list_token_budget() == mcp_server.DEFAULT_LIST_TOKEN_BUDGET
    monkeypatch.setenv("CCMEMORY_LIST_TOKEN_BUDGET", "0")
    assert mcp_server.list_token_budget() == 0


def test_whole_serialized_payload_fits_the_budget(memory_dir):
    # The budget is a promise about what lands in the context window, so it has
    # to cover the bytes actually shipped — entries AND the note/counts
    # envelope. Budgeting entries alone overshot by ~210 tokens on every call.
    monkey_budget = 2000
    for i in range(400):
        write_memory(memory_dir, f"proj{i:03d}", type="project",
                     description="d" * 140)
    import os
    os.environ["CCMEMORY_LIST_TOKEN_BUDGET"] = str(monkey_budget)
    try:
        raw = call("memory_list")
    finally:
        del os.environ["CCMEMORY_LIST_TOKEN_BUDGET"]
    shipped = -(-len(raw) // 4)
    assert shipped <= monkey_budget, \
        f"memory_list shipped {shipped} tokens against a {monkey_budget} budget"
    # And it is not trivially satisfied by shipping almost nothing.
    assert json.loads(raw)["shown"] > 10


def test_stats_reports_the_cost_of_the_real_payload(memory_dir):
    for i in range(200):
        write_memory(memory_dir, f"proj{i:03d}", type="project",
                     description="d" * 140)
    raw = call("memory_list")
    st = json.loads(call("memory_stats"))
    shipped = -(-len(raw) // 4)
    # Within 10% of the bytes that actually went out. memory_stats is what a
    # session consults to decide whether listing is affordable; a field that
    # under-reports by 1.4x is worse than no field.
    assert 0.9 <= st["list_tokens_actual"] / shipped <= 1.1, \
        f"stats says {st['list_tokens_actual']}, payload was {shipped}"


def test_list_returns_counts_and_memories(memory_dir):
    write_memory(memory_dir, "a")
    write_memory(memory_dir, "b")
    payload = call_json("memory_list")
    assert payload["total"] == 2 and payload["shown"] == 2
    assert payload["folded"] == 0 and payload["withheld"] == 0
    assert {m["name"] for m in payload["memories"]} == {"a", "b"}
    assert "path" not in payload["memories"][0]


def test_small_store_gets_no_withholding_note(memory_dir):
    write_memory(memory_dir, "a")
    payload = call_json("memory_list")
    assert "withheld" not in payload["note"]
    assert "folded into" not in payload["note"]


def test_folded_memories_are_omitted_and_explained(memory_dir):
    write_memory(memory_dir, "raw-a")
    write_memory(memory_dir, "raw-b")
    write_memory(memory_dir, "compiled-topic", body="[[raw-a]] [[raw-b]]")
    payload = call_json("memory_list")
    assert payload["folded"] == 2
    assert {m["name"] for m in payload["memories"]} == {"compiled-topic"}
    assert "already folded" in payload["note"]
    assert "memory_search" in payload["note"]
    assert "include_folded=true" in payload["note"]


def test_include_folded_brings_them_back(memory_dir):
    write_memory(memory_dir, "raw-a")
    write_memory(memory_dir, "compiled-topic", body="[[raw-a]]")
    payload = call_json("memory_list", include_folded=True)
    assert payload["folded"] == 0
    assert {m["name"] for m in payload["memories"]} == {"raw-a", "compiled-topic"}


def test_budget_truncation_is_never_silent(memory_dir, monkeypatch):
    monkeypatch.setenv("CCMEMORY_LIST_TOKEN_BUDGET", "200")
    for i in range(60):
        write_memory(memory_dir, f"proj{i:02d}", description="d" * 140)
    payload = call_json("memory_list")
    assert payload["withheld"] > 0
    assert payload["shown"] + payload["folded"] + payload["withheld"] == payload["total"]
    assert str(payload["withheld"]) in payload["note"]
    assert "token budget" in payload["note"]


def test_note_carries_compaction_directive_in_band(memory_dir, monkeypatch):
    # The SessionStart reminder gets ignored; the payload the model already
    # reads is where the ask has to live.
    monkeypatch.setenv("CCMEMORY_COMPILE_THRESHOLD", "3")
    for i in range(5):
        write_memory(memory_dir, f"note{i}")
    payload = call_json("memory_list")
    assert "COMPACTION DUE" in payload["note"]
    assert "compile-memories" in payload["note"]


def test_no_compaction_directive_once_cited(memory_dir, monkeypatch):
    monkeypatch.setenv("CCMEMORY_COMPILE_THRESHOLD", "3")
    for i in range(5):
        write_memory(memory_dir, f"note{i}")
    write_memory(memory_dir, "compiled-topic",
                 body=" ".join(f"[[note{i}]]" for i in range(5)))
    payload = call_json("memory_list")
    assert "COMPACTION DUE" not in payload["note"]


def test_type_filter_still_works(memory_dir):
    write_memory(memory_dir, "fb", type="feedback")
    write_memory(memory_dir, "pj", type="project")
    payload = call_json("memory_list", type="feedback")
    assert {m["name"] for m in payload["memories"]} == {"fb"}


def test_stats_reports_listing_pressure(memory_dir):
    write_memory(memory_dir, "raw-a")
    write_memory(memory_dir, "compiled-topic", body="[[raw-a]]")
    st = call_json("memory_stats")
    assert st["folded"] == 1
    assert st["list_budget"] == mcp_server.DEFAULT_LIST_TOKEN_BUDGET
    assert st["list_tokens_unbounded"] >= st["list_tokens_actual"] > 0
    assert st["list_counts"]["total"] == 2


def test_empty_store_lists_cleanly(memory_dir):
    payload = call_json("memory_list")
    assert payload["total"] == 0 and payload["memories"] == []
