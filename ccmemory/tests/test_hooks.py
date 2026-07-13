"""Hook handlers: fail-open behavior, sentinel, output shapes."""

import io
import json
import os
import sys

import pytest

from ccmemory import hooks
from ccmemory.store import Store
from tests.conftest import write_memory


def _capture(monkeypatch, payload, env=None):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload) if payload else ""))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    if env:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    return out


def _reindex(memory_dir):
    """inject_handler deliberately doesn't reindex itself — it free-rides on
    the mandatory session-start memory_list() call, which always reindexes
    (see mcp_server.dispatch). Tests that need the FTS index warm must do
    this explicitly to simulate that."""
    with Store(memory_dir) as store:
        store.reindex()


def test_guard_blocks_MEMORY_md(monkeypatch):
    out = _capture(monkeypatch, {"tool_name": "Write", "tool_input": {"file_path": "/x/MEMORY.md"}})
    rc = hooks.guard_handler()
    assert rc == 2
    payload = json.loads(out.getvalue())
    assert payload["permissionDecision"] == "deny"


def test_guard_allows_other_writes(monkeypatch):
    out = _capture(monkeypatch, {"tool_name": "Write", "tool_input": {"file_path": "/x/foo.py"}})
    rc = hooks.guard_handler()
    assert rc == 0
    assert out.getvalue() == ""


def test_stop_skips_when_sentinel_present(memory_dir, monkeypatch, capsys):
    write_memory(memory_dir, "foo")
    (memory_dir / hooks.SKIP_REGEN_SENTINEL).write_text("skip\n")
    _capture(monkeypatch, {})
    rc = hooks.stop_handler()
    assert rc == 0
    assert not (memory_dir / "MEMORY.md").exists()


def test_stop_regenerates_when_no_sentinel(memory_dir, monkeypatch):
    write_memory(memory_dir, "foo", description="useful description")
    _capture(monkeypatch, {})
    rc = hooks.stop_handler()
    assert rc == 0
    assert (memory_dir / "MEMORY.md").exists()
    assert "useful description" in (memory_dir / "MEMORY.md").read_text()


def test_session_emits_protocol(memory_dir, monkeypatch):
    write_memory(memory_dir, "foo")
    out = _capture(monkeypatch, {})
    rc = hooks.session_handler()
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "memory_search" in payload["hookSpecificOutput"]["additionalContext"]


def test_session_noop_when_no_memory_dir(tmp_path, monkeypatch):
    # A startup dir with no .ccmemory/ (and no legacy store) → nothing to emit.
    monkeypatch.chdir(tmp_path)
    out = _capture(monkeypatch, {})
    rc = hooks.session_handler()
    assert rc == 0
    assert out.getvalue() == ""


def test_inject_emits_relevant_hits(memory_dir, monkeypatch):
    write_memory(memory_dir, "xfs_inode_lesson", description="lessons about xfs_inode.c clobber bug")
    _reindex(memory_dir)
    out = _capture(monkeypatch, {
        "tool_name": "Read",
        "tool_input": {"file_path": "/src/mxfs/fs/xfs/xfs_inode.c"},
        "session_id": "S1",
    })
    rc = hooks.inject_handler()
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "xfs_inode_lesson" in payload["hookSpecificOutput"]["additionalContext"]


