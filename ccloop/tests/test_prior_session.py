"""The prior-session pointer that replaced the session-maintained handoff.

The contract under test: ccloop locates the previous session's transcript
DETERMINISTICALLY and names it in the prompt, so no session ever has to spend
tokens writing a handoff document about work Claude Code already recorded.
"""

import json
import os

import pytest

from ccloop import runner
from ccloop import transcript as tx


def _transcript(session_id, lines=5, mtime=None):
    path = tx.transcript_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps({"type": "assistant", "i": n}) + "\n" for n in range(lines)),
        encoding="utf-8",
    )
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def run_dir(tmp_path):
    d = tmp_path / "proj" / ".ccloop" / "runs" / "rid-1"
    d.mkdir(parents=True)
    return d


def test_prefers_the_last_session_of_this_run(run_dir, isolated_home):
    _transcript("older")
    latest = _transcript("newest-in-project")
    mine = _transcript("run-session-2")
    (run_dir / "sessions.log").write_text("run-session-1\nrun-session-2\n",
                                          encoding="utf-8")

    path, origin = runner.prior_session_transcript(run_dir)
    assert path == mine
    assert origin == runner.ORIGIN_RUN
    assert path != latest


def test_falls_back_past_a_missing_transcript(run_dir, isolated_home):
    first = _transcript("run-session-1")
    # session 2 ran but its transcript is gone (deleted, or never written)
    (run_dir / "sessions.log").write_text("run-session-1\nrun-session-2\n",
                                          encoding="utf-8")

    path, origin = runner.prior_session_transcript(run_dir)
    assert path == first
    assert origin == runner.ORIGIN_RUN


def test_first_session_uses_the_projects_newest_transcript(run_dir, isolated_home):
    _transcript("stale-session", mtime=1_000_000)
    newest = _transcript("the-session-the-user-was-in", mtime=2_000_000)

    path, origin = runner.prior_session_transcript(run_dir)
    assert path == newest
    assert origin == runner.ORIGIN_PROJECT


def test_project_fallback_never_returns_this_runs_own_sessions(run_dir, isolated_home):
    # sessions.log lists a run session whose transcript is gone. The fallback
    # must not hand back another session of the same run as "background".
    _transcript("run-session-1", mtime=2_000_000)
    background = _transcript("unrelated", mtime=1_000_000)
    (run_dir / "sessions.log").write_text("run-session-1\nrun-session-2\n",
                                          encoding="utf-8")
    tx.transcript_path("run-session-1").unlink()

    path, _ = runner.prior_session_transcript(run_dir)
    assert path == background


def test_empty_transcripts_are_not_offered(run_dir, isolated_home):
    empty = tx.transcript_path("started-and-died")
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_text("", encoding="utf-8")

    path, origin = runner.prior_session_transcript(run_dir)
    assert path is None
    assert origin == ""


def test_no_transcripts_anywhere_yields_no_block(run_dir, isolated_home):
    assert runner.prior_session_transcript(run_dir) == (None, "")
    assert runner.prior_session_block(run_dir) == ""


def test_block_carries_path_size_and_a_tail_offset(run_dir, isolated_home):
    path = _transcript("run-session-1", lines=1200)
    (run_dir / "sessions.log").write_text("run-session-1\n", encoding="utf-8")

    block = runner.prior_session_block(run_dir)
    assert str(path) in block
    assert "1200 lines of JSONL" in block
    # Reading a 1,200-line transcript from the top is how you blow the context
    # you were handed; the block has to aim at the tail.
    assert "offset` around 900" in block
    assert "do NOT need to write a handoff document" in block


def test_block_offset_never_precedes_the_file(run_dir, isolated_home):
    _transcript("run-session-1", lines=3)
    (run_dir / "sessions.log").write_text("run-session-1\n", encoding="utf-8")
    assert "offset` around 1" in runner.prior_session_block(run_dir)


def test_line_count_matches_the_file(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("a\nb\nc\n", encoding="utf-8")
    assert tx.line_count(p) == 3
    assert tx.line_count(tmp_path / "nope.jsonl") is None


def test_wedge_relay_replaces_the_transcript_pointer(tmp_path, monkeypatch):
    """Detection already happened; this is the different action taken on it.
    After a safeguard-flag wedge the transcript is a liability, not a handoff —
    it holds every tool result that was in the flagged request."""
    run_dir = tmp_path / "run"
    (run_dir / "transcripts").mkdir(parents=True)
    tx_file = run_dir / "transcripts" / "session-1.jsonl"
    tx_file.write_text('{"type":"assistant"}\n', encoding="utf-8")
    monkeypatch.setattr(runner, "prior_session_transcript",
                        lambda d: (tx_file, "transcripts"))

    normal = runner.prior_session_block(run_dir)
    wedged = runner.prior_session_block(run_dir, wedged=True)

    assert normal != wedged
    assert "DO NOT read" in wedged
    assert 'subagent_type="miner"' in wedged
    assert str(tx_file) in wedged, "the subagent still needs the path"
    assert "do NOT quote raw command output" in wedged


def test_wedge_relay_is_silent_with_no_prior_transcript(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setattr(runner, "prior_session_transcript", lambda d: (None, None))
    assert runner.prior_session_block(run_dir, wedged=True) == ""


def test_build_prompt_threads_the_wedge_flag(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    resume = run_dir / "resume.md"
    resume.write_text("task body\n", encoding="utf-8")
    monkeypatch.setattr(runner.state, "state_block", lambda *a, **k: "")
    monkeypatch.setattr(runner, "prior_session_block",
                        lambda d, wedged=False: "WEDGED" if wedged else "NORMAL")
    assert "WEDGED" in runner._build_prompt(resume, 2, "r1", wedged=True)
    assert "NORMAL" in runner._build_prompt(resume, 2, "r1")
