import shutil
import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture
def memory_dir(tmp_path):
    """Empty memory dir with one or more sample .md files written by the test."""
    return tmp_path


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
