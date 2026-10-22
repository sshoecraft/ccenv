"""Store: reindex, search, ranking, get."""

import sqlite3
import time

from ccmemory.store import Store
from tests.conftest import write_memory


def test_unopenable_index_is_discarded_and_rebuilt(memory_dir, monkeypatch):
    # A WAL-mode index on a filesystem with no shared-memory support cannot be
    # opened at all, read or write — sqlite answers "unable to open database
    # file" to every statement. That is what /src (NFS) does to an index
    # written anywhere else, and it took every ccmemory tool in every project
    # on the volume down silently: the hooks fail open, so a session with
    # unreachable memory looks exactly like a session with no memory.
    write_memory(memory_dir, "note", description="a lesson worth keeping")
    with Store(memory_dir) as s:
        s.reindex()
    assert (memory_dir / "index.db").exists()

    real_connect = sqlite3.connect
    calls = []

    def refuse_once(*args, **kwargs):
        calls.append(args)
        conn = real_connect(*args, **kwargs)
        if len(calls) == 1:
            conn.close()
            raise sqlite3.OperationalError("unable to open database file")
        return conn

    monkeypatch.setattr(sqlite3, "connect", refuse_once)
    with Store(memory_dir) as s:
        # Rebuilt from the markdown without being asked to reindex: the index
        # is derived, so throwing it away costs one rebuild and nothing else.
        names = {r["name"] for r in s.list_all()[0]}
    assert names == {"note"}
    assert len(calls) == 2, "one failed open, one reopen on a fresh file"


def test_reindex_empty_dir(memory_dir):
    with Store(memory_dir) as s:
        changed, removed, total = s.reindex()
    assert (changed, removed, total) == (0, 0, 0)


def test_reindex_and_get(memory_dir):
    write_memory(memory_dir, "foo", description="something about XFS")
    with Store(memory_dir) as s:
        changed, removed, total = s.reindex()
        assert (changed, removed, total) == (1, 0, 1)
        m = s.get("foo")
    assert m is not None
    assert m.name == "foo"
    assert m.type == "project"


def test_reindex_skip_unchanged(memory_dir):
    write_memory(memory_dir, "foo")
    with Store(memory_dir) as s:
        s.reindex()
        changed, _, _ = s.reindex()
    assert changed == 0


def test_reindex_drops_removed_files(memory_dir):
    p = write_memory(memory_dir, "foo")
    with Store(memory_dir) as s:
        s.reindex()
        p.unlink()
        _, removed, total = s.reindex()
    assert removed == 1
    assert total == 0


def test_search_finds_by_description(memory_dir):
    write_memory(memory_dir, "foo", description="something about XFS double-free")
    write_memory(memory_dir, "bar", description="unrelated memory")
    with Store(memory_dir) as s:
        s.reindex()
        results = s.search("XFS double-free")
    assert results
    assert results[0]["name"] == "foo"


def test_search_recency_boosts_recent(memory_dir):
    now = time.time()
    write_memory(memory_dir, "old", description="bnobt clobber bug", mtime=now - 60 * 86400)
    write_memory(memory_dir, "new", description="bnobt clobber bug", mtime=now - 1)
    with Store(memory_dir) as s:
        s.reindex()
        results = s.search("bnobt clobber")
    assert results[0]["name"] == "new"


def test_excludes_MEMORY_md_from_index(memory_dir):
    (memory_dir / "MEMORY.md").write_text("---\nname: index\ndescription: x\n---\nx\n")
    write_memory(memory_dir, "foo")
    with Store(memory_dir) as s:
        _, _, total = s.reindex()
    assert total == 1  # MEMORY.md excluded


def test_list_all_returns_every_memory_newest_first(memory_dir):
    now = time.time()
    write_memory(memory_dir, "old", type="feedback", mtime=now - 30 * 86400)
    write_memory(memory_dir, "new", type="project", mtime=now - 1)
    write_memory(memory_dir, "mid", type="reference", mtime=now - 5 * 86400)
    with Store(memory_dir) as s:
        s.reindex()
        results, counts = s.list_all()
    assert [r["name"] for r in results] == ["new", "mid", "old"]
    assert all("age_days" in r and "type" in r for r in results)
    assert counts == {"total": 3, "shown": 3, "folded": 0, "withheld": 0,
                      "load_bearing_withheld": 0}


