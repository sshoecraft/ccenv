"""Shared cache-path resolution. Imported by both server.py and statusline.py
so the writer and reader agree on the file location.

The cache is keyed PER SESSION under the XDG state dir
(``$XDG_STATE_HOME/ccusage/<session-id>.json``, default
``~/.local/state/ccusage/``). The previous single per-UID file in ``/tmp``
was clobbered by any concurrent same-UID Claude Code session: whichever
rendered its statusline last owned the file, so every other session's
reader saw a foreign ``session_id`` and silently bailed. A per-session file
removes that race — a reader keyed by its own session id always finds its
own data.
"""

from __future__ import annotations

import os
from pathlib import Path


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "ccusage"


def cache_path(session_id: str | None = None) -> Path:
    """Per-session cache file. Falls back to a per-UID name only when no
    session id is known (the writer always knows it from Claude Code's
    status JSON, so that path is essentially never taken)."""
    name = f"{session_id}.json" if session_id else f"uid-{os.getuid()}.json"
    return state_dir() / name


def latest_cache_path() -> Path | None:
    """The most recently written per-session cache, or ``None``.

    For readers that do not know their own session id (the MCP server runs
    as a subprocess and is not told which session it belongs to). With a
    single active session this is exactly that session; under concurrency it
    is the one whose statusline rendered most recently.
    """
    d = state_dir()
    try:
        files = list(d.glob("*.json"))
    except OSError:
        return None
    if not files:
        return None
    try:
        return max(files, key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
