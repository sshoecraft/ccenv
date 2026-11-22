"""Item states, the ready rule, claims, checks and events (`ccdirector.work`)."""
from __future__ import annotations

import copy
import time

from ccdirector import plan, work


def item(item_id, priority=1, **extra):
    base = {"id": item_id, "what": f"do {item_id}", "serves": ["c"], "priority": priority,
            "paths": [], "check": "true", "size": "S"}
    base.update(extra)
    return base


REGISTRY = {"rig": {"capacity": 1, "what": "the test rig"},
            "pool": {"capacity": 4, "what": "four VM nodes"}}


def make_plan(*items):
    return {"goal": "g", "source": "directive", "gate": "", "criteria": ["c"],
            "resources": REGISTRY, "items": list(items), "risks": []}


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


def test_independent_items_all_go_out_at_once(tmp_path):
    """Nothing but dependencies keeps an item back: no item holds a resource for its
    whole run and there are no interface items, so N independent goals go to N
    workers at once."""
    current = make_plan(item("a", 1), item("b", 1), item("c", 2), item("d", 3, depends_on=["a"]))
    accept(tmp_path, current)
    given = [work.next_item(tmp_path, w)[1]["id"] for w in ("w1", "w2", "w3")]
    assert given == ["a", "b", "c"]
    assert work.next_item(tmp_path, "w4")[0] == "wait", "d waits for a"
    assert work.finish(tmp_path, "w1", "a")[0] == "done"
    assert work.next_item(tmp_path, "w4")[1]["id"] == "d"


def test_a_worker_gets_its_own_item_back_and_none_at_the_end(tmp_path):
    current = make_plan(item("a"))
    accept(tmp_path, current)
    assert work.next_item(tmp_path, "w1")[1]["id"] == "a"
    assert work.next_item(tmp_path, "w1")[1]["id"] == "a"
    assert work.next_item(tmp_path, "w2")[0] == "wait", "a is not done yet"
    assert work.finish(tmp_path, "w1", "a")[0] == "done"
    kind, why = work.next_item(tmp_path, "w1")
    assert kind == "none" and "every item" in why


def test_a_worker_waits_through_parked_items_instead_of_ending(tmp_path):
    """A parked item is the director's to revise; the worker keeps its session,
    waiting at no cost, instead of ending and being relaunched cold."""
    current = make_plan(item("a"), item("b", depends_on=["a"]))
    accept(tmp_path, current)
    work.next_item(tmp_path, "w1")
    work.park(tmp_path, "w1", "a", "needs a decision")
    kind, why = work.next_item(tmp_path, "w2")
    assert kind == "wait" and "parked" in why
    accept(tmp_path, make_plan(item("a", what="do a, decided"), item("b", depends_on=["a"])))
    assert work.next_item(tmp_path, "w2")[1]["id"] == "a", "the revised item goes out"


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


def test_failed_checks_never_take_the_item_away(tmp_path):
    """The worker owns its goal until the check passes. Three failures in a row make it
    ask an advisor, and it keeps the item."""
    marker = tmp_path / "fixed"
    accept(tmp_path, make_plan(item("a", check=f"test -f {marker}")))
    work.next_item(tmp_path, "w1")
    outcomes = [work.finish(tmp_path, "w1", "a") for n in range(3)]
    assert [o for o, _ in outcomes] == ["failed", "failed", "failed"]
    assert "CONSULT NOW" in outcomes[2][1] and "CONSULT NOW" not in outcomes[1][1]
    entry = work.load(tmp_path)["items"]["a"]
    assert entry["status"] == "claimed" and entry["worker"] == "w1"
    assert entry["consult_required"] and not entry.get("parks")
    assert "item_parked" not in [e["kind"] for e in work.events(tmp_path)]
    outcome, why = work.finish(tmp_path, "w1", "a")
    assert outcome == "refused" and "Ask an advisor" in why and "stays yours" in why
    work.item_consulted(tmp_path, "w1")
    assert not work.consult_required(tmp_path, "a")
    marker.write_text("")
    assert work.finish(tmp_path, "w1", "a")[0] == "done"


def test_a_pass_between_failures_starts_the_count_again(tmp_path):
    marker = tmp_path / "fixed"
    accept(tmp_path, make_plan(item("a", check=f"test -f {marker}")))
    work.next_item(tmp_path, "w1")
    assert work.finish(tmp_path, "w1", "a")[0] == "failed"
    assert work.finish(tmp_path, "w1", "a")[0] == "failed"
    marker.write_text("")
    assert work.finish(tmp_path, "w1", "a")[0] == "done"
    assert not work.consult_required(tmp_path, "a")


