"""Item states, the ready rule, claims, checks and events (`ccdirector.work`)."""
from __future__ import annotations

import copy

from ccdirector import plan, work


def item(item_id, priority=1, **extra):
    base = {"id": item_id, "what": f"do {item_id}", "serves": ["c"], "priority": priority,
            "paths": [], "check": "true", "size": "S"}
    base.update(extra)
    return base


def make_plan(*items):
    return {"goal": "g", "source": "directive", "gate": "", "criteria": ["c"],
            "items": list(items), "risks": []}


def accept(root, current):
    assert plan.problems(current) == []
    plan.write(root, current)
    return work.sync(root, current)


def test_sync_adds_revises_and_drops(tmp_path):
    first = make_plan(item("a"), item("b"))
    assert accept(tmp_path, first) == ["new item a", "new item b"]
    assert work.park(tmp_path, "w1", "a", "x")[0] is False      # not held: refused
    work.next_item(tmp_path, "w1")                               # w1 takes a
    assert work.park(tmp_path, "w1", "a", "needs a decision")[0]
    second = make_plan(item("a", what="do a, decided"), item("c"))
    changes = accept(tmp_path, second)
    assert "item a revised: parked -> pending" in changes
    assert "item b dropped (pending)" in changes and "new item c" in changes
    state = work.load(tmp_path)
    assert set(state["items"]) == {"a", "c"} and state["items"]["a"]["status"] == "pending"
    kinds = [e["kind"] for e in work.events(tmp_path)]
    assert kinds.count("plan_accepted") == 2 and "item_parked" in kinds


def test_ready_respects_dependencies_priority_and_section(tmp_path):
    current = make_plan(item("low", 3, section="dlm"), item("high", 1),
                        item("after", 1, depends_on=["high"]), item("same", 3, section="net"))
    accept(tmp_path, current)
    state = work.load(tmp_path)
    assert work.ready(state, current) == ["high", "low", "same"]
    state["workers"]["w1"] = {"section": "net"}
    assert work.ready(state, current, "w1") == ["high", "same", "low"]


def test_resources_and_interface_are_exclusive(tmp_path):
    current = make_plan(item("rig-1", 1, resources=["rig"]), item("rig-2", 1, resources=["rig"]),
                        item("iface", 2, interface=True), item("free", 3))
    accept(tmp_path, current)
    assert work.next_item(tmp_path, "w1") == ("item", current["items"][0])
    kind, given = work.next_item(tmp_path, "w2")
    assert kind == "item" and given["id"] == "free", "rig-2 shares the rig; iface needs quiet"
    kind, why = work.next_item(tmp_path, "w3")
    assert kind == "wait"
    for worker, item_id in (("w1", "rig-1"), ("w2", "free")):
        assert work.finish(tmp_path, worker, item_id)[0] == "done"
    kind, given = work.next_item(tmp_path, "w3")
    assert given["id"] == "rig-2"
    assert work.next_item(tmp_path, "w4")[0] == "wait", "iface waits for rig-2 to finish"
    work.finish(tmp_path, "w3", "rig-2")
    assert work.next_item(tmp_path, "w4")[1]["id"] == "iface"
    assert work.next_item(tmp_path, "w5")[0] == "none", "nothing runs beside an interface item"


def test_a_worker_gets_its_own_item_back_and_none_at_the_end(tmp_path):
    current = make_plan(item("a"))
    accept(tmp_path, current)
    assert work.next_item(tmp_path, "w1")[1]["id"] == "a"
    assert work.next_item(tmp_path, "w1")[1]["id"] == "a"
    assert work.next_item(tmp_path, "w2")[0] == "none"
    assert work.finish(tmp_path, "w1", "a")[0] == "done"
    kind, why = work.next_item(tmp_path, "w1")
    assert kind == "none" and "every item" in why


def test_check_decides_done(tmp_path):
    marker = tmp_path / "fixed"
    current = make_plan(item("a", check=f"test -f {marker}"))
    accept(tmp_path, current)
    work.next_item(tmp_path, "w1")
    assert work.finish(tmp_path, "w2", "a")[0] == "refused", "only the holder may report"
    outcome, output = work.finish(tmp_path, "w1", "a")
    assert outcome == "failed" and "exit 1" in output
    state = work.load(tmp_path)
    assert state["items"]["a"]["status"] == "claimed" and state["items"]["a"]["attempts"] == 1
    marker.write_text("")
    assert work.finish(tmp_path, "w1", "a")[0] == "done"
    kinds = [e["kind"] for e in work.events(tmp_path)]
    assert kinds[-2:] == ["item_check_failed", "item_done"]


def test_three_failed_checks_in_a_row_park_the_item(tmp_path):
    accept(tmp_path, make_plan(item("a", check="false")))
    work.next_item(tmp_path, "w1")
    assert [work.finish(tmp_path, "w1", "a")[0] for n in range(3)] == ["failed", "failed", "parked"]
    entry = work.load(tmp_path)["items"]["a"]
    assert entry["status"] == "parked" and entry["worker"] is None and entry["parks"] == 1
    assert "3 failed checks in a row" in entry["note"]
    assert [e["kind"] for e in work.events(tmp_path)][-1] == "item_parked"


