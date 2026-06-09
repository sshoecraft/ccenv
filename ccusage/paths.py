"""Shared cache-path resolution. Imported by both server.py and statusline.py
so the writer and reader agree on the file location.
"""

import os
from pathlib import Path


def cache_path() -> Path:
    return Path(os.environ.get("TMPDIR", "/tmp")) / f"ccusage-{os.getuid()}.json"
