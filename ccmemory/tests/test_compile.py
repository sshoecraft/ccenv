"""Tests for memory compaction backlog detection + status (no LLM, no claude -p)."""

import os

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
    # A compiled article newer than the raw notes clears the backlog.
    write_memory(memory_dir, "compiled-topic", mtime=300)
    b = compile_mod.count_backlog(memory_dir)
    assert b["total_raw"] == 2          # compiled-* is not raw
    assert b["has_compiled"] is True
    assert b["backlog"] == 0            # both raw notes predate the compiled article


def test_backlog_counts_only_notes_newer_than_compiled(memory_dir):
    write_memory(memory_dir, "old-note", mtime=100)
    write_memory(memory_dir, "compiled-topic", mtime=200)
    write_memory(memory_dir, "new-note", mtime=300)  # added after last compile
    b = compile_mod.count_backlog(memory_dir)
    assert b["backlog"] == 1           # only new-note is unfolded


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
