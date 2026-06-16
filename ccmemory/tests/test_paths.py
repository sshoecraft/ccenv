"""Path resolution: startup dir (CWD) + memory dir. No env vars, no walk-up."""

from ccmemory import paths


def test_startup_dir_is_cwd_with_no_markers(tmp_path, monkeypatch):
    # No .git, no build files — exactly a ccloop run dir. Resolves to the
    # directory CC was started in.
    monkeypatch.chdir(tmp_path)
    assert paths.startup_dir() == tmp_path.resolve()


def test_startup_dir_never_walks_up(tmp_path, monkeypatch):
    # A parent with .git (and build markers) must NOT capture a subdirectory
    # session: memory stays local to the exact dir CC was started in.
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    sub = tmp_path / "a" / "b" / "c"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert paths.startup_dir() == sub.resolve()


def test_startup_dir_ignores_env_vars(tmp_path, monkeypatch):
    # Regression guard: ccmemory must NEVER relocate the store via env vars.
    # The old override knobs are dead — setting them changes nothing.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("CCMEMORY_PROJECT_ROOT", str(elsewhere))
    monkeypatch.setenv("CCMEMORY_DIR", str(elsewhere))
    monkeypatch.chdir(tmp_path)
    assert paths.startup_dir() == tmp_path.resolve()
    assert paths.resolve_memory_dir(must_exist=False) == tmp_path / ".ccmemory"


def test_startup_memory_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert paths.startup_memory_dir() == tmp_path / ".ccmemory"


def test_resolve_prefers_startup_local(tmp_path, monkeypatch):
    (tmp_path / ".ccmemory").mkdir()
    (tmp_path / ".ccmemory" / "x.md").write_text("---\nname: x\n---\nbody\n")
    monkeypatch.chdir(tmp_path)
    assert paths.resolve_memory_dir() == tmp_path / ".ccmemory"


def test_resolve_must_exist_false_returns_future_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # .ccmemory/ doesn't exist yet — should still return the path to create.
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
