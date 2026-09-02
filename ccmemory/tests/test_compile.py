"""Tests for memory compaction backlog detection + status (no LLM, no claude -p)."""

import os
import time

import pytest

from ccmemory import compile as compile_mod
from .conftest import write_memory


def test_threshold_default_and_env(monkeypatch):
    monkeypatch.delenv("CCMEMORY_COMPILE_THRESHOLD", raising=False)
    assert compile_mod.threshold() == compile_mod.DEFAULT_THRESHOLD
    monkeypatch.setenv("CCMEMORY_COMPILE_THRESHOLD", "5")
    assert compile_mod.threshold() == 5
    monkeypatch.setenv("CCMEMORY_COMPILE_THRESHOLD", "garbage")
    assert compile_mod.threshold() == compile_mod.DEFAULT_THRESHOLD


def test_compilable_and_always_listed_types_are_complementary():
    # The drift that broke mxfs: _select ingested only 'project' while
    # count_backlog counted every type, so 'reference' was in neither the
    # compilable set nor the exempt set. It could not be retired from a
    # listing and it could not be drained from the backlog — 144 permanently
    # stuck memories against a threshold of 20. Any new type must land in
    # exactly one of these tuples.
    from ccmemory.store import Store

    known = {"user", "feedback", "project", "reference"}
    assert set(Store.ALWAYS_LIST_TYPES).isdisjoint(compile_mod.COMPILABLE_TYPES)
    assert set(Store.ALWAYS_LIST_TYPES) | set(compile_mod.COMPILABLE_TYPES) == known


def test_backlog_counts_only_what_a_compile_pass_can_act_on(memory_dir):
    # An unsilenceable alarm is a broken alarm. Types _select will never ingest
    # must not be counted, or the backlog has a floor above the threshold and
    # the nudge fires forever no matter how much compaction runs.
    write_memory(memory_dir, "pref", type="user")
    for i in range(3):
        write_memory(memory_dir, f"corrected{i}", type="feedback")
    write_memory(memory_dir, "note", type="project")
    write_memory(memory_dir, "fact", type="reference")

    b = compile_mod.count_backlog(memory_dir)
    assert b["backlog"] == 2, "only the project + reference notes are actionable"
    assert b["total_raw"] == 2

    # And compacting those two drives it to zero — the floor is reachable.
    write_memory(memory_dir, "compiled-topic", body="[[note]] [[fact]]")
    assert compile_mod.count_backlog(memory_dir)["backlog"] == 0


def test_select_offers_reference_notes_as_candidates(memory_dir):
    write_memory(memory_dir, "note", type="project")
    write_memory(memory_dir, "fact", type="reference")
    write_memory(memory_dir, "pref", type="user")
    picks = compile_mod._select(memory_dir, topic=None, max_inputs=10)
    assert {p["name"] for p in picks} == {"note", "fact"}


def test_backlog_all_raw_when_no_compiled(memory_dir):
    for i in range(3):
        write_memory(memory_dir, f"note{i}")
    b = compile_mod.count_backlog(memory_dir)
    assert b["backlog"] == 3
    assert b["total_raw"] == 3
    assert b["has_compiled"] is False


def test_compiled_articles_excluded_from_raw(memory_dir):
    write_memory(memory_dir, "note-a", mtime=100)
    write_memory(memory_dir, "note-b", mtime=200)
    # Citing both inputs is what clears the backlog.
    write_memory(memory_dir, "compiled-topic", body="[[note-a]] [[note-b]]", mtime=300)
    b = compile_mod.count_backlog(memory_dir)
    assert b["total_raw"] == 2          # compiled-* is not raw
    assert b["has_compiled"] is True
    assert b["backlog"] == 0


def test_backlog_counts_uncited_notes_regardless_of_mtime(memory_dir):
    """The mtime heuristic this replaced assumed a compile pass covers
    everything older than itself. It doesn't — on a real 1,695-memory store it
    reported 249 while 431 notes had never been cited by any article, so 182
    were invisible to the nudge forever. Citation is the exact signal."""
    write_memory(memory_dir, "old-but-never-folded", mtime=100)
    write_memory(memory_dir, "old-and-folded", mtime=100)
    write_memory(memory_dir, "compiled-topic", body="[[old-and-folded]]", mtime=200)
    write_memory(memory_dir, "new-note", mtime=300)
    b = compile_mod.count_backlog(memory_dir)
    # Under the old rule this was 1 (only new-note). Both uncited notes count.
    assert b["backlog"] == 2
    assert b["total_raw"] == 3