def test_list_all_omits_path(memory_dir):
    # path was 43% of the payload on a large store and unusable — memory_get
    # keys on name.
    write_memory(memory_dir, "foo")
    with Store(memory_dir) as s:
        s.reindex()
        results, _ = s.list_all()
    assert "path" not in results[0]


def test_list_all_type_filter(memory_dir):
    write_memory(memory_dir, "fb1", type="feedback")
    write_memory(memory_dir, "fb2", type="feedback")
    write_memory(memory_dir, "ref1", type="reference")
    with Store(memory_dir) as s:
        s.reindex()
        results, _ = s.list_all(type_filter="feedback")
    assert {r["name"] for r in results} == {"fb1", "fb2"}
    assert all(r["type"] == "feedback" for r in results)


def test_compiled_article_folds_the_notes_it_cites(memory_dir):
    write_memory(memory_dir, "raw-a")
    write_memory(memory_dir, "raw-b")
    write_memory(memory_dir, "raw-uncited")
    write_memory(memory_dir, "compiled-topic", body="see [[raw-a]] and [[raw-b]]")
    with Store(memory_dir) as s:
        s.reindex()
        assert s.folded_names() == {"raw-a", "raw-b"}
        results, counts = s.list_all()
    names = {r["name"] for r in results}
    assert names == {"raw-uncited", "compiled-topic"}
    assert counts["total"] == 4 and counts["folded"] == 2 and counts["withheld"] == 0


def test_include_folded_returns_everything(memory_dir):
    write_memory(memory_dir, "raw-a")
    write_memory(memory_dir, "compiled-topic", body="see [[raw-a]]")
    with Store(memory_dir) as s:
        s.reindex()
        results, counts = s.list_all(include_folded=True)
    assert {r["name"] for r in results} == {"raw-a", "compiled-topic"}
    assert counts["folded"] == 0 and counts["shown"] == 2


def test_load_bearing_types_are_never_folded(memory_dir):
    # Behavior and preference memories are what the session-start listing
    # exists to surface; a compiled article citing one must not retire it.
    # reference is deliberately NOT in this set (see ALWAYS_LIST_TYPES): it is
    # a durable fact, search reaches it, and pinning it is what grew mxfs's
    # listing to 160 unretirable entries.
    write_memory(memory_dir, "pref", type="user")
    write_memory(memory_dir, "corrected", type="feedback")
    write_memory(memory_dir, "fact", type="reference")
    write_memory(memory_dir, "note", type="project")
    write_memory(memory_dir, "compiled-topic",
                 body="[[pref]] [[corrected]] [[fact]] [[note]]")
    with Store(memory_dir) as s:
        s.reindex()
        assert s.folded_names() == {"note", "fact"}
        results, _ = s.list_all()
    names = {r["name"] for r in results}
    assert {"pref", "corrected"} <= names
    assert "fact" not in names


def test_reference_is_foldable_so_compaction_can_retire_it(memory_dir):
    # The 0.19.0 fix, stated directly: an uncited reference note lists, and
    # citing it in an article retires it from the listing. Before this, no
    # sequence of operations could ever remove a reference note from a listing.
    write_memory(memory_dir, "fact-a", type="reference")
    write_memory(memory_dir, "fact-b", type="reference")
    with Store(memory_dir) as s:
        s.reindex()
        results, _ = s.list_all()
        assert {"fact-a", "fact-b"} <= {r["name"] for r in results}
    write_memory(memory_dir, "compiled-facts", body="[[fact-a]]")
    with Store(memory_dir) as s:
        s.reindex()
        results, counts = s.list_all()
    names = {r["name"] for r in results}
    assert "fact-a" not in names
    assert {"fact-b", "compiled-facts"} <= names
    assert counts["folded"] == 1


