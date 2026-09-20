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


def test_every_type_can_be_drained():
    # A type nothing can retire grows without limit. 'reference' was that type
    # until 0.19.0 (144 permanently stuck memories against a threshold of 20);
    # 'user'/'feedback' were that type until 0.20.0, one tier up, which is how
    # a store reached 56 feedback memories against a tier that holds ~30 and
    # reported load_bearing_withheld on every listing forever.
    from ccmemory.store import Store

    known = {"user", "feedback", "project", "reference"}
    assert set(compile_mod.COMPILABLE_TYPES) == known
    # And the safety rule that makes folding them sound: a behavior group's
    # article is itself always-listed, so the representative cannot be trimmed
    # out of the tier the notes it retires were pinned to.
    assert Store._is_always_listed(
        compile_mod.POOL_ARTICLE_TYPE[compile_mod.BEHAVIOR_POOL])
    assert not Store._is_always_listed(
        compile_mod.POOL_ARTICLE_TYPE[compile_mod.KNOWLEDGE_POOL])


def test_backlog_counts_every_type_and_reaches_zero(memory_dir):
    # An unsilenceable alarm is a broken alarm: the backlog must be drainable
    # to zero from inside the system, whatever types the store holds.
    write_memory(memory_dir, "pref", type="user")
    for i in range(3):
        write_memory(memory_dir, f"corrected{i}", type="feedback")
    write_memory(memory_dir, "note", type="project")
    write_memory(memory_dir, "fact", type="reference")

    b = compile_mod.count_backlog(memory_dir)
    assert b["backlog"] == 6
    assert b["total_raw"] == 6

    write_memory(memory_dir, "compiled-topic", body="[[note]] [[fact]]")
    write_memory(memory_dir, "compiled-behavior", type="feedback",
                 body="[[pref]] " + " ".join(f"[[corrected{i}]]" for i in range(3)))
    assert compile_mod.count_backlog(memory_dir)["backlog"] == 0


def test_behavior_note_stays_pending_until_a_feedback_article_cites_it(memory_dir):
    # Citation alone is not retirement for a behavior note: a `type: project`
    # article sits in a tier that can be budget-trimmed, so the note would be
    # dropped from the listing with nothing of its own tier standing in for it.
    write_memory(memory_dir, "corrected", type="feedback")
    write_memory(memory_dir, "compiled-wrong-tier", type="project",
                 body="[[corrected]]")
    assert compile_mod.count_backlog(memory_dir)["backlog"] == 1

    write_memory(memory_dir, "compiled-right-tier", type="feedback",
                 body="[[corrected]]")
    assert compile_mod.count_backlog(memory_dir)["backlog"] == 0


def test_select_offers_every_compilable_type_as_a_candidate(memory_dir):
    write_memory(memory_dir, "note", type="project")
    write_memory(memory_dir, "fact", type="reference")
    write_memory(memory_dir, "pref", type="user")
    picks = compile_mod._select(memory_dir, topic=None, max_inputs=10)
    assert {p["name"] for p in picks} == {"note", "fact", "pref"}


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


# --- compaction_plan: the partition that makes fan-out possible --------------


def plan_names(plan) -> list[str]:
    return [n for g in plan["groups"] for n in g["names"]]


def test_plan_covers_the_whole_backlog_exactly_once(memory_dir):
    """Coverage and disjointness are the two properties the fan-out rests on.

    A note in no group can never be cited, never retires from the listing and
    never leaves the backlog — that is the permanent floor COMPILABLE_TYPES
    documents. A note in two groups means two concurrent compactors write two
    articles about it, which is what forced the groups to be split by hand.
    """
    for i in range(25):
        write_memory(memory_dir, f"note{i}", body=f"body text {i}")
    plan = compile_mod.compaction_plan(memory_dir)
    names = plan_names(plan)
    assert plan["backlog"] == 25
    assert sorted(names) == sorted(f"note{i}" for i in range(25))
    assert len(names) == len(set(names)), "groups must be disjoint"


def test_plan_skips_cited_notes(memory_dir):
    write_memory(memory_dir, "folded")
    write_memory(memory_dir, "pending")
    write_memory(memory_dir, "pref", type="user")
    write_memory(memory_dir, "corrected", type="feedback")
    write_memory(memory_dir, "compiled-prior", body="[[folded]]")
    plan = compile_mod.compaction_plan(memory_dir)
    assert sorted(plan_names(plan)) == ["corrected", "pending", "pref"]


def test_plan_never_mixes_the_two_pools(memory_dir):
    # A group names the article's type, and an article has exactly one. A group
    # spanning both pools would have to mis-file one half of it.
    for i in range(6):
        write_memory(memory_dir, f"correction{i}", type="feedback")
        write_memory(memory_dir, f"note{i}", type="project")
    plan = compile_mod.compaction_plan(memory_dir)
    assert plan["groups"], "both pools must be planned, not just one"
    for g in plan["groups"]:
        kinds = {n.startswith("correction") for n in g["names"]}
        assert len(kinds) == 1, f"group {g['seed']} mixes pools: {g['names']}"
        behavior = kinds == {True}
        assert g["pool"] == (compile_mod.BEHAVIOR_POOL if behavior
                             else compile_mod.KNOWLEDGE_POOL)
        assert g["article_type"] == ("feedback" if behavior else "project")
    planned = plan_names(plan)
    assert len(planned) == 12 and len(set(planned)) == 12


