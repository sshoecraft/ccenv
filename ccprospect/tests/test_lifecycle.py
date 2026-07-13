"""The state machine end to end: fire → ack paths, deferral, counterfactual
resolution of cancelled/superseded contracts, expiry, one-shot latching,
probe rate limits, and the factual report."""

from __future__ import annotations

import pytest

from ccprospect import events as events_mod
from ccprospect.store import Store, StoreError
from tests.conftest import file_prospect, in_days, touch


def test_fire_then_done(store, startup_dir):
    file_prospect(store, predicate={"type": "path_exists", "path": "done.flag"})
    touch(startup_dir, "done.flag")
    inbox = store.inbox()
    row = inbox["fired"][0]
    assert row["observed"]["exists"] is True  # mechanically populated
    assert row["intention"] == "do the thing"

    result = store.ack("p-0001", "done", evidence="commit abc123")
    assert result["attention"] == "closed"
    assert result["resolution"] == "done"
    assert store.inbox()["pending_count"] == 0


def test_ack_validation(store, startup_dir):
    file_prospect(store)
    with pytest.raises(StoreError, match="disposition must be one of"):
        store.ack("p-0001", "shrug")
    with pytest.raises(StoreError, match="requires resolution"):
        store.ack("p-0001", "resolve")
    with pytest.raises(StoreError, match="requires next_review"):
        store.ack("p-0001", "defer")
    with pytest.raises(StoreError, match="requires a note"):
        store.ack("p-0001", "cancel_attention")
    store.ack("p-0001", "resolve", resolution="unresolvable", note="answered out of band")
    with pytest.raises(StoreError, match="already closed"):
        store.ack("p-0001", "done")


def test_keep_after_fire_leaves_inbox_but_stays_active(store, startup_dir):
    file_prospect(store, predicate={"type": "path_exists", "path": "seen.flag"})
    touch(startup_dir, "seen.flag")
    assert store.inbox()["pending_count"] == 1
    store.ack("p-0001", "keep", note="still relevant, working through it")
    inbox = store.inbox()
    assert inbox["pending_count"] == 0
    assert inbox["active"] == 1
    assert store.states()["p-0001"].attention == "acked"


def test_defer_resurfaces_when_due(store, startup_dir, clock):
    file_prospect(store, predicate={"type": "path_exists", "path": "later.flag"},
                  expires=in_days(30, clock.now))
    touch(startup_dir, "later.flag")
    store.inbox()  # fires p-0001
    store.ack("p-0001", "defer", next_review=in_days(2, clock.now))
    inbox = store.inbox()
    assert inbox["pending_count"] == 0
    assert store.states()["p-0001"].attention == "deferred"

    clock.advance(days=3)
    inbox = store.inbox()
    assert inbox["due"][0]["id"] == "p-0001"
    assert inbox["pending_count"] == 1


def test_one_shot_latching(store, startup_dir):
    file_prospect(store, predicate={"type": "path_exists", "path": "once.flag"})
    touch(startup_dir, "once.flag")
    store.evaluate()
    store.evaluate()
    fired_events = [e for e in events_mod.read_events(store.prospect_dir)
                    if e["event"] == "fired"]
    assert len(fired_events) == 1


def test_cancel_attention_still_resolves_counterfactually(store, startup_dir):
    file_prospect(store, predicate={"type": "path_exists", "path": "watched.flag"},
                  expect="this flag will appear", bucket=60)
    store.ack("p-0001", "cancel_attention", note="lost interest")
    st = store.states()["p-0001"]
    assert st.attention == "closed" and st.closed_reason == "cancelled"
    assert st.resolution == "pending"  # the outcome is still open

    touch(startup_dir, "watched.flag")
    result = store.evaluate()
    assert result["fired"][0]["counterfactual"] is True

    st = store.states()["p-0001"]
    assert st.resolution == "hit"
    assert st.resolved_counterfactually
    assert st.attention == "closed"  # firing does not reopen attention
    assert store.inbox()["pending_count"] == 0

    report = store.report()
    assert report["cancelled"]["n"] == 1
    assert report["cancelled"]["counterfactual_hit"] == 1


def test_amend_supersedes_and_original_keeps_resolving(store, startup_dir):
    file_prospect(store, title="watch config",
                  predicate={"type": "path_exists", "path": "old-trigger.flag"})
    result = store.amend("p-0001", intention="check the NEW trigger instead",
                         predicate={"type": "path_exists", "path": "new-trigger.flag"})
    assert result == {"superseded": "p-0001", "successor": "p-0002",
                      "note": result["note"]}
    assert "counterfactually" in result["note"]

    states = store.states()
    assert states["p-0001"].attention == "closed"
    assert states["p-0001"].closed_reason == "superseded"
    assert states["p-0001"].successor == "p-0002"
    assert states["p-0002"].contract.predecessor == "p-0001"
    assert states["p-0002"].contract.title == "watch config"  # inherited
    assert states["p-0002"].contract.intention == "check the NEW trigger instead"

    # the superseded original still fires counterfactually
    touch(startup_dir, "old-trigger.flag")
    store.evaluate()
    st = store.states()["p-0001"]
    assert st.resolution == "hit" and st.resolved_counterfactually
    assert store.report()["superseded"]["counterfactual_hit"] == 1


def test_amend_closed_contract_refused(store):
    file_prospect(store)
    store.ack("p-0001", "done")
    with pytest.raises(StoreError, match="closed"):
        store.amend("p-0001", title="zombie")