def test_untyped_memories_are_never_folded(memory_dir):
    p = memory_dir / "untyped.md"
    p.write_text("---\nname: untyped\ndescription: no type set\n---\n\nbody\n",
                 encoding="utf-8")
    write_memory(memory_dir, "compiled-topic", body="[[untyped]]")
    with Store(memory_dir) as s:
        s.reindex()
        assert s.folded_names() == set()
        results, _ = s.list_all()
    assert "untyped" in {r["name"] for r in results}


def test_behavior_note_folds_only_into_an_always_listed_article(memory_dir):
    # Folding a correction into a `type: project` article would swap an entry
    # with first claim on the budget for a representative that can be trimmed
    # out of the listing — the note retires and nothing stands in for it.
    write_memory(memory_dir, "corrected", type="feedback")
    write_memory(memory_dir, "compiled-wrong-tier", type="project",
                 body="[[corrected]]")
    with Store(memory_dir) as s:
        s.reindex()
        assert s.folded_names() == set()
        results, _ = s.list_all()
        assert "corrected" in {r["name"] for r in results}

    write_memory(memory_dir, "compiled-right-tier", type="feedback",
                 body="[[corrected]]")
    with Store(memory_dir) as s:
        s.reindex()
        assert s.folded_names() == {"corrected"}
        results, _ = s.list_all()
        names = {r["name"] for r in results}
        assert "corrected" not in names
        assert "compiled-right-tier" in names


def test_budget_gives_load_bearing_types_first_claim(memory_dir):
    now = time.time()
    write_memory(memory_dir, "pref", type="user", mtime=now - 900 * 86400)
    write_memory(memory_dir, "fb", type="feedback", mtime=now - 900 * 86400)
    for i in range(40):
        write_memory(memory_dir, f"proj{i:02d}", type="project",
                     description="d" * 120, mtime=now - i * 86400)
    with Store(memory_dir) as s:
        s.reindex()
        results, counts = s.list_all(token_budget=400)
    names = {r["name"] for r in results}
    # Oldest memories in the store, kept anyway because of their tier.
    assert {"pref", "fb"} <= names
    assert counts["withheld"] > 0
    assert counts["load_bearing_withheld"] == 0
    assert counts["shown"] + counts["folded"] + counts["withheld"] == counts["total"]
    # What survived of project is the newest of them.
    assert "proj00" in names and "proj39" not in names


def test_budget_is_a_real_ceiling_even_for_load_bearing_types(memory_dir):
    # The defect this release exists to fix. Pre-0.19.0 the always-listed types
    # were charged against `spent` but never trimmed, so a store with enough of
    # them blew the budget without limit AND starved every other tier. mxfs
    # shipped 14,921 tokens against a 6,000 budget with zero project notes.
    now = time.time()
    for i in range(200):
        write_memory(memory_dir, f"fb{i:03d}", type="feedback",
                     description="d" * 140, mtime=now - i * 86400)
    for i in range(20):
        write_memory(memory_dir, f"proj{i:02d}", type="project",
                     description="d" * 140, mtime=now - i * 86400)
    budget = 2000
    with Store(memory_dir) as s:
        s.reindex()
        results, counts = s.list_all(token_budget=budget)
        spent = sum(s._entry_tokens(e) for e in results)
    assert spent <= budget, "budget must bind on every tier, including the first"
    assert counts["load_bearing_withheld"] > 0, "over-large tier 1 must be trimmed"
    # And the starvation half: other tiers still get their share.
    assert any(r["type"] == "project" for r in results), \
        "tier 1 must not be able to consume the whole budget"


