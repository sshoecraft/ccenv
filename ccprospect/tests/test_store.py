"""Contract creation: validation, immutable round-trip, gate, caps, ids."""

from __future__ import annotations

import json

import pytest

from ccprospect import contracts as contracts_mod
from ccprospect import events as events_mod
from ccprospect import paths
from ccprospect.contracts import ContractError
from ccprospect.store import Store, StoreError
from tests.conftest import file_prospect, in_days, touch


def test_create_roundtrip(store, startup_dir):
    c = file_prospect(store, title="revisit Node pin",
                      intention="re-test ensure_node() against Node 23",
                      evidence="pinned in install.sh because of npm ENOTEMPTY")
    assert c.id == "p-0001"
    assert c.path.name == "p-0001-revisit-node-pin.md"

    reread = contracts_mod.parse_contract(c.path)
    assert reread.title == "revisit Node pin"
    assert reread.intention == "re-test ensure_node() against Node 23"
    assert reread.predicate["type"] == "path_exists"
    assert reread.evidence.startswith("pinned in install.sh")
    assert reread.created_at and reread.expires

    evs = events_mod.read_events(store.prospect_dir)
    assert [e["event"] for e in evs] == ["created"]
    assert (store.prospect_dir / ".gitignore").exists()

    st = store.states()[c.id]
    assert st.attention == "open" and st.resolution == "pending"


def test_create_field_validation(store):
    with pytest.raises(StoreError, match="title is required"):
        file_prospect(store, title="  ")
    with pytest.raises(StoreError, match="intention is required"):
        file_prospect(store, intention="")
    with pytest.raises(StoreError, match="future"):
        file_prospect(store, expires=in_days(-1))
    with pytest.raises(StoreError, match="unparseable 'expires'"):
        file_prospect(store, expires="whenever")
    with pytest.raises(StoreError, match="only meaningful with an 'expect'"):
        file_prospect(store, bucket=60)
    with pytest.raises(StoreError, match="must be one of"):
        file_prospect(store, bucket=50, expect="it will merge")
    c = file_prospect(store, bucket=60, expect="it will merge")
    assert c.bucket == 60 and c.expect == "it will merge"


def test_create_gate_refuses_while_fired_unacked(store, startup_dir):
    file_prospect(store, predicate={"type": "path_exists", "path": "go.flag"})
    touch(startup_dir, "go.flag")
    inbox = store.inbox()
    assert inbox["pending_count"] == 1 and inbox["fired"][0]["id"] == "p-0001"

    with pytest.raises(StoreError, match="awaiting acknowledgment"):
        file_prospect(store, title="second")

    store.ack("p-0001", "done")
    c2 = file_prospect(store, title="second")
    assert c2.id == "p-0002"


def test_active_cap(store, monkeypatch):
    monkeypatch.setenv("CCPROSPECT_MAX_ACTIVE", "2")
    file_prospect(store, title="one", predicate={"type": "path_exists", "path": "a"})
    file_prospect(store, title="two", predicate={"type": "path_exists", "path": "b"})
    with pytest.raises(StoreError, match="attention budget"):
        file_prospect(store, title="three", predicate={"type": "path_exists", "path": "c"})
    store.ack("p-0001", "cancel_attention", note="making room")
    file_prospect(store, title="three", predicate={"type": "path_exists", "path": "c"})


def test_daily_budget(store, monkeypatch):
    monkeypatch.setenv("CCPROSPECT_DAILY_BUDGET", "2")
    file_prospect(store, title="one", predicate={"type": "path_exists", "path": "a"})
    file_prospect(store, title="two", predicate={"type": "path_exists", "path": "b"})
    with pytest.raises(StoreError, match="daily creation budget"):
        file_prospect(store, title="three", predicate={"type": "path_exists", "path": "c"})


def test_resolve_id_forms(store):
    file_prospect(store, title="alpha", predicate={"type": "path_exists", "path": "a"})
    file_prospect(store, title="beta", predicate={"type": "path_exists", "path": "b"})
    assert store.resolve_id("p-0002") == "p-0002"
    assert store.resolve_id("2") == "p-0002"
    assert store.resolve_id("p-0001") == "p-0001"
    with pytest.raises(ContractError, match="ambiguous"):
        store.resolve_id("p-000")
    with pytest.raises(ContractError, match="no prospect matches"):
        store.resolve_id("p-9999")


def test_contract_files_are_never_overwritten(store):
    c = file_prospect(store)
    with pytest.raises(FileExistsError):
        contracts_mod.write_contract(store.prospect_dir, {
            "id": c.id, "title": c.title, "intention": "overwrite attempt",
            "predicate": c.predicate, "expires": c.expires,
            "created_at": c.created_at,
        })


def test_load_all_skips_appledouble_sidecars(store):
    file_prospect(store)
    junk = paths.contracts_dir(store.prospect_dir) / "._p-0001-junk.md"
    junk.write_text("garbage")
    loaded = contracts_mod.load_all(store.prospect_dir)
    assert list(loaded) == ["p-0001"]


def test_corrupt_event_lines_are_skipped(store, startup_dir):
    file_prospect(store)
    with open(paths.events_path(store.prospect_dir), "a", encoding="utf-8") as fh:
        fh.write("not json at all\n")
        fh.write(json.dumps({"event": "ack", "id": "p-0001", "disposition": "keep"}) + "\n")
    st = store.states()["p-0001"]
    assert st.attention == "open"  # keep on an unfired item stays open
