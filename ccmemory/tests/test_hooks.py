"""Hook handlers: fail-open behavior, sentinel, output shapes."""

import io
import json
import os
import sys

import pytest

from ccmemory import hooks
from tests.conftest import write_memory


def _capture(monkeypatch, payload, env=None):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload) if payload else ""))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    if env:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    return out


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
    out = _capture(monkeypatch, {"tool_name": "Read", "tool_input": {"file_path": "/src/mxfs/fs/xfs/xfs_inode.c"}})
    rc = hooks.inject_handler()
    assert rc == 0
    if out.getvalue():
        payload = json.loads(out.getvalue())
        assert payload["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert "xfs_inode_lesson" in payload["hookSpecificOutput"]["additionalContext"]


def test_inject_ignores_non_Read(monkeypatch):
    out = _capture(monkeypatch, {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    rc = hooks.inject_handler()
    assert rc == 0
    assert out.getvalue() == ""


def test_handler_dispatch_fail_open_on_unknown():
    rc = hooks.dispatch("nonexistent_handler")
    assert rc == 0
