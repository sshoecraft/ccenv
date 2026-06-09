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
