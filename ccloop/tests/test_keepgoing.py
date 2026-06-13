import io
import json
import os

import pytest

from ccloop import keepgoing


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path, monkeypatch):
    """Empty TMPDIR so usage cache lookups return None unless a test writes one."""
    d = tmp_path / "_tmp_for_cache"
    d.mkdir()
    monkeypatch.setenv("TMPDIR", str(d))


def write_cache(session_id, used_percentage, window=1000000):
    """Drop a ccusage statusline cache entry. The keepgoing cutoff gate
    reads exclusively from this — see keepgoing._signal_halt context."""
    cache = os.path.join(os.environ["TMPDIR"], f"ccusage-{os.getuid()}.json")
    with open(cache, "w") as fh:
        json.dump({"session_id": session_id,
                   "context_window": {"used_percentage": used_percentage,
                                       "context_window_size": window}}, fh)


def run(monkeypatch, stdin_obj):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin_obj)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    rc = keepgoing.main([])
    return rc, out.getvalue()


def test_noop_outside_ccloop(monkeypatch):
    monkeypatch.delenv("CCLOOP_RUN_ID", raising=False)
    rc, out = run(monkeypatch, {})
    assert rc == 0 and out == ""


def test_allows_stop_when_done(monkeypatch, tmp_path):
    resume = tmp_path / "resume.md"
    resume.write_text("DONE\n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    rc, out = run(monkeypatch, {"session_id": "s1"})
    assert rc == 0 and out == ""


def test_blocks_stop_and_refeeds(monkeypatch, tmp_path):
    resume = tmp_path / "resume.md"
    resume.write_text("task body, not converged\n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    rc, out = run(monkeypatch, {"session_id": "s1"})
    payload = json.loads(out)
    assert payload["decision"] == "block"
    assert "DONE" in payload["reason"]
    assert "re-fed #1" in payload["systemMessage"]


def test_counter_increments(monkeypatch, tmp_path):
    resume = tmp_path / "resume.md"
    resume.write_text("body\n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    run(monkeypatch, {"session_id": "s1"})
    rc, out = run(monkeypatch, {"session_id": "s1"})
    assert "re-fed #2" in json.loads(out)["systemMessage"]


def test_cap_allows_stop_after_max(monkeypatch, tmp_path):
    resume = tmp_path / "resume.md"
    resume.write_text("body\n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    monkeypatch.setenv("CCLOOP_MAX_CONTINUES", "2")
    # Two re-feeds, third call should give up and let the model stop.
    run(monkeypatch, {"session_id": "s1"})
    run(monkeypatch, {"session_id": "s1"})
    rc, out = run(monkeypatch, {"session_id": "s1"})
    assert rc == 0 and out == ""


def test_ignores_other_session(monkeypatch, tmp_path):
    resume = tmp_path / "resume.md"
    resume.write_text("body\n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    rc, out = run(monkeypatch, {"session_id": "DIFFERENT"})
    assert rc == 0 and out == ""


def test_missing_resume_file_treated_as_done(monkeypatch, tmp_path):
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(tmp_path / "absent.md"))
    rc, out = run(monkeypatch, {"session_id": "s1"})
    assert rc == 0 and out == ""


def test_done_with_trailing_text(monkeypatch, tmp_path):
    resume = tmp_path / "resume.md"
    resume.write_text("  DONE: everything verified\n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    rc, out = run(monkeypatch, {"session_id": "s1"})
    assert rc == 0 and out == ""


# ── criteria-gate path ────────────────────────────────────────────────


def _setup_criteria(tmp_path, criteria_text="all tests pass with zero errors\n"):
    resume = tmp_path / "resume.md"
    resume.write_text("task body\n")
    (tmp_path / "criteria.md").write_text(criteria_text)
    return resume


def test_criteria_path_done_marker_is_ignored(monkeypatch, tmp_path):
    # With criteria configured, raw DONE in resume.md must NOT allow stop.
    resume = _setup_criteria(tmp_path)
    resume.write_text("DONE\n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    rc, out = run(monkeypatch, {"session_id": "s1"})
    payload = json.loads(out)
    assert payload["decision"] == "block"
    assert "HAVE YOU MET THE CRITERIA" in payload["reason"]
    assert "all tests pass with zero errors" in payload["reason"]
    assert str(tmp_path / "criteria-met") in payload["reason"]


def test_criteria_path_marker_yes_allows_stop(monkeypatch, tmp_path):
    resume = _setup_criteria(tmp_path)
    (tmp_path / "criteria-met").write_text("YES\n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    rc, out = run(monkeypatch, {"session_id": "s1"})
    assert rc == 0 and out == ""


def test_criteria_path_marker_other_content_blocks(monkeypatch, tmp_path):
    # Marker file exists but doesn't say YES — still block.
    resume = _setup_criteria(tmp_path)
    (tmp_path / "criteria-met").write_text("MAYBE\n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    rc, out = run(monkeypatch, {"session_id": "s1"})
    payload = json.loads(out)
    assert payload["decision"] == "block"
    assert "HAVE YOU MET THE CRITERIA" in payload["reason"]


def test_criteria_path_no_marker_blocks(monkeypatch, tmp_path):
    resume = _setup_criteria(tmp_path)
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    rc, out = run(monkeypatch, {"session_id": "s1"})
    payload = json.loads(out)
    assert payload["decision"] == "block"
    assert "HAVE YOU MET THE CRITERIA" in payload["reason"]


def test_empty_criteria_falls_back_to_legacy(monkeypatch, tmp_path):
    # Empty criteria.md = explicit opt-out; raw DONE is enough.
    resume = tmp_path / "resume.md"
    resume.write_text("DONE\n")
    (tmp_path / "criteria.md").write_text("   \n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    rc, out = run(monkeypatch, {"session_id": "s1"})
    assert rc == 0 and out == ""


def test_criteria_path_max_continues_cap(monkeypatch, tmp_path):
    resume = _setup_criteria(tmp_path)
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    monkeypatch.setenv("CCLOOP_MAX_CONTINUES", "2")
    run(monkeypatch, {"session_id": "s1"})
    run(monkeypatch, {"session_id": "s1"})
    rc, out = run(monkeypatch, {"session_id": "s1"})
    assert rc == 0 and out == ""


# ── cutoff gate (cache-only since 0.3.2) ────────────────────────────────


def test_cutoff_allows_stop_and_writes_halt(monkeypatch, tmp_path):
    resume = tmp_path / "resume.md"
    resume.write_text("body\n")
    (tmp_path / "cutoff").write_text("50000\n")
    write_cache("s1", 10, window=1000000)  # 10% of 1M = 100000 ≥ 50000
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    rc, out = run(monkeypatch, {"session_id": "s1"})
    assert rc == 0 and out == ""
    assert (tmp_path / "halt-s1").exists()


def test_below_cutoff_still_refeeds(monkeypatch, tmp_path):
    resume = tmp_path / "resume.md"
    resume.write_text("body\n")
    (tmp_path / "cutoff").write_text("200000\n")
    write_cache("s1", 5, window=1000000)  # 50000 < 200000
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    rc, out = run(monkeypatch, {"session_id": "s1"})
    payload = json.loads(out)
    assert payload["decision"] == "block"
    assert not (tmp_path / "halt-s1").exists()


def test_done_wins_over_cutoff(monkeypatch, tmp_path):
    resume = tmp_path / "resume.md"
    resume.write_text("DONE\n")
    (tmp_path / "cutoff").write_text("50000\n")
    write_cache("s1", 10, window=1000000)  # over cutoff, but DONE wins
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    rc, out = run(monkeypatch, {"session_id": "s1"})
    assert rc == 0 and out == ""
    assert not (tmp_path / "halt-s1").exists()


def test_criteria_yes_wins_over_cutoff(monkeypatch, tmp_path):
    resume = _setup_criteria(tmp_path)
    (tmp_path / "criteria-met").write_text("YES\n")
    (tmp_path / "cutoff").write_text("50000\n")
    write_cache("s1", 10, window=1000000)
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    rc, out = run(monkeypatch, {"session_id": "s1"})
    assert rc == 0 and out == ""
    assert not (tmp_path / "halt-s1").exists()


def test_cutoff_does_not_bump_refeed_counter(monkeypatch, tmp_path):
    """Cutoff-driven allow-stop must skip the keepgoing-<sess>.count bump."""
    resume = tmp_path / "resume.md"
    resume.write_text("body\n")
    (tmp_path / "cutoff").write_text("50000\n")
    write_cache("s1", 10, window=1000000)
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    run(monkeypatch, {"session_id": "s1"})
    assert not (tmp_path / "keepgoing-s1.count").exists()


def test_cache_for_other_session_does_not_fire(monkeypatch, tmp_path):
    """Regression: a concurrent Claude Code session writes its own
    statusline cache to the per-UID file; keepgoing must ignore that
    cache (session_id mismatch) and let the model keep working rather
    than fall back to anything that would over-count and halt at the
    start of every session. This is the 0.3.2 fix."""
    resume = tmp_path / "resume.md"
    resume.write_text("body\n")
    (tmp_path / "cutoff").write_text("50000\n")
    write_cache("DIFFERENT-SESS", 99, window=1000000)  # 990k for that session
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    rc, out = run(monkeypatch, {"session_id": "s1"})
    payload = json.loads(out)
    assert payload["decision"] == "block"
    assert not (tmp_path / "halt-s1").exists()


def test_tiny_cutoff_value_treated_as_typo(monkeypatch, tmp_path):
    """A hand-edited cutoff file containing '160' (meaning thousands)
    must NOT halt every session at low context. Falls back to default."""
    resume = tmp_path / "resume.md"
    resume.write_text("body\n")
    (tmp_path / "cutoff").write_text("160\n")  # raw 160, not 160000
    write_cache("s1", 5, window=1000000)  # 50000 — would halt under cutoff=160
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    rc, out = run(monkeypatch, {"session_id": "s1"})
    payload = json.loads(out)
    assert payload["decision"] == "block"  # refeed, not halt
    assert not (tmp_path / "halt-s1").exists()


def test_no_cache_at_all_does_not_fire(monkeypatch, tmp_path):
    """No ccusage cache → no token data → don't halt. We'd rather
    miss a relay than halt spuriously at session start."""
    resume = tmp_path / "resume.md"
    resume.write_text("body\n")
    (tmp_path / "cutoff").write_text("50000\n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    rc, out = run(monkeypatch, {"session_id": "s1"})
    payload = json.loads(out)
    assert payload["decision"] == "block"
    assert not (tmp_path / "halt-s1").exists()


# ---------------------------------------------------------------------------
# Background-task gate.
#
# When the model stops with a Bash background task still pending (its
# .output file in the session's /tmp/claude-<uid>/<slug>/<sid>/tasks/ dir),
# the keepgoing nudge ("you stopped without signaling completion, pick a
# new angle") is the wrong response — the model is correctly waiting. The
# Stop hook returns silently: no decision, no systemMessage, no re-feed.
# The harness wakes the model when the task completes; the next Stop after
# the file is reaped falls through to the normal keepgoing logic.
#
# Detection is by file presence, not a liveness check (no lsof, no
# subprocess). A "task done but not yet reaped" momentary false positive
# costs at most one Stop cycle of delayed keepgoing — invisible — and a
# real liveness check would cost real CPU on every Stop on every machine.
# ---------------------------------------------------------------------------

def test_pending_background_blocks_with_wait_message(monkeypatch, tmp_path):
    """With background pending, the hook must BLOCK (not return 0). Returning
    0 would let ccloop's runner relay-and-lose the task."""
    resume = tmp_path / "resume.md"
    resume.write_text("body\n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    monkeypatch.setattr(keepgoing, "_pending_background_task_count", lambda _s: 2)
    rc, out = run(monkeypatch, {"session_id": "s1"})
    payload = json.loads(out)
    assert rc == 0
    assert payload["decision"] == "block"
    assert "ccloop wait" in payload["systemMessage"]
    assert "2 background command" in payload["systemMessage"]


def test_no_pending_background_runs_keepgoing(monkeypatch, tmp_path):
    """With nothing pending, the normal keepgoing CONTINUE_MSG fires as before."""
    resume = tmp_path / "resume.md"
    resume.write_text("body\n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    monkeypatch.setattr(keepgoing, "_pending_background_task_count", lambda _s: 0)
    rc, out = run(monkeypatch, {"session_id": "s1"})
    payload = json.loads(out)
    assert payload["decision"] == "block"
    assert "DONE" in payload["reason"]
    assert "re-fed #1" in payload["systemMessage"]


def test_pending_background_does_not_bump_keepgoing_counter(monkeypatch, tmp_path):
    """Two wait re-feeds followed by a real keepgoing cycle should re-feed
    CONTINUE_MSG as #1, not #3 — wait re-feeds must not consume the cap."""
    resume = tmp_path / "resume.md"
    resume.write_text("body\n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))

    # Phase 1: two wait re-feeds while background is pending.
    monkeypatch.setattr(keepgoing, "_pending_background_task_count", lambda _s: 1)
    run(monkeypatch, {"session_id": "s1"})
    run(monkeypatch, {"session_id": "s1"})

    # Phase 2: nothing pending — first keepgoing re-feed must be #1.
    monkeypatch.setattr(keepgoing, "_pending_background_task_count", lambda _s: 0)
    rc, out = run(monkeypatch, {"session_id": "s1"})
    assert "re-fed #1" in json.loads(out)["systemMessage"]


def test_done_wins_over_pending_background(monkeypatch, tmp_path):
    """An honest DONE/YES must end the run cleanly even if a background task
    file is still present — the model decided, trust the model."""
    resume = tmp_path / "resume.md"
    resume.write_text("DONE\n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    monkeypatch.setattr(keepgoing, "_pending_background_task_count", lambda _s: 1)
    rc, out = run(monkeypatch, {"session_id": "s1"})
    assert rc == 0 and out == ""


def test_cutoff_wins_over_pending_background(monkeypatch, tmp_path):
    """CRITICAL ordering test: when the cutoff is hit AND a background task
    is pending, the cutoff MUST win — write the halt sentinel and allow the
    stop so ccloop can relay. The original bug was that the wait gate ran
    BEFORE the cutoff gate, so the session hung at 270k+/250k tokens with
    stale .output files lying around."""
    resume = tmp_path / "resume.md"
    resume.write_text("body\n")
    (tmp_path / "cutoff").write_text("50000\n")
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    monkeypatch.setenv("CCLOOP_SESSION_ID", "s1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(resume))
    write_cache("s1", used_percentage=99)   # well over the 50000 cutoff
    monkeypatch.setattr(keepgoing, "_pending_background_task_count", lambda _s: 3)
    rc, out = run(monkeypatch, {"session_id": "s1"})
    assert rc == 0
    assert out == ""                          # cutoff allows stop, no JSON
    assert (tmp_path / "halt-s1").exists()    # halt sentinel was written


def test_pending_background_task_count_real_glob(tmp_path, monkeypatch):
    """End-to-end: simulate a Claude Code tasks dir and confirm the count
    matches the number of .output files (no liveness check, no subprocess)."""
    sess = "deadbeef-1234-5678-9abc-def012345678"
    tasks = tmp_path / f"claude-{os.getuid()}" / "-fake-slug" / sess / "tasks"
    tasks.mkdir(parents=True)

    # Redirect the /tmp/claude-* glob root under tmp_path.
    import glob as _glob
    real_glob = _glob.glob
    def patched(pattern, *a, **kw):
        if pattern.startswith(f"/tmp/claude-{os.getuid()}/"):
            redirect = pattern.replace("/tmp/", str(tmp_path) + "/", 1)
            return real_glob(redirect, *a, **kw)
        return real_glob(pattern, *a, **kw)
    monkeypatch.setattr(keepgoing.glob, "glob", patched)

    # No .output -> 0
    assert keepgoing._pending_background_task_count(sess) == 0

    # Drop two .output -> 2
    (tasks / "abc.output").write_text("hello\n")
    (tasks / "def.output").write_text("world\n")
    assert keepgoing._pending_background_task_count(sess) == 2

    # Unrelated file is ignored
    (tasks / "abc.notoutput").write_text("nope\n")
    assert keepgoing._pending_background_task_count(sess) == 2
