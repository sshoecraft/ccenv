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