def test_a_consult_only_counts_for_the_item_its_worker_holds(tmp_path):
    accept(tmp_path, make_plan(item("a", check="false"), item("b")))
    work.next_item(tmp_path, "w1")
    for n in range(3):
        work.finish(tmp_path, "w1", "a")
    work.next_item(tmp_path, "w2")
    work.item_consulted(tmp_path, "w2")
    assert work.consult_required(tmp_path, "a"), "w2 asked about b, not a"


def test_a_deliberate_park_owes_no_consult(tmp_path):
    current = make_plan(item("a"))
    accept(tmp_path, current)
    for n in range(4):
        assert work.next_item(tmp_path, "w1")[0] == "item"
        assert work.park(tmp_path, "w1", "a", "the goal is wrong")[0]
        current["items"][0]["what"] += " revised"
        accept(tmp_path, current)
    assert not work.consult_required(tmp_path, "a")


def test_check_timeout(tmp_path):
    current = make_plan(item("slow", check="sleep 5", check_timeout=1))
    accept(tmp_path, current)
    work.next_item(tmp_path, "w1")
    outcome, output = work.finish(tmp_path, "w1", "slow")
    assert outcome == "failed" and "timed out after 1s" in output


def test_a_check_without_a_limit_runs_to_its_end(tmp_path):
    assert work.check_limit(item("a")) is None and work.check_limit(item("a", check_timeout=0)) is None
    current = make_plan(item("slow", check="sleep 2; true"))
    accept(tmp_path, current)
    work.next_item(tmp_path, "w1")
    assert work.finish(tmp_path, "w1", "slow")[0] == "done"


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


# --- shared resources: leases and the queue ---------------------------------

import os
import subprocess
import sys

from ccdirector import procs

ME = os.getpid()
ME_START = procs.identity(ME)


def take(root, worker, ticket, resource, units=1):
    return work.acquire(root, worker, ticket, resource, units, "test", ME, ME_START)


def test_a_lease_is_granted_when_units_fit_and_waits_when_they_do_not(tmp_path):
    accept(tmp_path, make_plan(item("a")))
    assert take(tmp_path, "w1", "t1", "pool", 3)["units"] == 3
    assert take(tmp_path, "w2", "t2", "pool", 2) is None, "3 + 2 > 4: waits"
    state = work.load(tmp_path)
    assert [q["ticket"] for q in state["queue"]] == ["t2"]
    assert work.release(tmp_path, "w1", "t1")["units"] == 3
    assert take(tmp_path, "w2", "t2", "pool", 2)["lease"] == "t2"
    assert work.load(tmp_path)["queue"] == []


def test_a_request_that_fits_goes_ahead_of_one_that_does_not(tmp_path):
    """Backfill: a 1-unit request does not idle behind a whole-pool one while a unit is
    free; the whole pool goes to the large one once it is free."""
    accept(tmp_path, make_plan(item("a")))
    take(tmp_path, "w1", "t1", "pool", 2)
    assert take(tmp_path, "w2", "big", "pool", 4) is None
    assert take(tmp_path, "w3", "small", "pool", 1) is not None, "1 of 2 free units"
    assert take(tmp_path, "w3", "rig", "rig") is not None, "another resource is not held up"
    work.release(tmp_path, "w1", "t1")
    work.release(tmp_path, "w3", "small")
    assert take(tmp_path, "w2", "big", "pool", 4) is not None, "everything free: big goes"
    assert work.load(tmp_path)["queue"] == []


def test_of_several_that_fit_the_earliest_goes_first(tmp_path):
    accept(tmp_path, make_plan(item("a")))
    take(tmp_path, "w1", "t1", "pool", 3)
    take(tmp_path, "w1", "t2", "pool", 1)
    assert take(tmp_path, "w2", "big", "pool", 4) is None
    assert take(tmp_path, "w3", "first", "pool", 1) is None, "nothing free yet"
    assert take(tmp_path, "w4", "second", "pool", 1) is None
    work.release(tmp_path, "w1", "t2")
    assert take(tmp_path, "w4", "second", "pool", 1) is None, "first fits too and is earlier"
    assert take(tmp_path, "w3", "first", "pool", 1) is not None
    assert take(tmp_path, "w4", "second", "pool", 1) is None, "nothing free again"