def test_type_filter_gets_the_whole_budget(memory_dir):
    # `load_bearing_withheld` tells the caller to re-list with type="feedback".
    # That advice was a no-op: the tier shares are cumulative and donate
    # DOWNWARD only, so with tiers 2 and 3 empty the filtered call was still
    # capped at tier 1's 25% and returned the same truncated set. On a real
    # 331-memory store it withheld 34 of 56 feedback memories twice in a row.
    now = time.time()
    for i in range(60):
        write_memory(memory_dir, f"fb{i:03d}", type="feedback",
                     description="d" * 140, mtime=now - i * 86400)
    for i in range(20):
        write_memory(memory_dir, f"proj{i:02d}", type="project",
                     description="d" * 140, mtime=now - i * 86400)
    budget = 3000
    with Store(memory_dir) as s:
        s.reindex()
        mixed, mixed_counts = s.list_all(token_budget=budget)
        filtered, counts = s.list_all(type_filter="feedback", token_budget=budget)
        spent = sum(s._entry_tokens(e) for e in filtered)
    in_mixed = sum(1 for e in mixed if e["type"] == "feedback")
    assert len(filtered) > in_mixed, "the filtered call must recover entries"
    assert counts["load_bearing_withheld"] < mixed_counts["load_bearing_withheld"]
    assert spent <= budget, "and must still respect the ceiling"


def test_compiled_articles_get_their_own_budget_tier(memory_dir):
    # Ranked purely by mtime, 2 of mxfs's 132 articles made the listing while
    # the 1,494 notes they replaced were folded away — the session saw neither.
    now = time.time()
    for i in range(30):
        write_memory(memory_dir, f"compiled-t{i:02d}", type="project",
                     description="d" * 140, mtime=now - (500 + i) * 86400)
    for i in range(60):
        write_memory(memory_dir, f"proj{i:02d}", type="project",
                     description="d" * 140, mtime=now - i * 86400)
    with Store(memory_dir) as s:
        s.reindex()
        results, _ = s.list_all(token_budget=1500)
    articles = [r for r in results if r["name"].startswith("compiled-")]
    raw = [r for r in results if not r["name"].startswith("compiled-")]
    # Articles are the OLDEST entries here, so mtime order alone would drop all
    # of them. The tier is what keeps them.
    assert articles, "compiled articles must not be starved by newer raw notes"
    assert raw, "the article tier must not consume the whole budget either"


def test_unused_tier_share_spills_downward(memory_dir):
    # Cumulative caps: a store with no user/feedback and no articles must still
    # be able to spend the entire budget on raw notes.
    now = time.time()
    for i in range(60):
        write_memory(memory_dir, f"proj{i:02d}", type="project",
                     description="d" * 140, mtime=now - i * 86400)
    budget = 1500
    with Store(memory_dir) as s:
        s.reindex()
        results, _ = s.list_all(token_budget=budget)
        spent = sum(s._entry_tokens(e) for e in results)
    assert spent > budget * 0.9, \
        f"tier 3 stranded the empty tiers' shares: spent {spent} of {budget}"


def test_unspent_budget_returns_to_withheld_tiers(memory_dir):
    # The mxfs shape after full compaction: nothing raw left, a large tier 1 of
    # feedback articles and a small tier 2. The shares only donated downward,
    # so tier 1 stayed capped at 25% while the rest of the budget went unused:
    # 49 feedback articles withheld from a store that listed for 10,107 tokens
    # of a 19,700-token budget.
    now = time.time()
    for i in range(120):
        write_memory(memory_dir, f"compiled-fb{i:03d}", type="feedback",
                     description="d" * 140, mtime=now - i * 86400)
    for i in range(27):
        write_memory(memory_dir, f"compiled-p{i:02d}", type="project",
                     description="d" * 140, mtime=now - i * 86400)
    budget = 10000
    with Store(memory_dir) as s:
        s.reindex()
        unbounded, _ = s.list_all()
        whole = sum(s._entry_tokens(e) for e in unbounded)
        results, counts = s.list_all(token_budget=budget)
        spent = sum(s._entry_tokens(e) for e in results)
    assert budget * 0.25 < whole <= budget, "fixture must overflow tier 1's share only"
    assert counts["withheld"] == 0 and counts["load_bearing_withheld"] == 0, \
        f"withheld {counts['withheld']} with {budget - spent} tokens unspent"
    assert spent <= budget


