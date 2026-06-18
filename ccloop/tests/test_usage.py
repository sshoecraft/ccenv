import json
import os
from pathlib import Path

import pytest

from ccloop import usage


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path, monkeypatch):
    # Isolate BOTH the new per-session XDG cache and the legacy /tmp cache so
    # a reader can never pick up a real file from the developer's home dir.
    state = tmp_path / "state"
    (state / "ccusage").mkdir(parents=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp))


def _xdg_dir():
    return Path(os.environ["XDG_STATE_HOME"]) / "ccusage"


def write_cache(session_id, pct, window=1000000):
    """Per-session XDG cache — the primary path the statusline now writes."""
    d = _xdg_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{session_id}.json").write_text(json.dumps({
        "session_id": session_id,
        "context_window": {"used_percentage": pct, "context_window_size": window},
    }))


def write_legacy_cache(session_id, pct, window=1000000):
    """Old single per-UID /tmp cache, read only as a transition fallback."""
    p = Path(os.environ["TMPDIR"]) / f"ccusage-{os.getuid()}.json"
    p.write_text(json.dumps({
        "session_id": session_id,
        "context_window": {"used_percentage": pct, "context_window_size": window},
    }))


def test_no_cache_returns_none():
    assert usage.read_cache("anything") is None
    assert usage.exact_pct("anything") is None
    assert usage.window_size("anything") is None


def test_exact_pct_matches_session():
    write_cache("S1", 42)
    assert usage.exact_pct("S1") == 42


def test_exact_pct_rejects_other_session():
    write_cache("S1", 42)
    assert usage.exact_pct("S2") is None


def test_window_size():
    write_cache("S1", 42, window=200000)
    assert usage.window_size("S1") == 200000


def test_corrupt_cache_safe():
    (_xdg_dir() / "S1.json").write_text("{not json")
    assert usage.read_cache("S1") is None
    assert usage.exact_pct("S1") is None
    assert usage.exact_tokens("S1") is None


def test_exact_tokens_matches_session():
    write_cache("S1", 25, window=200000)  # 25% of 200k → 50k
    assert usage.exact_tokens("S1") == 50000


def test_exact_tokens_rejects_other_session():
    write_cache("S1", 25, window=200000)
    assert usage.exact_tokens("S2") is None


def test_exact_tokens_missing_fields():
    (_xdg_dir() / "S1.json").write_text(
        json.dumps({"session_id": "S1", "context_window": {}}))
    assert usage.exact_tokens("S1") is None


def test_per_session_files_isolated():
    """Two concurrent sessions' caches don't collide — the old per-UID
    clobber that made readers see a foreign session_id is gone."""
    write_cache("S1", 10, window=1000000)
    write_cache("S2", 90, window=1000000)
    assert usage.exact_tokens("S1") == 100000
    assert usage.exact_tokens("S2") == 900000


def test_legacy_tmp_cache_fallback():
    """A session still served by a pre-upgrade statusline (writing the old
    /tmp file) is read via the legacy fallback — but only for its own id,
    so it can't leak a foreign session's numbers."""
    write_legacy_cache("S1", 30, window=200000)
    assert usage.exact_tokens("S1") == 60000   # 30% of 200k
    assert usage.exact_tokens("S2") is None


def test_per_session_wins_over_legacy():
    """When both exist, the authoritative per-session file is used."""
    write_legacy_cache("S1", 99, window=1000000)
    write_cache("S1", 5, window=1000000)
    assert usage.exact_tokens("S1") == 50000   # 5%, from the per-session file
