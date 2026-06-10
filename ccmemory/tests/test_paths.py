"""Path resolution: project root + memory dir."""

from pathlib import Path

from ccmemory import paths


def test_project_root_from_git(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "a" / "b" / "c"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert paths.project_root() == tmp_path


def test_project_root_from_pyproject(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    sub = tmp_path / "src"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert paths.project_root() == tmp_path


def test_project_root_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CCMEMORY_PROJECT_ROOT", str(tmp_path))
    monkeypatch.chdir("/tmp")
    assert paths.project_root() == tmp_path.resolve()


def test_project_memory_dir(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    assert paths.project_memory_dir() == tmp_path / ".ccmemory"


def test_resolve_prefers_project_local(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".ccmemory").mkdir()
    (tmp_path / ".ccmemory" / "x.md").write_text("---\nname: x\n---\nbody\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CCMEMORY_DIR", raising=False)
    assert paths.resolve_memory_dir() == tmp_path / ".ccmemory"


def test_resolve_env_overrides_all(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".ccmemory").mkdir()
    override = tmp_path / "override"
    override.mkdir()
    monkeypatch.setenv("CCMEMORY_DIR", str(override))
    monkeypatch.chdir(tmp_path)
    assert paths.resolve_memory_dir() == override


def test_resolve_must_exist_false_returns_future_path(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CCMEMORY_DIR", raising=False)
    # .ccmemory/ doesn't exist yet — should still return the path
    assert paths.resolve_memory_dir(must_exist=False) == tmp_path / ".ccmemory"
    assert paths.resolve_memory_dir(must_exist=True) is None


def test_ensure_gitignore_creates_file(tmp_path):
    d = tmp_path / ".ccmemory"
    d.mkdir()
    assert paths.ensure_gitignore(d) is True
    text = (d / ".gitignore").read_text()
    assert "index.db" in text
    assert "._*" in text
    assert ".DS_Store" in text
    # Idempotent: second call is a no-op (returns False, content unchanged).
    assert paths.ensure_gitignore(d) is False
    assert (d / ".gitignore").read_text() == text


def test_ensure_gitignore_appends_missing_patterns(tmp_path):
    d = tmp_path / ".ccmemory"
    d.mkdir()
    # Simulate an older/foreign .gitignore lacking ._* and the new index name.
    (d / ".gitignore").write_text("# mine\n.memory_index.db\n")
    assert paths.ensure_gitignore(d) is True
    text = (d / ".gitignore").read_text()
    assert "# mine" in text          # user content preserved
    assert "index.db" in text         # new index name added
    assert "._*" in text              # sidecar coverage added