def test_backlog_quiets_down_once_everything_is_cited(memory_dir):
    write_memory(memory_dir, "a")
    write_memory(memory_dir, "b")
    assert compile_mod.count_backlog(memory_dir)["backlog"] == 2
    write_memory(memory_dir, "compiled-topic", body="folded [[a]] and [[b]]")
    assert compile_mod.count_backlog(memory_dir)["backlog"] == 0


def test_select_prefers_never_cited_candidates(memory_dir):
    now = time.time()
    write_memory(memory_dir, "already-folded", mtime=now - 1)
    write_memory(memory_dir, "never-folded", mtime=now - 100 * 86400)
    write_memory(memory_dir, "compiled-topic", body="[[already-folded]]", mtime=now)
    status = compile_mod.compile_status(memory_dir, max_inputs=5)
    # Newest-first would have picked already-folded; recompiling it would add an
    # article without retiring anything.
    assert "never-folded" in status["candidate_names"]
    assert "already-folded" not in status["candidate_names"]


def test_memory_md_and_appledouble_ignored(memory_dir):
    write_memory(memory_dir, "real")
    (memory_dir / "MEMORY.md").write_text("generated index\n", encoding="utf-8")
    (memory_dir / "._sidecar.md").write_text("junk\n", encoding="utf-8")
    b = compile_mod.count_backlog(memory_dir)
    assert b["total_raw"] == 1


def test_compile_status_reports_candidates_and_over_threshold(memory_dir, monkeypatch):
    monkeypatch.setenv("CCMEMORY_COMPILE_THRESHOLD", "2")
    for i in range(3):
        write_memory(memory_dir, f"note{i}")
    status = compile_mod.compile_status(memory_dir)
    assert status["status"] == "ok"
    assert status["over_threshold"] is True
    assert status["candidate_count"] == 3
    assert set(status["candidate_names"]) == {"note0", "note1", "note2"}
    assert "compile-memories skill" in status["how"]


def test_compile_status_excludes_compiled_from_candidates(memory_dir):
    write_memory(memory_dir, "note0")
    write_memory(memory_dir, "compiled-prior")
    status = compile_mod.compile_status(memory_dir)
    assert "compiled-prior" not in status["candidate_names"]


def test_no_claude_bin_resolver_remains():
    # The claude -p machinery must be gone entirely.
    assert not hasattr(compile_mod, "_resolve_claude_bin")
    assert not hasattr(compile_mod, "compile_directory")


def test_cooldown_suppresses_nudge_after_a_recent_compile(memory_dir, monkeypatch):
    """Several concurrent sessions must not all dispatch a compactor for the
    same notes. A compile pass on a large store may not push the backlog under
    the threshold, so backlog alone cannot be the only gate."""
    monkeypatch.setenv("CCMEMORY_COMPILE_THRESHOLD", "3")
    monkeypatch.setenv("CCMEMORY_COMPILE_COOLDOWN", "900")
    for i in range(5):
        write_memory(memory_dir, f"note{i}")

    b = compile_mod.count_backlog(memory_dir)
    assert b["backlog"] >= b["threshold"]
    assert compile_mod.nudge_suppressed(b) is False

    # A compiled article that cites nothing: backlog is unchanged, but a pass
    # just ran, so the nudge must go quiet for the cooldown window.
    write_memory(memory_dir, "compiled-topic", body="no citations here")
    b = compile_mod.count_backlog(memory_dir)
    assert b["backlog"] >= b["threshold"]
    assert b["since_compiled"] is not None and b["since_compiled"] < 900
    assert compile_mod.nudge_suppressed(b) is True


def test_cooldown_can_be_disabled(memory_dir, monkeypatch):
    monkeypatch.setenv("CCMEMORY_COMPILE_THRESHOLD", "3")
    monkeypatch.setenv("CCMEMORY_COMPILE_COOLDOWN", "0")
    for i in range(5):
        write_memory(memory_dir, f"note{i}")
    write_memory(memory_dir, "compiled-topic", body="no citations here")
    b = compile_mod.count_backlog(memory_dir)
    assert compile_mod.nudge_suppressed(b) is False


def test_since_compiled_is_none_with_no_articles(memory_dir, monkeypatch):
    monkeypatch.setenv("CCMEMORY_COMPILE_THRESHOLD", "3")
    for i in range(5):
        write_memory(memory_dir, f"note{i}")
    b = compile_mod.count_backlog(memory_dir)
    assert b["since_compiled"] is None
    assert compile_mod.nudge_suppressed(b) is False


def test_backlog_under_threshold_still_suppresses(memory_dir, monkeypatch):
    monkeypatch.setenv("CCMEMORY_COMPILE_THRESHOLD", "50")
    for i in range(5):
        write_memory(memory_dir, f"note{i}")
    b = compile_mod.count_backlog(memory_dir)
    assert compile_mod.nudge_suppressed(b) is True
