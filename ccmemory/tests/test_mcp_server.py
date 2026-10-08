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


def test_note_carries_the_background_dispatch(memory_dir, monkeypatch):
    # The dispatch rides back in-band because the model already reads this
    # payload and the SessionStart nudge does not reach every session. It must
    # ask for background agents and no waiting, and must say "once" so a
    # session that also saw the SessionStart nudge does not dispatch twice.
    monkeypatch.setenv("CCMEMORY_COMPILE_THRESHOLD", "3")
    for i in range(5):
        write_memory(memory_dir, f"note{i}")
    payload = call_json("memory_list")
    note = payload["note"]
    assert "Compaction backlog: 5" in note
    assert 'Agent(subagent_type="memory-compactor"' in note
    assert "memory_compaction_plan()" in note
    assert "Do not wait on the agents" in note
    assert "already dispatched them this session" in note
    assert "NOT a task" not in note


def overflow_feedback(memory_dir, monkeypatch, *, prefix="fb"):
    # A budget far too small for 30 feedback entries, and a threshold far above
    # the backlog, so any dispatch request comes from the overflow alone.
    monkeypatch.setenv("CCMEMORY_LIST_TOKEN_BUDGET", "500")
    monkeypatch.setenv("CCMEMORY_COMPILE_THRESHOLD", "1000")
    for i in range(30):
        write_memory(memory_dir, f"{prefix}{i:02d}", type="feedback",
                     description="a behavioral correction " + "d" * 120)


def test_feedback_overflow_with_backlog_is_handled_without_the_user(memory_dir, monkeypatch):
    # The overflow is the session's to fix: compacting the behavior notes is
    # what makes room, so the note asks for that dispatch even under the
    # threshold and says not to report it. Telling the user about an env var
    # while uncompiled corrections sit in the store pointed at the wrong fix.
    overflow_feedback(memory_dir, monkeypatch)
    raw = call("memory_list")
    # The longest note the server writes still fits the envelope reserve.
    assert -(-len(raw) // 4) <= 500
    payload = json.loads(raw)
    note = payload["note"]
    assert payload["load_bearing_withheld"] > 0
    assert "do not report it to the user" in note
    assert 'memory_list(type="feedback")' in note
    assert "Compaction backlog: 30" in note
    assert 'Agent(subagent_type="memory-compactor"' in note
    assert "WARNING" not in note
    assert "Tell the user" not in note

    filtered = call_json("memory_list", type="feedback")
    assert filtered["load_bearing_withheld"] > 0
    assert "even in this filtered listing" in filtered["note"]
    assert "do not report it to the user" in filtered["note"]
    # The filtered call does not tell the caller to make the call it just made.
    assert "Recover them now" not in filtered["note"]
    # Withheld feedback is not described as oldest raw notes reachable by search.
    assert "oldest raw notes" not in filtered["note"]


def test_feedback_overflow_with_nothing_to_compact_tells_the_user(memory_dir, monkeypatch):
    # Every behavior entry is already a compiled article, so compaction cannot
    # make room. Raising the budget is the only fix, and only the user can.
    overflow_feedback(memory_dir, monkeypatch, prefix="compiled-fb")
    payload = call_json("memory_list")
    note = payload["note"]
    assert payload["load_bearing_withheld"] > 0
    assert "WARNING" in note
    assert "Tell the user" in note
    assert "CCMEMORY_LIST_TOKEN_BUDGET (currently 500)" in note
    assert "do not report it to the user" not in note
    assert "memory-compactor" not in note


def test_feedback_overflow_respects_the_cooldown(memory_dir, monkeypatch):
    # A compiled article written moments ago means compactors may still be in
    # flight on these notes; the overflow must not re-dispatch them.
    overflow_feedback(memory_dir, monkeypatch)
    write_memory(memory_dir, "compiled-other", body="unrelated")
    note = call_json("memory_list")["note"]
    assert "do not report it to the user" in note
    assert "memory-compactor" not in note


def test_no_backlog_report_once_cited(memory_dir, monkeypatch):
    monkeypatch.setenv("CCMEMORY_COMPILE_THRESHOLD", "3")
    for i in range(5):
        write_memory(memory_dir, f"note{i}")
    write_memory(memory_dir, "compiled-topic",
                 body=" ".join(f"[[note{i}]]" for i in range(5)))
    payload = call_json("memory_list")
    assert "Compaction backlog" not in payload["note"]


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


def test_compaction_plan_tool_returns_disjoint_groups(memory_dir):
    for i in range(15):
        write_memory(memory_dir, f"note{i}", body=f"unrelated body {i}")
    plan = call_json("memory_compaction_plan")
    assert plan["backlog"] == 15
    names = [n for g in plan["groups"] for n in g["names"]]
    assert sorted(names) == sorted(f"note{i}" for i in range(15))
    assert len(names) == len(set(names))
    assert plan["wave"] >= 1


def test_compaction_plan_tool_fetches_one_group_by_seed(memory_dir):
    for i in range(15):
        write_memory(memory_dir, f"note{i}", body=f"unrelated body {i}")
    seed = call_json("memory_compaction_plan")["groups"][0]["seed"]
    one = call_json("memory_compaction_plan", seed=seed)
    assert len(one["groups"]) == 1
    assert one["groups"][0]["seed"] == seed


def test_compaction_plan_tool_says_done_for_a_compiled_seed(memory_dir):
    write_memory(memory_dir, "note0")
    plan = call_json("memory_compaction_plan", seed="already-gone")
    assert plan["status"] == "done"
    assert plan["groups"] == []
