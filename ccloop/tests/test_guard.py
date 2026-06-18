import io
import json

import pytest

from ccloop import guard


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path, monkeypatch):
    """Point the ccusage cache lookups (per-session XDG + legacy /tmp) at
    empty temp dirs so a reader can't pick up a real home-dir file."""
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    (tmp_path / "state" / "ccusage").mkdir(parents=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def write_transcript(path, tokens):
    path.write_text(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "x"}],
                     "usage": {"input_tokens": tokens,
                               "cache_creation_input_tokens": 0,
                               "cache_read_input_tokens": 0}},
    }) + "\n", encoding="utf-8")


def write_cache(session_id, used_percentage, window=1000000):
    import os
    d = os.path.join(os.environ["XDG_STATE_HOME"], "ccusage")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{session_id}.json"), "w") as fh:
        json.dump({"session_id": session_id,
                   "context_window": {"used_percentage": used_percentage,
                                       "context_window_size": window}}, fh)


def setup_run(tmp_path, cutoff_tokens):
    """Create a minimal run dir with cutoff file and resume.md."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "cutoff").write_text(f"{cutoff_tokens}\n")
    resume = run_dir / "resume.md"
    resume.write_text("task body\n")
    return run_dir, resume


def run_guard(monkeypatch, stdin_obj):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin_obj)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    rc = guard.main([])
    return rc, out.getvalue()


def test_noop_without_run_id(monkeypatch):
    monkeypatch.delenv("CCLOOP_RUN_ID", raising=False)
    rc, out = run_guard(monkeypatch, {})
    assert rc == 0 and out == ""


def test_fires_on_exact_token_count_from_cache(monkeypatch, tmp_path):
    run_dir, resume = setup_run(tmp_path, cutoff_tokens=100000)
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "sess-A")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    # 50% of 200000 = 100000 tokens → at cutoff, fires
    write_cache("sess-A", 50, window=200000)
    rc, out = run_guard(monkeypatch, {})
    payload = json.loads(out)
    msg = payload["hookSpecificOutput"]["additionalContext"]
    assert "relay boundary" in msg
    # The wrap-up must NOT volunteer numeric token counts or a cutoff value —
    # the model conflates them with its real context window and starts
    # mis-reporting usage ("50k/160k") in later turns.
    assert "100000" not in msg
    assert "160000" not in msg


def test_silent_below_cutoff(monkeypatch, tmp_path):
    run_dir, resume = setup_run(tmp_path, cutoff_tokens=200000)
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "sess-A")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    # 50% of 200000 = 100000 tokens, well under 200000 cutoff
    write_cache("sess-A", 50, window=200000)
    rc, out = run_guard(monkeypatch, {})
    assert rc == 0 and out == ""


def test_ignores_cache_from_other_session(monkeypatch, tmp_path):
    run_dir, resume = setup_run(tmp_path, cutoff_tokens=100000)
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "sess-A")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    write_cache("sess-OTHER", 99, window=200000)
    rc, out = run_guard(monkeypatch, {})
    assert rc == 0 and out == ""


def test_tiny_cutoff_value_treated_as_typo(monkeypatch, tmp_path):
    """A hand-edited cutoff file containing '160' (meaning thousands)
    must NOT fire on every tool call. The floor falls back to default."""
    run_dir, resume = setup_run(tmp_path, cutoff_tokens=160)  # raw 160, not 160000
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "sess-A")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    write_cache("sess-A", 5, window=1000000)  # 50000 tokens — would fire under 160
    rc, out = run_guard(monkeypatch, {})
    assert rc == 0 and out == ""  # default in effect, 50000 well under


def test_cutoff_zero_means_no_cutoff(monkeypatch, tmp_path):
    """A cutoff of 0 disables the gate: the wrap-up never fires, even when
    usage is far above any positive cutoff."""
    run_dir, resume = setup_run(tmp_path, cutoff_tokens=0)
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "sess-A")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    write_cache("sess-A", 99, window=1000000)  # 990000 tokens — would fire under any positive cutoff
    rc, out = run_guard(monkeypatch, {})
    assert rc == 0 and out == ""  # disabled — no wrap-up


def test_no_cache_silent(monkeypatch, tmp_path):
    """No ccusage cache → no token data → silent. The transcript
    fallback was removed in 0.3.2: per-turn API token sums over-count
    Opus 4.8 / 1 M context and would halt at the start of every session."""
    run_dir, resume = setup_run(tmp_path, cutoff_tokens=50000)
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "sess-A")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    t = tmp_path / "t.jsonl"
    write_transcript(t, 999999)  # would have fired under the old fallback
    rc, out = run_guard(monkeypatch, {"transcript_path": str(t)})
    assert rc == 0 and out == ""


def test_missing_cutoff_file_uses_default(monkeypatch, tmp_path):
    """No cutoff file in run dir → default 250000 (still cache-driven)."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    resume = run_dir / "resume.md"
    resume.write_text("task\n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "sess-A")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    # 26% of 1M = 260000 → over default 250000
    write_cache("sess-A", 26, window=1000000)
    rc, out = run_guard(monkeypatch, {})
    assert json.loads(out)["hookSpecificOutput"]["additionalContext"]


def test_handles_empty_stdin(monkeypatch):
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert guard.main([]) == 0
    assert out.getvalue() == ""
