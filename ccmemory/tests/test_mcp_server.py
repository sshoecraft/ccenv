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