def test_plan_groups_notes_by_subject(memory_dir):
    """A topic group must actually be a topic.

    Note the corpus size: BM25 weights a term by how rare it is, and a term in
    half the documents carries an IDF of zero. On a four-document-per-subject
    toy store nothing discriminates and every group comes out `assorted` — the
    clustering needs a realistic corpus to have any signal at all, which is
    why this fixture is 40 notes and not 8.
    """
    subjects = {
        "xfs": "xfs allocator agno bitmap extent inode",
        "unit": "systemd socket activation dependency ordering",
        "quota": "quota weekly bucket metered credit billing",
        "hooks": "pretooluse hook settings dispatch matcher",
    }
    for subject, vocab in subjects.items():
        for i in range(10):
            text = f"{vocab} detail{subject}{i}"
            write_memory(memory_dir, f"{subject}-case{i}", description=text, body=text)

    plan = compile_mod.compaction_plan(memory_dir)
    topic_groups = [g for g in plan["groups"] if g["kind"] == "topic"]
    assert topic_groups, "a four-subject store must yield topic groups"
    for g in topic_groups:
        assert len({n.split("-case")[0] for n in g["names"]}) == 1, \
            f"subjects must not be mixed: {g['names']}"


def test_plan_pools_unclusterable_notes_into_assorted_groups(memory_dir):
    # Nothing here shares vocabulary, so nothing clusters. Every note must
    # still be dispatched: refusing to compile the awkward ones is exactly how
    # a backlog acquires a floor it can never get under.
    subjects = ["quantum", "asparagus", "bicycle", "tungsten", "monsoon"]
    for s in subjects:
        write_memory(memory_dir, f"note-{s}", description=s, body=s)
    plan = compile_mod.compaction_plan(memory_dir)
    assert sorted(plan_names(plan)) == sorted(f"note-{s}" for s in subjects)
    assert all(g["kind"] == "assorted" for g in plan["groups"])


def test_plan_respects_group_size(memory_dir):
    body = "identical shared vocabulary across every single one of these notes"
    for i in range(30):
        write_memory(memory_dir, f"note{i}", description=body, body=body)
    plan = compile_mod.compaction_plan(memory_dir, size=5)
    assert plan["group_size"] == 5
    assert all(g["size"] <= 5 for g in plan["groups"])
    assert len(plan_names(plan)) == 30


def test_plan_seed_addressing_returns_one_group(memory_dir):
    for i in range(10):
        write_memory(memory_dir, f"note{i}", body=f"body {i}")
    full = compile_mod.compaction_plan(memory_dir)
    seed = full["groups"][0]["seed"]
    one = compile_mod.compaction_plan(memory_dir, seed=seed)
    assert one["status"] == "ok"
    assert len(one["groups"]) == 1
    assert one["groups"][0]["names"] == full["groups"][0]["names"]
    assert one["group_count"] == full["group_count"]


def test_plan_reports_done_for_an_already_compiled_seed(memory_dir):
    """The agent must be told to stop, not to go find other work: picking a
    different group is how two agents end up on the same notes."""
    write_memory(memory_dir, "note0")
    plan = compile_mod.compaction_plan(memory_dir, seed="never-existed")
    assert plan["status"] == "done"
    assert plan["groups"] == []
    assert "Do NOT pick a different group" in plan["how"]


def test_plan_on_an_empty_backlog(memory_dir):
    write_memory(memory_dir, "folded")
    write_memory(memory_dir, "compiled-prior", body="[[folded]]")
    plan = compile_mod.compaction_plan(memory_dir)
    assert plan["backlog"] == 0
    assert plan["groups"] == []
    assert plan["group_count"] == 0


def test_plan_size_and_wave_env_overrides(monkeypatch):
    monkeypatch.delenv("CCMEMORY_COMPILE_GROUP_SIZE", raising=False)
    monkeypatch.delenv("CCMEMORY_COMPILE_WAVE", raising=False)
    assert compile_mod.plan_group_size() == compile_mod.DEFAULT_GROUP_SIZE
    assert compile_mod.plan_wave_size() == compile_mod.DEFAULT_WAVE
    monkeypatch.setenv("CCMEMORY_COMPILE_GROUP_SIZE", "4")
    monkeypatch.setenv("CCMEMORY_COMPILE_WAVE", "2")
    assert compile_mod.plan_group_size() == 4
    assert compile_mod.plan_wave_size() == 2
    monkeypatch.setenv("CCMEMORY_COMPILE_GROUP_SIZE", "garbage")
    assert compile_mod.plan_group_size() == compile_mod.DEFAULT_GROUP_SIZE