def test_entry_tokens_tracks_real_wire_size(memory_dir):
    # ENTRY_ENVELOPE_CHARS must model what mcp_server actually serializes. The
    # old constant (30) modelled name+description only and under-counted by
    # 1.42x, so the budget admitted far more than it believed and memory_stats
    # reported a number that was never the real cost.
    import json

    now = time.time()
    for i in range(40):
        write_memory(memory_dir, f"proj{i:02d}", type="project",
                     description="d" * 140, mtime=now - i * 86400)
    with Store(memory_dir) as s:
        s.reindex()
        results, _ = s.list_all()
        estimated = sum(s._entry_tokens(e) for e in results)
    actual = -(-len(json.dumps(results, separators=(",", ":"))) // 4)
    assert 0.9 <= estimated / actual <= 1.1, \
        f"estimator drifted from the wire format: {estimated} vs {actual}"


def test_budget_zero_is_unbounded(memory_dir):
    for i in range(30):
        write_memory(memory_dir, f"proj{i:02d}", description="d" * 200)
    with Store(memory_dir) as s:
        s.reindex()
        results, counts = s.list_all(token_budget=0)
    assert counts["withheld"] == 0 and len(results) == 30


def test_limit_caps_entries(memory_dir):
    now = time.time()
    for i in range(10):
        write_memory(memory_dir, f"proj{i:02d}", mtime=now - i * 86400)
    with Store(memory_dir) as s:
        s.reindex()
        results, counts = s.list_all(limit=4)
    assert counts["shown"] == 4 and counts["withheld"] == 6


def test_compiled_citing_a_missing_slug_folds_nothing(memory_dir):
    # Dangling wikilinks exist in the wild (mxfs had 5). They must not
    # phantom-fold or crash the join.
    write_memory(memory_dir, "raw-a")
    write_memory(memory_dir, "compiled-topic", body="[[does-not-exist]]")
    with Store(memory_dir) as s:
        s.reindex()
        assert s.folded_names() == set()
        results, counts = s.list_all()
    assert counts["folded"] == 0 and len(results) == 2


def test_compiled_articles_do_not_fold_each_other(memory_dir):
    write_memory(memory_dir, "compiled-a", body="see [[compiled-b]]")
    write_memory(memory_dir, "compiled-b", body="body")
    with Store(memory_dir) as s:
        s.reindex()
        assert s.folded_names() == set()


def test_reindex_skips_appledouble_sidecars(memory_dir):
    write_memory(memory_dir, "real", description="a real one")
    # macOS AppleDouble sidecar the FS materializes next to real.md on
    # xattr-less volumes — must not be indexed as a (null-type) memory.
    (memory_dir / "._real.md").write_text("garbage sidecar content")
    with Store(memory_dir) as s:
        changed, removed, total = s.reindex(force=True)
        rows = [r["name"] for r in s.db.execute("SELECT name FROM mem ORDER BY name")]
    assert total == 1
    assert rows == ["real"]


def test_reindex_refreshes_a_row_whose_path_changed(memory_dir):
    # A store shared over NFS is mounted at different paths on different
    # hosts. Rows first indexed on macOS kept /System/Volumes/Data/... on
    # Linux because the content never changed, and memory_get failed on them.
    write_memory(memory_dir, "note", body="unchanged")
    with Store(memory_dir) as s:
        s.reindex()
        s.db.execute("UPDATE mem SET path = ? WHERE name = ?",
                     ("/System/Volumes/Data/elsewhere/note.md", "note"))
        s.reindex()
        assert s.get("note") is not None
        row = s.db.execute("SELECT path FROM mem WHERE name = 'note'").fetchone()
    assert row["path"] == str(memory_dir / "note.md")


def test_delete_folded_removes_note_and_unlinks_citations(memory_dir):
    write_memory(memory_dir, "source", body="the fact")
    write_memory(memory_dir, "other", body="see [[source]] and [[source|alias]]")
    write_memory(memory_dir, "compiled-topic", body="the fact [[source]]")
    with Store(memory_dir) as s:
        result = s.delete_folded("source")
        names = {r["name"] for r in s.db.execute("SELECT name FROM mem")}
        edges = s.db.execute(
            "SELECT COUNT(*) FROM mem_edges WHERE dst_name = 'source'").fetchone()[0]
    assert not (memory_dir / "source.md").exists()
    assert names == {"other", "compiled-topic"}
    assert edges == 0
    assert sorted(result["relinked"]) == ["compiled-topic.md", "other.md"]
    article = (memory_dir / "compiled-topic.md").read_text()
    assert "`source`" in article and "[[source]]" not in article
    assert "[[" not in (memory_dir / "other.md").read_text()


def test_delete_folded_refuses_what_no_article_represents(memory_dir):
    import pytest
    write_memory(memory_dir, "uncited")
    write_memory(memory_dir, "corrected", type="feedback")
    # A behavior note cited only by a project article is not folded: that
    # article cannot stand in for it in the first listing tier.
    write_memory(memory_dir, "compiled-wrong-tier", type="project", body="[[corrected]]")
    with Store(memory_dir) as s:
        for name in ("uncited", "corrected", "compiled-wrong-tier"):
            with pytest.raises(ValueError, match="refused"):
                s.delete_folded(name)
    for name in ("uncited", "corrected", "compiled-wrong-tier"):
        assert (memory_dir / f"{name}.md").exists()


def content_hash(s: Store, name: str) -> str:
    return s.db.execute("SELECT content_hash FROM mem WHERE name = ?", (name,)).fetchone()[0]


def test_article_goes_only_as_the_absorbed_half_of_a_merge(memory_dir):
    import pytest
    write_memory(memory_dir, "compiled-absorbed", type="feedback", body="the lesson")
    write_memory(memory_dir, "compiled-alone", type="feedback", body="unrelated")
    write_memory(memory_dir, "compiled-kept", type="feedback",
                 body="the lesson [[compiled-absorbed]]")
    with Store(memory_dir) as s:
        s.reindex()
        h = content_hash(s, "compiled-absorbed")
        with pytest.raises(ValueError, match="expect_hash"):
            s.delete_folded("compiled-absorbed")
        with pytest.raises(ValueError, match="changed after the merge read it"):
            s.delete_folded("compiled-absorbed", expect_hash="0" * 64)
        # Cited by nothing: no article holds what it says.
        with pytest.raises(ValueError, match="refused"):
            s.delete_folded("compiled-alone", expect_hash=content_hash(s, "compiled-alone"))
        result = s.delete_folded("compiled-absorbed", expect_hash=h)
    assert result["relinked"] == ["compiled-kept.md"]
    assert not (memory_dir / "compiled-absorbed.md").exists()
    assert "`compiled-absorbed`" in (memory_dir / "compiled-kept.md").read_text()
    assert (memory_dir / "compiled-alone.md").exists()


def test_article_rewritten_after_the_merge_read_it_is_kept(memory_dir):
    # Two compactors: one merges compiled-b into compiled-a, while another has
    # just grown compiled-b in place. Deleting compiled-b on the stale read
    # would destroy everything the second compactor folded into it.
    import pytest
    write_memory(memory_dir, "compiled-b", type="feedback", body="original")
    with Store(memory_dir) as s:
        s.reindex()
        read = content_hash(s, "compiled-b")
    write_memory(memory_dir, "compiled-b", type="feedback", body="original plus a backfill")
    write_memory(memory_dir, "compiled-a", type="feedback", body="original [[compiled-b]]")
    with Store(memory_dir) as s:
        with pytest.raises(ValueError, match="changed after the merge read it"):
            s.delete_folded("compiled-b", expect_hash=read)
    assert (memory_dir / "compiled-b.md").exists()


def test_behavior_article_does_not_merge_into_a_project_article(memory_dir):
    # Same retirement rule as a raw note, one level up: a project article can
    # be trimmed from the listing, so it cannot stand in for a feedback one.
    import pytest
    write_memory(memory_dir, "compiled-fb", type="feedback", body="a correction")
    write_memory(memory_dir, "compiled-pj", type="project", body="[[compiled-fb]]")
    with Store(memory_dir) as s:
        s.reindex()
        with pytest.raises(ValueError, match="refused"):
            s.delete_folded("compiled-fb", expect_hash=content_hash(s, "compiled-fb"))
    assert (memory_dir / "compiled-fb.md").exists()