def test_the_head_is_not_passed_once_it_has_waited_long_enough(tmp_path):
    accept(tmp_path, make_plan(item("a")))
    take(tmp_path, "w1", "t1", "pool", 2)
    assert take(tmp_path, "w2", "big", "pool", 4) is None
    later = time.time() + work.HEAD_PATIENCE_SECONDS + 60
    assert work.acquire(tmp_path, "w2", "big", "pool", 4, "x", ME, ME_START,
                        clock=later) is None, "big is still asking"
    assert work.acquire(tmp_path, "w3", "small", "pool", 1, "x", ME, ME_START,
                        clock=later) is None, "big has waited too long: units drain to it"
    work.release(tmp_path, "w1", "t1")
    assert take(tmp_path, "w2", "big", "pool", 4) is not None


def test_withdraw_leaves_the_queue_and_unblocks_the_next(tmp_path):
    accept(tmp_path, make_plan(item("a")))
    take(tmp_path, "w1", "t1", "rig")
    take(tmp_path, "w2", "t2", "rig")
    take(tmp_path, "w3", "t3", "rig")
    work.withdraw(tmp_path, "t2")
    work.release(tmp_path, "w1", "t1")
    assert take(tmp_path, "w3", "t3", "rig") is not None


def test_only_its_worker_releases_a_lease(tmp_path):
    accept(tmp_path, make_plan(item("a")))
    take(tmp_path, "w1", "t1", "rig")
    assert work.release(tmp_path, "w2", "t1") is None
    assert work.release(tmp_path, "w1", "t1") is not None


def test_a_dead_holder_loses_its_lease(tmp_path):
    accept(tmp_path, make_plan(item("a")))
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone_start = procs.identity(gone.pid)
    gone.wait()
    assert work.acquire(tmp_path, "w1", "t1", "rig", 1, "x", gone.pid, gone_start)
    assert take(tmp_path, "w2", "t2", "rig") is not None, "w1's session is gone"
    log = (tmp_path / ".ccdirector" / "resources.log").read_text()
    assert "its worker's session is gone" in log


def test_a_worker_that_exits_gives_back_its_leases_and_queue_places(tmp_path):
    accept(tmp_path, make_plan(item("a")))
    take(tmp_path, "w1", "t1", "rig")
    take(tmp_path, "w1", "t2", "pool", 4)
    take(tmp_path, "w1", "t3", "rig")     # queued behind its own lease
    work.release_worker(tmp_path, "w1")
    state = work.load(tmp_path)
    assert state["leases"] == [] and state["queue"] == []


def test_a_claimed_item_holds_no_resource(tmp_path):
    """Workers on different goals share a resource through leases, unit by unit."""
    current = make_plan(item("a"), item("b"))
    accept(tmp_path, current)
    work.next_item(tmp_path, "w1")
    work.next_item(tmp_path, "w2")
    assert take(tmp_path, "w1", "t1", "pool", 2) is not None
    assert take(tmp_path, "w2", "t2", "pool", 2) is not None, "4 units, 2 each"
    assert work.resource_lines(work.load(tmp_path), current) == [
        "pool 4/4, held by w1 2, w2 2"]


def test_start_clears_leases_of_an_earlier_run(tmp_path):
    accept(tmp_path, make_plan(item("a")))
    take(tmp_path, "w1", "t1", "rig")
    take(tmp_path, "w2", "t2", "rig")
    work.reset_claims(tmp_path)
    state = work.load(tmp_path)
    assert state["leases"] == [] and state["queue"] == []


def test_routine_leases_do_not_wake_the_director(tmp_path):
    accept(tmp_path, make_plan(item("a")))
    before = len(work.events(tmp_path))
    take(tmp_path, "w1", "t1", "rig")
    work.release(tmp_path, "w1", "t1")
    assert len(work.events(tmp_path)) == before


def test_park_until_waits_and_returns_when_the_result_is_in(tmp_path):
    """An item parked until a result is not the director's to answer: ccdirector
    puts it back the moment its condition holds, first in line for its worker."""
    current = make_plan(item("a"), item("a0"))
    accept(tmp_path, current)
    assert work.next_item(tmp_path, "w1")[1]["id"] == "a"
    result = tmp_path / "lap.done"
    ok, detail = work.park(tmp_path, "w1", "a", "lap queued; grade lap.done",
                           until=f"test -e {result}")
    assert ok, detail
    entry = work.load(tmp_path)["items"]["a"]
    assert entry["status"] == "waiting" and entry["worker"] is None
    assert entry.get("parks", 0) == 0, "a wait is not a failure and owes no consult"
    assert [e["kind"] for e in work.events(tmp_path)][-1] == "item_waiting"
    state = work.load(tmp_path)
    assert not work.stalled(state, current), "a result is coming"
    assert work.next_item(tmp_path, "w2")[1]["id"] == "a0"
    assert work.wake_waiting(tmp_path) == [], "the result is not in yet"
    result.write_text("")
    assert work.wake_waiting(tmp_path) == [f"item a resumed: `test -e {result}`"]
    entry = work.load(tmp_path)["items"]["a"]
    assert entry["status"] == "pending" and "until" not in entry
    assert work.resumed(tmp_path, "a")["note"] == "lap queued; grade lap.done"
    assert work.finish(tmp_path, "w2", "a0")[0] == "done"
    assert work.next_item(tmp_path, "w1")[1]["id"] == "a"
    assert work.finish(tmp_path, "w1", "a")[0] == "done"
    assert work.resumed(tmp_path, "a") is None


