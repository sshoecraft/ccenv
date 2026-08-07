"""Handoff tier: the session's own words, and the staleness check that makes
including them safe.

The failure this guards against is not "no handoff" — it is a handoff from six
sessions ago being read as the previous session's parting words. A stale file
is byte-identical to a fresh one, so nothing but the mtime check separates
them.
"""

import json
import time

import pytest

from ccloop import handoff, summarize


def _run_dir(tmp_path):
    """<project>/.ccloop/runs/<run-id> — handoff.md sits two levels up."""
    d = tmp_path / "proj" / ".ccloop" / "runs" / "rid"
    d.mkdir(parents=True)
    return d


def _write_handoff(run_dir, text, mtime=None):
    p = handoff.handoff_path(run_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if mtime is not None:
        import os
        os.utime(p, (mtime, mtime))
    return p


def _transcript(tmp_path, text="did some work"):
    t = tmp_path / "sid.jsonl"
    t.write_text(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}],
                    "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0,
                              "cache_read_input_tokens": 0, "output_tokens": 1}},
    }) + "\n", encoding="utf-8")
    return t


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("CCLOOP_HANDOFF_FILE", raising=False)
    monkeypatch.delenv("CCLOOP_HANDOFF_MAX_BYTES", raising=False)


def test_missing_handoff_is_not_a_section(tmp_path):
    d = _run_dir(tmp_path)
    block, fresh = handoff.handoff_block(d, time.time())
    assert block == "" and fresh is False


def test_handoff_written_during_the_session_is_fresh(tmp_path):
    d = _run_dir(tmp_path)
    started = time.time() - 600
    _write_handoff(d, "HANDOFF-SENTINEL", mtime=started + 300)
    block, fresh = handoff.handoff_block(d, started)
    assert fresh is True
    assert "HANDOFF-SENTINEL" in block
    assert "STALE" not in block


def test_handoff_predating_the_session_is_marked_stale(tmp_path):
    d = _run_dir(tmp_path)
    started = time.time()
    _write_handoff(d, "OLD-SENTINEL", mtime=started - 7 * 86400)
    block, fresh = handoff.handoff_block(d, started)
    assert fresh is False
    assert "STALE" in block
    assert "7.0 days before this session started" in block
    # Still shown — it may be the only context there is — but never as current.
    assert "OLD-SENTINEL" in block


def test_freshness_cannot_be_claimed_without_a_start_time(tmp_path):
    # Defaulting the other way would let any caller that forgets to thread the
    # timestamp silently assert currency it never checked.
    d = _run_dir(tmp_path)
    _write_handoff(d, "SENTINEL")
    status, _, _ = handoff.read_handoff(d, session_started=None)
    assert status == "stale"


def test_empty_handoff_is_reported_not_silently_skipped(tmp_path):
    d = _run_dir(tmp_path)
    _write_handoff(d, "   \n  ")
    block, fresh = handoff.handoff_block(d, time.time())
    assert fresh is False
    assert "is empty" in block


def test_handoff_truncation_is_visible(tmp_path, monkeypatch):
    d = _run_dir(tmp_path)
    started = time.time() - 60
    _write_handoff(d, "x" * 5000, mtime=started + 10)
    monkeypatch.setenv("CCLOOP_HANDOFF_MAX_BYTES", "500")
    block, fresh = handoff.handoff_block(d, started)
    assert fresh is True
    assert "truncated at 500 bytes" in block
    assert "CCLOOP_HANDOFF_MAX_BYTES" in block


def test_env_override_relocates_the_file(tmp_path, monkeypatch):
    d = _run_dir(tmp_path)
    alt = tmp_path / "elsewhere.md"
    alt.write_text("ELSEWHERE", encoding="utf-8")
    monkeypatch.setenv("CCLOOP_HANDOFF_FILE", str(alt))
    assert handoff.handoff_path(d) == alt
    block, fresh = handoff.handoff_block(d, time.time() - 60)
    assert fresh is True and "ELSEWHERE" in block


# --- integration with summarize ------------------------------------------


def test_fresh_handoff_replaces_the_scraped_text(tmp_path):
    d = _run_dir(tmp_path)
    started = time.time() - 600
    _write_handoff(d, "HANDOFF-SENTINEL", mtime=started + 300)
    t = _transcript(tmp_path, "SCRAPED-SENTINEL")
    out = summarize.summarize(t, "task", "RID", 2, run_dir=d, session_started=started)
    assert "HANDOFF-SENTINEL" in out
    assert "SCRAPED-SENTINEL" not in out
    assert "## Last text from previous session" not in out


def test_stale_handoff_does_not_replace_the_scraped_text(tmp_path):
    # The whole point of the tier: a stale file is not a substitute, so the
    # fallback has to survive alongside it.
    d = _run_dir(tmp_path)
    started = time.time()
    _write_handoff(d, "OLD-SENTINEL", mtime=started - 5 * 86400)
    t = _transcript(tmp_path, "SCRAPED-SENTINEL")
    out = summarize.summarize(t, "task", "RID", 2, run_dir=d, session_started=started)
    assert "STALE" in out
    assert "SCRAPED-SENTINEL" in out
    assert "## Last text from previous session" in out


def test_crashed_session_still_hands_off_what_it_wrote(tmp_path):
    # The motivating case. Zero assistant text turns — the scraper has nothing
    # — but the handoff the session wrote along the way is on disk.
    d = _run_dir(tmp_path)
    started = time.time() - 600
    _write_handoff(d, "MID-FLIGHT-SENTINEL", mtime=started + 120)
    t = tmp_path / "sid.jsonl"
    t.write_text("", encoding="utf-8")
    out = summarize.summarize(t, "task", "RID", 2, run_dir=d, session_started=started)
    assert "MID-FLIGHT-SENTINEL" in out
    assert "crashed mid-tool" not in out


def test_crashed_session_with_no_handoff_keeps_the_old_marker(tmp_path):
    d = _run_dir(tmp_path)
    t = tmp_path / "sid.jsonl"
    t.write_text("", encoding="utf-8")
    out = summarize.summarize(t, "task", "RID", 2, run_dir=d,
                              session_started=time.time())
    assert "crashed mid-tool" in out


def test_summarize_without_run_dir_falls_back_to_scraping(tmp_path):
    # Callers that don't thread run_dir must keep working, and must NOT get a
    # silently handoff-less document with no fallback text.
    t = _transcript(tmp_path, "SCRAPED-SENTINEL")
    out = summarize.summarize(t, "task", "RID", 2)
    assert "SCRAPED-SENTINEL" in out
    assert "## Last text from previous session" in out