def test_a_passing_or_interrupted_run_of_failures_does_not_park(tmp_path):
    marker = tmp_path / "fixed"
    accept(tmp_path, make_plan(item("a", check=f"test -f {marker}")))
    work.next_item(tmp_path, "w1")
    assert work.finish(tmp_path, "w1", "a")[0] == "failed"
    assert work.finish(tmp_path, "w1", "a")[0] == "failed"
    marker.write_text("")
    assert work.finish(tmp_path, "w1", "a")[0] == "done"


def park_and_revise(root, current, times):
    """Park the item and have the director revise it, `times` over."""
    for n in range(times):
        assert work.next_item(root, "w1")[0] == "item"
        assert work.park(root, "w1", "a", "blocked")[0]
        current["items"][0]["what"] += " revised"
        accept(root, current)


def test_the_third_park_makes_the_next_worker_consult(tmp_path):
    current = make_plan(item("a"))
    accept(tmp_path, current)
    park_and_revise(tmp_path, current, 2)
    assert not work.consult_required(tmp_path, "a")
    park_and_revise(tmp_path, current, 1)
    assert work.consult_required(tmp_path, "a")
    assert work.next_item(tmp_path, "w2")[0] == "item"
    outcome, why = work.finish(tmp_path, "w2", "a")
    assert outcome == "refused" and "Ask an advisor" in why
    work.item_consulted(tmp_path, "w2")
    assert work.finish(tmp_path, "w2", "a")[0] == "done"


def test_a_consult_along_the_way_resets_the_items_count(tmp_path):
    current = make_plan(item("a"))
    accept(tmp_path, current)
    park_and_revise(tmp_path, current, 2)
    work.next_item(tmp_path, "w1")
    work.item_consulted(tmp_path, "w1")
    work.park(tmp_path, "w1", "a", "still blocked")
    entry = work.load(tmp_path)["items"]["a"]
    assert entry["parks"] == 1 and not entry.get("consult_required")


def test_a_consult_only_counts_for_the_item_its_worker_holds(tmp_path):
    current = make_plan(item("a"), item("b"))
    accept(tmp_path, current)
    work.next_item(tmp_path, "w1")
    work.park(tmp_path, "w1", "a", "blocked")
    work.next_item(tmp_path, "w2")
    held = work.holding(work.load(tmp_path), "w2")
    work.item_consulted(tmp_path, "w2")
    assert work.load(tmp_path)["items"]["a"]["parks"] == 1 and held != "a"


def test_check_timeout(tmp_path):
    current = make_plan(item("slow", check="sleep 5", check_timeout=1))
    accept(tmp_path, current)
    work.next_item(tmp_path, "w1")
    outcome, output = work.finish(tmp_path, "w1", "slow")
    assert outcome == "failed" and "timed out after 1s" in output


def test_check_runs_in_the_project_root(tmp_path):
    (tmp_path / "here").write_text("")
    current = make_plan(item("a", check="test -f here"))
    accept(tmp_path, current)
    work.next_item(tmp_path, "w1")
    assert work.finish(tmp_path, "w1", "a")[0] == "done"


def test_finishing_a_dropped_item_is_refused(tmp_path):
    accept(tmp_path, make_plan(item("a"), item("b")))
    work.next_item(tmp_path, "w1")
    accept(tmp_path, make_plan(item("b")))
    outcome, why = work.finish(tmp_path, "w1", "a")
    assert outcome == "refused" and "not in the current plan" in why


def test_release_and_reset_return_items(tmp_path):
    accept(tmp_path, make_plan(item("a"), item("b")))
    work.next_item(tmp_path, "w1")
    work.next_item(tmp_path, "w2")
    assert work.release_worker(tmp_path, "w1") == "a"
    assert work.reset_claims(tmp_path) == ["b"]
    state = work.load(tmp_path)
    assert {e["status"] for e in state["items"].values()} == {"pending"}


def test_stalled_and_all_done(tmp_path):
    current = make_plan(item("a"), item("b", depends_on=["a"]))
    accept(tmp_path, current)
    state = work.load(tmp_path)
    assert not work.stalled(state, current) and not work.all_done(state, current)
    work.next_item(tmp_path, "w1")
    work.park(tmp_path, "w1", "a", "blocked")
    state = work.load(tmp_path)
    assert work.stalled(state, current), "a parked, b waits on a, nothing in progress"
    revised = copy.deepcopy(current)
    revised["items"][0]["what"] = "do a another way"
    accept(tmp_path, revised)
    for _ in range(2):
        given = work.next_item(tmp_path, "w1")[1]
        work.finish(tmp_path, "w1", given["id"])
    assert work.all_done(work.load(tmp_path), revised)


def test_wait_for_events_advances_the_cursor(tmp_path):
    accept(tmp_path, make_plan(item("a")))
    first = work.wait_for_events(tmp_path, "test.cursor", timeout=0)
    assert [e["kind"] for e in first] == ["plan_accepted"]
    assert work.wait_for_events(tmp_path, "test.cursor", timeout=0) == []
    work.next_item(tmp_path, "w1")
    assert [e["kind"] for e in work.wait_for_events(tmp_path, "test.cursor", 0)] == ["item_claimed"]
