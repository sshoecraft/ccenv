"""The MCP tool surface via module-level dispatch (no ccenvmcp needed)."""

from __future__ import annotations

import json

from ccprospect import paths
from ccprospect.mcp_server import SCHEMAS, dispatch
from tests.conftest import in_days, touch


def call(name, arguments=None):
    result = dispatch(name, arguments or {})
    assert len(result) == 1 and result[0]["type"] == "text"
    return result[0]["text"]


def call_json(name, arguments=None):
    return json.loads(call(name, arguments))


def file_args(**over):
    args = {
        "title": "revisit Node pin",
        "intention": "re-test ensure_node() against Node 23; drop the pin if CI passes",
        "predicate": {"type": "path_exists", "path": "node23.flag"},
        "expires": in_days(30),
    }
    args.update(over)
    return args


def test_read_tools_without_store(startup_dir):
    inbox = call_json("prospect_inbox")
    assert inbox["pending_count"] == 0 and "no .ccprospect/" in inbox["note"]
    assert call_json("prospect_list") == []
    assert "no .ccprospect/" in call("prospect_get", {"id": "p-0001"})
    assert not paths.startup_prospect_dir(startup_dir).exists()  # reads never litter


def test_file_creates_store_and_contract(startup_dir):
    out = call_json("prospect_file", file_args())
    assert out["id"] == "p-0001"
    assert paths.contracts_dir(paths.startup_prospect_dir(startup_dir)).exists()

    rows = call_json("prospect_list")
    assert rows[0]["id"] == "p-0001" and rows[0]["attention"] == "open"

    detail = call_json("prospect_get", {"id": "1"})
    assert detail["intention"].startswith("re-test ensure_node()")
    assert [e["event"] for e in detail["events"]] == ["created"]


def test_refusals_come_back_as_refused_text(startup_dir):
    touch(startup_dir, "already.flag")
    msg = call("prospect_file", file_args(
        predicate={"type": "path_exists", "path": "already.flag"}))
    assert msg.startswith("refused:") and "already true" in msg

    msg = call("prospect_file", file_args(expires=in_days(-1)))
    assert msg.startswith("refused:") and "future" in msg


def test_fire_ack_report_flow(startup_dir):
    call_json("prospect_file", file_args())
    touch(startup_dir, "node23.flag")

    inbox = call_json("prospect_inbox")
    assert inbox["pending_count"] == 1
    assert inbox["fired"][0]["observed"]["exists"] is True

    # gate: creation refused while fired sits unacknowledged
    msg = call("prospect_file", file_args(
        title="second", predicate={"type": "path_exists", "path": "other.flag"}))
    assert msg.startswith("refused:") and "awaiting acknowledgment" in msg

    acked = call_json("prospect_ack",
                      {"id": "p-0001", "disposition": "done", "evidence": "commit deadbeef"})
    assert acked["attention"] == "closed" and acked["resolution"] == "done"

    report = call_json("prospect_report")
    assert report["total"] == 1 and report["by_resolution"]["done"] == 1


def test_amend_flow(startup_dir):
    call_json("prospect_file", file_args())
    out = call_json("prospect_amend", {"id": "p-0001", "title": "revisit Node pin (v2)"})
    assert out == {"superseded": "p-0001", "successor": "p-0002", "note": out["note"]}
    rows = {r["id"]: r for r in call_json("prospect_list")}
    assert rows["p-0001"]["attention"] == "closed"
    assert rows["p-0002"]["title"] == "revisit Node pin (v2)"


def test_unknown_tool(startup_dir):
    assert call("prospect_nonsense") == "unknown tool: prospect_nonsense"


def test_every_schema_has_description_and_object_type():
    from ccprospect.mcp_server import DESCRIPTIONS
    assert set(SCHEMAS) == set(DESCRIPTIONS)
    for name, schema in SCHEMAS.items():
        assert schema["type"] == "object", name
