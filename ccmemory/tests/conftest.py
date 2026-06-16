import shutil
import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture
def memory_dir(tmp_path, monkeypatch):
    """The ``.ccmemory/`` store for the directory Claude Code was 'started in'.

    Resolution is CWD-only (no env vars), so we chdir into a startup dir and
    hand back its ``.ccmemory/``. Code under test (hooks, MCP server, store)
    resolves to exactly this path with no further setup. Tests write sample
    .md files into it.
    """
    startup_dir = tmp_path
    monkeypatch.chdir(startup_dir)
    store = startup_dir / ".ccmemory"
    store.mkdir()
    return store


def write_memory(memory_dir: Path, name: str, *, type: str = "project",
                 description: str = "test memory", body: str = "body text",
                 tags=None, mtime: float | None = None) -> Path:
    front = [
        "---",
        f"name: {name}",
        f"description: {description}",
        "metadata:",
        f"  type: {type}",
    ]
    if tags:
        front.append("tags: [" + ", ".join(tags) + "]")
    front.append("---")
    path = memory_dir / f"{name}.md"
    path.write_text("\n".join(front) + "\n\n" + body + "\n", encoding="utf-8")
    if mtime is not None:
        import os
        os.utime(path, (mtime, mtime))
    return path