def test_inject_ignores_non_Read(monkeypatch):
    out = _capture(monkeypatch, {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    rc = hooks.inject_handler()
    assert rc == 0
    assert out.getvalue() == ""


def test_inject_fails_shut_without_session_id(memory_dir, monkeypatch):
    """No session_id -> can't key the dedup ledger -> emit nothing, never
    fall back to unranked/unbounded injection."""
    write_memory(memory_dir, "xfs_inode_lesson", description="lessons about xfs_inode.c clobber bug")
    _reindex(memory_dir)
    out = _capture(monkeypatch, {"tool_name": "Read", "tool_input": {"file_path": "/src/mxfs/fs/xfs/xfs_inode.c"}})
    rc = hooks.inject_handler()
    assert rc == 0
    assert out.getvalue() == ""


def test_inject_dedups_same_slug_across_reads(memory_dir, monkeypatch):
    write_memory(memory_dir, "xfs_inode_lesson", description="lessons about xfs_inode.c clobber bug")
    _reindex(memory_dir)
    payload = {"tool_name": "Read", "tool_input": {"file_path": "/src/mxfs/fs/xfs/xfs_inode.c"}, "session_id": "S1"}

    out1 = _capture(monkeypatch, payload)
    assert hooks.inject_handler() == 0
    assert "xfs_inode_lesson" in out1.getvalue()

    out2 = _capture(monkeypatch, payload)
    assert hooks.inject_handler() == 0
    assert out2.getvalue() == ""


def test_inject_never_repeats_a_slug_even_with_a_larger_pool(memory_dir, monkeypatch):
    """The single-memory dedup test above can't distinguish 'correctly
    deduped' from 'nothing else existed to fall through to'. With 6 matching
    candidates and CCMEMORY_INJECT_TOP_N=3, repeat Reads of the exact same
    file surface *different*, previously unseen slugs from the ranked pool
    (not silence) until it's exhausted — but no slug is ever shown twice.
    This is the real-world shape (confirmed live against a 79-memory store):
    a repeat Read isn't required to go silent, only to never duplicate."""
    for i in range(6):
        write_memory(memory_dir, f"pool-lesson-{i}", description=f"pool subsystem lesson number {i}")
    _reindex(memory_dir)
    payload = {"tool_name": "Read", "tool_input": {"file_path": "/src/pool/main.py"}, "session_id": "S1"}
    env = {"CCMEMORY_INJECT_TOP_N": "3"}

    seen: list[str] = []
    for _ in range(3):
        out = _capture(monkeypatch, payload, env=env)
        assert hooks.inject_handler() == 0
        ctx = out.getvalue()
        shown = []
        if ctx:
            body = json.loads(ctx)["hookSpecificOutput"]["additionalContext"]
            shown = [f"pool-lesson-{i}" for i in range(6) if f"pool-lesson-{i}" in body]
        assert not (set(shown) & set(seen)), "a slug repeated across reads"
        seen.extend(shown)

    assert sorted(seen) == [f"pool-lesson-{i}" for i in range(6)]  # all 6 surfaced, none twice, then exhausted


def test_inject_dedups_same_slug_across_different_files(memory_dir, monkeypatch):
    """Two different files that both rank the same memory: shown once, not
    once per file — this is the pathology observed in production (the same
    lesson re-teased on every file in a subsystem)."""
    write_memory(memory_dir, "widget-lesson", description="widget subsystem lesson")
    _reindex(memory_dir)
    payload1 = {"tool_name": "Read", "tool_input": {"file_path": "/src/widget/foo.py"}, "session_id": "S1"}
    payload2 = {"tool_name": "Read", "tool_input": {"file_path": "/other/widget/bar.py"}, "session_id": "S1"}

    out1 = _capture(monkeypatch, payload1)
    assert hooks.inject_handler() == 0
    assert "widget-lesson" in out1.getvalue()

    out2 = _capture(monkeypatch, payload2)
    assert hooks.inject_handler() == 0
    assert "widget-lesson" not in out2.getvalue()


def test_inject_per_read_cap(memory_dir, monkeypatch):
    for i in range(5):
        write_memory(memory_dir, f"gadget-lesson-{i}", description=f"gadget subsystem lesson number {i}")
    _reindex(memory_dir)
    out = _capture(
        monkeypatch,
        {"tool_name": "Read", "tool_input": {"file_path": "/src/gadget/main.py"}, "session_id": "S1"},
        env={"CCMEMORY_INJECT_TOP_N": "2"},
    )
    assert hooks.inject_handler() == 0
    ctx = json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]
    emitted = sum(1 for i in range(5) if f"gadget-lesson-{i}" in ctx)
    assert emitted == 2


def test_inject_session_cap_stops_further_injection(memory_dir, monkeypatch):
    write_memory(memory_dir, "lesson-one", description="lesson about apple module")
    write_memory(memory_dir, "lesson-two", description="lesson about banana module")
    write_memory(memory_dir, "lesson-three", description="lesson about cherry module")
    _reindex(memory_dir)
    env = {"CCMEMORY_INJECT_SESSION_MAX": "2", "CCMEMORY_INJECT_TOP_N": "1"}

    out1 = _capture(monkeypatch, {"tool_name": "Read", "tool_input": {"file_path": "/src/apple.py"}, "session_id": "S1"}, env=env)
    assert hooks.inject_handler() == 0
    assert "lesson-one" in out1.getvalue()

    out2 = _capture(monkeypatch, {"tool_name": "Read", "tool_input": {"file_path": "/src/banana.py"}, "session_id": "S1"}, env=env)
    assert hooks.inject_handler() == 0
    assert "lesson-two" in out2.getvalue()

    # session_max already reached (2 unique slugs claimed) -> nothing further
    out3 = _capture(monkeypatch, {"tool_name": "Read", "tool_input": {"file_path": "/src/cherry.py"}, "session_id": "S1"}, env=env)
    assert hooks.inject_handler() == 0
    assert out3.getvalue() == ""


def test_session_handler_resets_ledger_on_compact(memory_dir, monkeypatch):
    write_memory(memory_dir, "xfs_inode_lesson", description="lessons about xfs_inode.c clobber bug")
    _reindex(memory_dir)
    read_payload = {"tool_name": "Read", "tool_input": {"file_path": "/src/mxfs/fs/xfs/xfs_inode.c"}, "session_id": "S1"}

    out1 = _capture(monkeypatch, read_payload)
    assert hooks.inject_handler() == 0
    assert "xfs_inode_lesson" in out1.getvalue()

    out2 = _capture(monkeypatch, read_payload)
    assert hooks.inject_handler() == 0
    assert out2.getvalue() == ""

    _capture(monkeypatch, {"session_id": "S1", "source": "compact"})
    assert hooks.session_handler() == 0

    out3 = _capture(monkeypatch, read_payload)
    assert hooks.inject_handler() == 0
    assert "xfs_inode_lesson" in out3.getvalue()


def test_prune_ledger_removes_old_rows_keeps_fresh(memory_dir):
    with Store(memory_dir) as store:
        store.db.execute(
            "INSERT INTO injection_ledger(session_id, slug, injected_at, tokens) VALUES (?, ?, ?, ?)",
            ("old-session", "old-slug", 1, 10),
        )
        store.db.execute(
            "INSERT INTO injection_ledger(session_id, slug, injected_at, tokens) VALUES (?, ?, unixepoch(), ?)",
            ("new-session", "new-slug", 10),
        )
        removed = store.prune_ledger(30)
        assert removed == 1
        remaining = {r["session_id"] for r in store.db.execute("SELECT session_id FROM injection_ledger")}
        assert remaining == {"new-session"}


def test_claim_injections_is_idempotent_per_slug(memory_dir):
    with Store(memory_dir) as store:
        cand = [{"name": "dup-lesson", "line": "  - [dup-lesson] a lesson description"}]
        first = store.claim_injections("S1", cand, per_read_max=3, session_max=20, token_backstop=4000)
        assert [c["name"] for c in first] == ["dup-lesson"]

        second = store.claim_injections("S1", cand, per_read_max=3, session_max=20, token_backstop=4000)
        assert second == []


def test_handler_dispatch_fail_open_on_unknown():
    rc = hooks.dispatch("nonexistent_handler")
    assert rc == 0