def test_the_worker_that_parked_an_item_is_first_in_line_for_it(tmp_path):
    current = make_plan(item("b"), item("a"))
    accept(tmp_path, current)
    state = work.load(tmp_path)
    state["items"]["b"]["parked_by"] = "w1"
    assert work.ready(state, current, "w1") == ["b", "a"]
    assert work.ready(state, current, "w2") == ["a", "b"]


def test_park_until_refuses_a_condition_that_cannot_wait(tmp_path, monkeypatch):
    current = make_plan(item("a"))
    accept(tmp_path, current)
    work.next_item(tmp_path, "w1")
    ok, why = work.park(tmp_path, "w1", "a", "r", until="true")
    assert not ok and "already succeeds" in why
    ok, why = work.park(tmp_path, "w1", "a", "r", until="no-such-command-ccdirector")
    assert not ok and "cannot be run" in why
    monkeypatch.setattr(work, "UNTIL_TEST_SECONDS", 1)
    ok, why = work.park(tmp_path, "w1", "a", "r", until="sleep 5")
    assert not ok and "must not wait" in why
    ok, why = work.park(tmp_path, "w1", "a", "r", until="false", hours=0)
    assert not ok and "positive" in why
    assert work.load(tmp_path)["items"]["a"]["status"] == "claimed", "refusals keep the item"


def test_a_wait_past_its_hours_goes_to_the_director(tmp_path):
    current = make_plan(item("a"))
    accept(tmp_path, current)
    work.next_item(tmp_path, "w1")
    assert work.park(tmp_path, "w1", "a", "lap queued", until="false", hours=1)[0]
    assert work.wake_waiting(tmp_path, clock=time.time() + 1800) == []
    changes = work.wake_waiting(tmp_path, clock=time.time() + 3601)
    assert changes == ["item a parked: `false` never came"]
    entry = work.load(tmp_path)["items"]["a"]
    assert entry["status"] == "parked" and entry["parks"] == 1 and "until" not in entry
    assert entry["note"].startswith("waited since") and "lap queued" in entry["note"]
    assert work.stalled(work.load(tmp_path), current)


def test_revising_a_waiting_item_returns_it_to_pending(tmp_path):
    accept(tmp_path, make_plan(item("a")))
    work.next_item(tmp_path, "w1")
    work.park(tmp_path, "w1", "a", "lap queued", until="false")
    changes = accept(tmp_path, make_plan(item("a", what="do a differently")))
    assert "item a revised: waiting -> pending" in changes
    entry = work.load(tmp_path)["items"]["a"]
    assert entry["status"] == "pending" and "until" not in entry
    assert work.wake_waiting(tmp_path) == []


def test_park_after_waits_for_another_goal(tmp_path):
    """A goal that turns out to need another goal done first waits for it, not for
    the director, and comes back to the worker that parked it."""
    current = make_plan(item("api"), item("client"), item("other", 2))
    accept(tmp_path, current)
    assert work.next_item(tmp_path, "w1")[1]["id"] == "api"
    assert work.next_item(tmp_path, "w2")[1]["id"] == "client"
    ok, detail = work.park(tmp_path, "w2", "client", "needs the new api call", after="api")
    assert ok and "api is done" in detail
    entry = work.load(tmp_path)["items"]["client"]
    assert entry["status"] == "waiting" and entry["after"] == "api" and "until" not in entry
    assert "until api done" in work.summary(tmp_path)
    assert work.next_item(tmp_path, "w2")[1]["id"] == "other", "w2 takes other work"
    assert work.wake_waiting(tmp_path) == []
    assert work.finish(tmp_path, "w1", "api")[0] == "done"
    assert work.wake_waiting(tmp_path) == ["item client resumed: api done"]
    assert work.resumed(tmp_path, "client")["waited_for"] == "api done"
    assert work.finish(tmp_path, "w2", "other")[0] == "done"
    assert work.next_item(tmp_path, "w1")[1]["id"] == "client"


