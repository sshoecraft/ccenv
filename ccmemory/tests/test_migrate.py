"""Migration: copy + verify + gitignore + refuse-if-non-empty + dry-run."""

import hashlib
from pathlib import Path

import pytest

from ccmemory import migrate as migrate_mod
from tests.conftest import write_memory


def _make_legacy(tmp_path: Path, n: int = 3) -> Path:
    d = tmp_path / "legacy"
    d.mkdir()
    for i in range(n):
        write_memory(d, f"sess{i}_lessons", description=f"session {i}")
    return d


def test_migrate_happy_path(tmp_path):
    src = _make_legacy(tmp_path, n=3)
    dst = tmp_path / ".ccmemory"
    r = migrate_mod.migrate(source=src, target=dst)
    assert r.status == "ok"
    assert r.files_copied == 3
    # All files present
    copied = sorted(p.name for p in dst.glob("*.md"))
    assert copied == ["sess0_lessons.md", "sess1_lessons.md", "sess2_lessons.md"]
    # gitignore + marker created
    assert (dst / ".gitignore").exists()
    assert ".memory_index.db" in (dst / ".gitignore").read_text()
    assert (dst / ".migrated-from").exists()
    assert str(src) in (dst / ".migrated-from").read_text()


def test_migrate_dry_run_changes_nothing(tmp_path):
    src = _make_legacy(tmp_path, n=2)
    dst = tmp_path / ".ccmemory"
    r = migrate_mod.migrate(source=src, target=dst, dry_run=True)
    assert r.status == "ok"
    assert r.files_copied == 2
    assert not dst.exists()


def test_migrate_refuses_if_dest_has_md(tmp_path):
    src = _make_legacy(tmp_path, n=1)
    dst = tmp_path / ".ccmemory"
    dst.mkdir()
    write_memory(dst, "existing")
    r = migrate_mod.migrate(source=src, target=dst)
    assert r.status == "refused"
    # Existing file untouched
    assert (dst / "existing.md").exists()


def test_migrate_overwrite_allowed_with_flag(tmp_path):
    src = _make_legacy(tmp_path, n=1)
    dst = tmp_path / ".ccmemory"
    dst.mkdir()
    write_memory(dst, "stale_existing")
    r = migrate_mod.migrate(source=src, target=dst, overwrite=True)
    assert r.status == "ok"
    assert (dst / "sess0_lessons.md").exists()


def test_migrate_no_source(tmp_path):
    src = tmp_path / "nothere"
    dst = tmp_path / ".ccmemory"
    r = migrate_mod.migrate(source=src, target=dst)
    assert r.status == "no-source"


def test_migrate_empty_source(tmp_path):
    src = tmp_path / "empty"
    src.mkdir()
    dst = tmp_path / ".ccmemory"
    r = migrate_mod.migrate(source=src, target=dst)
    assert r.status == "no-source"


def test_migrate_preserves_source(tmp_path):
    src = _make_legacy(tmp_path, n=2)
    dst = tmp_path / ".ccmemory"
    src_files_before = sorted(p.name for p in src.glob("*.md"))
    migrate_mod.migrate(source=src, target=dst)
    src_files_after = sorted(p.name for p in src.glob("*.md"))
    assert src_files_before == src_files_after


def test_migrate_hashes_match_source(tmp_path):
    src = _make_legacy(tmp_path, n=3)
    dst = tmp_path / ".ccmemory"
    migrate_mod.migrate(source=src, target=dst)
    for p in src.glob("*.md"):
        d = dst / p.name
        with open(p, "rb") as a, open(d, "rb") as b:
            assert hashlib.sha256(a.read()).hexdigest() == hashlib.sha256(b.read()).hexdigest()


def test_automigrate_respects_env_optout(tmp_path, monkeypatch):
    src = _make_legacy(tmp_path, n=1)
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CCMEMORY_NO_AUTOMIGRATE", "1")
    # Even though source exists and project resolves, opt-out skips it
    result = migrate_mod.automigrate_quiet()
    assert result is None
    assert not (tmp_path / ".ccmemory").exists()


def test_automigrate_skips_when_target_populated(tmp_path, monkeypatch):
    src = _make_legacy(tmp_path, n=1)
    (tmp_path / ".git").mkdir()
    dst = tmp_path / ".ccmemory"
    dst.mkdir()
    write_memory(dst, "already_here")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CCMEMORY_NO_AUTOMIGRATE", raising=False)
    # Even if legacy has content, we already have .ccmemory/ — skip
    # (Note: this won't actually find the legacy dir we made; the test
    # is mostly that automigrate doesn't crash and returns None when
    # the target is populated.)
    result = migrate_mod.automigrate_quiet()
    assert result is None
    # existing content preserved
    assert (dst / "already_here.md").exists()
