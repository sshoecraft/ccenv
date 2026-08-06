"""Store: reindex, search, ranking, get."""

import time

from ccmemory.store import Store
from tests.conftest import write_memory


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
    assert counts == {"total": 3, "shown": 3, "folded": 0, "withheld": 0}


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
    # Behavior/preference/reference memories are what the session-start listing
    # exists to surface; a compiled article citing one must not retire it. Same
    # exemption as budget trimming — one rule, not two.
    write_memory(memory_dir, "pref", type="user")
    write_memory(memory_dir, "corrected", type="feedback")
    write_memory(memory_dir, "fact", type="reference")
    write_memory(memory_dir, "note", type="project")
    write_memory(memory_dir, "compiled-topic",
                 body="[[pref]] [[corrected]] [[fact]] [[note]]")
    with Store(memory_dir) as s:
        s.reindex()
        assert s.folded_names() == {"note"}
        results, _ = s.list_all()
    assert {"pref", "corrected", "fact"} <= {r["name"] for r in results}


def test_untyped_memories_are_never_folded_or_trimmed(memory_dir):
    p = memory_dir / "untyped.md"
    p.write_text("---\nname: untyped\ndescription: no type set\n---\n\nbody\n",
                 encoding="utf-8")
    write_memory(memory_dir, "compiled-topic", body="[[untyped]]")
    with Store(memory_dir) as s:
        s.reindex()
        assert s.folded_names() == set()
        results, _ = s.list_all(token_budget=1)
    assert "untyped" in {r["name"] for r in results}


def test_budget_trims_project_but_never_load_bearing_types(memory_dir):
    now = time.time()
    write_memory(memory_dir, "pref", type="user", mtime=now - 900 * 86400)
    write_memory(memory_dir, "fb", type="feedback", mtime=now - 900 * 86400)
    write_memory(memory_dir, "ref", type="reference", mtime=now - 900 * 86400)
    for i in range(40):
        write_memory(memory_dir, f"proj{i:02d}", type="project",
                     description="d" * 120, mtime=now - i * 86400)
    with Store(memory_dir) as s:
        s.reindex()
        results, counts = s.list_all(token_budget=400)
    names = {r["name"] for r in results}
    # Oldest memories in the store, kept anyway because of their type.
    assert {"pref", "fb", "ref"} <= names
    assert counts["withheld"] > 0
    assert counts["shown"] + counts["folded"] + counts["withheld"] == counts["total"]
    # What survived of project is the newest of them.
    assert "proj00" in names and "proj39" not in names


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