def test_park_after_refuses_what_it_cannot_wait_for(tmp_path):
    accept(tmp_path, make_plan(item("a"), item("b")))
    work.next_item(tmp_path, "w1")
    work.next_item(tmp_path, "w2")
    assert work.finish(tmp_path, "w2", "b")[0] == "done"
    for after, words in (("a", "itself"), ("ghost", "no item"), ("b", "already done")):
        ok, why = work.park(tmp_path, "w1", "a", "r", after=after)
        assert not ok and words in why
    ok, why = work.park(tmp_path, "w1", "a", "r", after="b", until="false")
    assert not ok and "not both" in why
    assert work.load(tmp_path)["items"]["a"]["status"] == "claimed"


def test_only_a_change_to_the_work_reopens_a_done_item(tmp_path):
    """Removing a field the plan format dropped, or changing priority, paths or size,
    is bookkeeping; it must not send finished items back to be done again."""
    current = make_plan(item("a"), item("b"))
    accept(tmp_path, current)
    for worker in ("w1", "w2"):
        item_id = work.next_item(tmp_path, worker)[1]["id"]
        assert work.finish(tmp_path, worker, item_id)[0] == "done"
    shuffled = make_plan(item("a", 4, paths=["src/a.c"], size="L", section="x"),
                         item("b", check="test -e b.done"))
    changes = accept(tmp_path, shuffled)
    assert changes == ["item b revised: done -> pending"]
    state = work.load(tmp_path)
    assert state["items"]["a"]["status"] == "done" and state["items"]["b"]["status"] == "pending"


def test_state_recorded_before_work_digests_is_compared_once_the_old_way(tmp_path):
    current = make_plan(item("a"))
    accept(tmp_path, current)
    work.next_item(tmp_path, "w1")
    work.finish(tmp_path, "w1", "a")
    with work.locked(tmp_path):
        state = work.load(tmp_path)
        entry = state["items"]["a"]
        entry.pop("work_digest")
        entry["digest"] = work.digest(current["items"][0])     # as 0.17.0 recorded it
        work.save(tmp_path, state)
    assert accept(tmp_path, current) == [], "unchanged: adopted without a revision"
    entry = work.load(tmp_path)["items"]["a"]
    assert entry["status"] == "done" and "work_digest" in entry and "digest" not in entry
    assert accept(tmp_path, make_plan(item("a", 2))) == [], "and from then on only work counts"


def test_start_clears_consults_flagged_by_the_old_park_rule(tmp_path):
    accept(tmp_path, make_plan(item("old"), item("real", check="false")))
    with work.locked(tmp_path):
        state = work.load(tmp_path)
        state["items"]["old"].update(status="pending", worker=None, consult_required=True,
                                     parks=3, fails_in_a_row=0)
        state["items"]["real"].update(status="claimed", worker="w2", consult_required=True,
                                      fails_in_a_row=3)
        work.save(tmp_path, state)
    work.reset_claims(tmp_path)
    assert not work.consult_required(tmp_path, "old"), "flagged by parks, not by failures"
    assert work.consult_required(tmp_path, "real"), "three failed checks still owe one"


def test_a_request_whose_caller_stopped_asking_is_dropped(tmp_path):
    """A frozen or dropped acquire never withdraws, and its claude lives on; left in
    line it would never take its units and would hold back everyone it fits ahead of."""
    accept(tmp_path, make_plan(item("a")))
    take(tmp_path, "w1", "t1", "pool", 4)
    start = time.time()
    assert work.acquire(tmp_path, "w2", "frozen", "pool", 2, "x", ME, ME_START, clock=start) is None
    assert work.acquire(tmp_path, "w3", "live", "pool", 2, "x", ME, ME_START, clock=start) is None
    work.release(tmp_path, "w1", "t1")
    soon = start + work.ABANDONED_SECONDS - 10
    assert work.acquire(tmp_path, "w3", "live", "pool", 2, "x", ME, ME_START, clock=soon) is None, \
        "frozen is earlier and fits, so it goes first while it may still be asking"
    late = start + work.ABANDONED_SECONDS + 10
    assert work.acquire(tmp_path, "w3", "live", "pool", 2, "x", ME, ME_START, clock=late) is not None, \
        "frozen stopped asking: it is dropped and live goes"
    assert work.load(tmp_path)["queue"] == []
    log = (tmp_path / ".ccdirector" / "resources.log").read_text()
    assert '"what": "abandoned"' in log and "stopped asking" in log
