"""Read the exact Claude Code context usage from the ccusage statusline cache.

Claude Code's statusline (ccusage) writes the raw status JSON it receives to
a PER-SESSION cache file every turn:
``$XDG_STATE_HOME/ccusage/<session-id>.json`` (default
``~/.local/state/ccusage/``). That JSON carries the exact
``context_window.used_percentage`` Claude Code itself computes, the real
``context_window_size``, and the ``session_id`` it belongs to.

The cache used to be a single per-UID file in ``/tmp``, which any concurrent
same-UID session clobbered — so a reader routinely saw a foreign
``session_id`` and silently skipped. The per-session file removes that race:
each session reads its OWN file. A legacy ``/tmp/ccusage-<uid>.json`` is still
read as a fallback so a session already in flight across the upgrade keeps
working.

This drives the *early* relay cutoff only. The hard guarantee that a full
context relays (rather than wedging on the wall) does NOT depend on this
cache — it comes from ``transcript.hit_context_wall``.
"""

import json
import os
from pathlib import Path


def _state_dir():
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "ccusage"


def cache_path(session_id=None):
    """Per-session cache file under the XDG state dir."""
    name = f"{session_id}.json" if session_id else f"uid-{os.getuid()}.json"
    return _state_dir() / name


def _legacy_cache_path():
    """Old single per-UID file in /tmp (pre per-session cache)."""
    return Path(os.environ.get("TMPDIR", "/tmp")) / f"ccusage-{os.getuid()}.json"


def _load(path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_cache(session_id=None):
    """This session's cache JSON, or None.

    Prefers the per-session XDG file; falls back to the legacy per-UID
    ``/tmp`` file (used only while a pre-upgrade statusline is still writing
    there). The legacy file is trusted only when it actually belongs to
    ``session_id`` — exactly the old guard, so it can't leak a foreign
    session's numbers.
    """
    cache = _load(cache_path(session_id))
    if cache is not None:
        return cache
    legacy = _load(_legacy_cache_path())
    if legacy is not None and (session_id is None or legacy.get("session_id") == session_id):
        return legacy
    return None


def exact_pct(session_id, cache=None):
    """Exact used_percentage, but only when the cache is for ``session_id``."""
    cache = read_cache(session_id) if cache is None else cache
    if not cache or cache.get("session_id") != session_id:
        return None
    return (cache.get("context_window") or {}).get("used_percentage")


def exact_tokens(session_id, cache=None):
    """Exact used token count for ``session_id``, or None.

    Derived from ``used_percentage * context_window_size / 100`` when the
    cache belongs to this session. Returns None when the cache is missing,
    is for a different session, or lacks either field.
    """
    cache = read_cache(session_id) if cache is None else cache
    if not cache or cache.get("session_id") != session_id:
        return None
    cw = cache.get("context_window") or {}
    pct = cw.get("used_percentage")
    size = cw.get("context_window_size")
    if pct is None or not size:
        return None
    return int(float(pct) * float(size) / 100.0)


def window_size(session_id=None, cache=None):
    """Real context window size from this session's cache, or None."""
    cache = read_cache(session_id) if cache is None else cache
    if not cache:
        return None
    return (cache.get("context_window") or {}).get("context_window_size")