def test_amend_is_exempt_from_fired_gate(store, startup_dir):
    file_prospect(store, title="fires", predicate={"type": "path_exists", "path": "f.flag"})
    file_prospect(store, title="other", predicate={"type": "path_exists", "path": "g.flag"})
    touch(startup_dir, "f.flag")
    store.evaluate()
    # p-0001 fired+unacked: plain creation is gated, but amending p-0002 is
    # a slot-neutral replacement and stays legal.
    with pytest.raises(StoreError, match="awaiting acknowledgment"):
        file_prospect(store, title="third", predicate={"type": "path_exists", "path": "h.flag"})
    result = store.amend("p-0002", title="other, revised")
    assert result["successor"] == "p-0003"


def test_expiry_resolves_expired(store, clock):
    file_prospect(store,
                  predicate={"type": "at", "time": in_days(10, clock.now)},
                  expires=in_days(1, clock.now),
                  expect="the release lands within a day", bucket=40)
    clock.advance(days=2)
    result = store.evaluate()
    assert result["expired"] == ["p-0001"]
    st = store.states()["p-0001"]
    assert st.resolution == "expired"
    assert st.attention == "closed" and st.closed_reason == "expired"

    report = store.report()
    assert report["calibration_by_bucket"]["40"] == {
        "n": 1, "hit": 0, "miss": 0, "expired": 1, "unresolvable": 0, "pending": 0}


def test_expiry_final_evaluation_can_still_fire(store, startup_dir, clock):
    file_prospect(store, predicate={"type": "path_exists", "path": "late.flag"},
                  expires=in_days(1, clock.now))
    clock.advance(days=2)
    touch(startup_dir, "late.flag")
    result = store.evaluate()
    # past expiry, but the final evaluation catches the fire first
    assert result["fired"] and not result["expired"]
    assert store.states()["p-0001"].attention == "fired"


def test_at_predicate_fires_via_clock(store, clock):
    file_prospect(store, predicate={"type": "at", "time": in_days(1, clock.now)},
                  expires=in_days(30, clock.now))
    assert store.evaluate()["fired"] == []
    clock.advance(days=1, seconds=1)
    fired = store.evaluate()["fired"]
    assert fired[0]["id"] == "p-0001"
    assert fired[0]["observed"]["time"]


def test_session_start_fires_only_at_session_start(store, clock):
    file_prospect(store, predicate={"type": "session_start"},
                  expires=in_days(30, clock.now))
    clock.advance(minutes=5)
    assert store.evaluate(at_session_start=False)["fired"] == []
    fired = store.evaluate(at_session_start=True)["fired"]
    assert fired[0]["id"] == "p-0001"


def test_cmd_min_interval_rate_limit(store, startup_dir):
    file_prospect(store, title="rate limited",
                  predicate={"type": "cmd_ok", "run": "test -f probe.flag"})
    touch(startup_dir, "probe.flag")
    # creation probe stamped the watermark; default min_interval blocks re-probe
    result = store.evaluate()
    assert result["probes_run"] == 0 and result["fired"] == []

    store2 = Store(store.prospect_dir)
    file_prospect(store2, title="eager",
                  predicate={"type": "cmd_ok", "run": "test -f probe2.flag",
                             "min_interval": 0})
    touch(startup_dir, "probe2.flag")
    result = store2.evaluate()
    assert [f["id"] for f in result["fired"]] == ["p-0002"]


def test_no_probes_skips_cmd_but_not_paths(store, startup_dir):
    file_prospect(store, title="cmd",
                  predicate={"type": "cmd_ok", "run": "test -f cp.flag", "min_interval": 0})
    file_prospect(store, title="path", predicate={"type": "path_exists", "path": "pp.flag"})
    touch(startup_dir, "cp.flag")
    touch(startup_dir, "pp.flag")
    result = store.evaluate(allow_probes=False)
    assert [f["id"] for f in result["fired"]] == ["p-0002"]
    assert result["probes_run"] == 0


def test_expiry_with_no_probes_records_probe_skipped(store, clock):
    file_prospect(store, title="cmd",
                  predicate={"type": "cmd_ok", "run": "test -f never.flag", "min_interval": 0},
                  expires=in_days(1, clock.now))
    clock.advance(days=2)
    result = store.evaluate(allow_probes=False)
    assert result["expired"] == ["p-0001"]
    evs = [e for e in events_mod.read_events(store.prospect_dir) if e["event"] == "expired"]
    assert evs[0]["probe_skipped"] is True


def test_report_shapes(store, startup_dir):
    file_prospect(store, title="a", predicate={"type": "path_exists", "path": "ra.flag"})
    file_prospect(store, title="b", predicate={"type": "path_exists", "path": "rb.flag"},
                  expect="b appears first", bucket=60)
    touch(startup_dir, "rb.flag")
    store.inbox()
    store.ack("p-0002", "resolve", resolution="hit", evidence="rb.flag observed")

    report = store.report()
    assert report["total"] == 2
    assert report["active"] == 1
    assert report["by_resolution"]["hit"] == 1
    assert report["calibration_by_bucket"]["60"]["hit"] == 1
    assert report["ack_latency_hours"]["n"] == 1
    assert report["age_days"]["active"]["n"] == 1


def test_list_filters(store, startup_dir):
    file_prospect(store, title="a", predicate={"type": "path_exists", "path": "la.flag"})
    file_prospect(store, title="b", predicate={"type": "path_exists", "path": "lb.flag"})
    store.ack("p-0001", "cancel_attention", note="cleanup")
    assert {r["id"] for r in store.list_all("active")} == {"p-0002"}
    assert {r["id"] for r in store.list_all("closed")} == {"p-0001"}
    assert len(store.list_all()) == 2
    assert len(store.list_all("all")) == 2
