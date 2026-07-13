from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ccprospect import paths
from ccprospect.store import Store
from ccprospect.util import to_iso


@pytest.fixture
def startup_dir(tmp_path, monkeypatch):
    """The directory Claude Code was 'started in'. Resolution is CWD-only,
    so chdir there; code under test resolves to <here>/.ccprospect."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def store(startup_dir):
    return Store(paths.startup_prospect_dir(startup_dir), create=True)


@pytest.fixture
def clock(monkeypatch):
    """Controllable logical clock patched into the store module.

    ``clock.advance(seconds=...)`` moves it; predicates receive it as the
    ``now`` parameter through Store, so the whole lifecycle follows it.
    """
    class Clock:
        def __init__(self):
            self.now = datetime.now(timezone.utc)

        def advance(self, **kwargs):
            self.now = self.now + timedelta(**kwargs)
            return self.now

        def __call__(self):
            return self.now

    c = Clock()
    monkeypatch.setattr("ccprospect.store.now_utc", c)
    return c


def in_days(days: float, base: datetime | None = None) -> str:
    base = base or datetime.now(timezone.utc)
    return to_iso(base + timedelta(days=days))


def file_prospect(store: Store, *, title="test prospect", intention="do the thing",
                  predicate=None, expires=None, **kwargs):
    """File a real contract against a real (missing) sentinel path by default."""
    if predicate is None:
        predicate = {"type": "path_exists", "path": "sentinel-appears.txt"}
    if expires is None:
        expires = in_days(30)
    return store.create(title=title, intention=intention, predicate=predicate,
                        expires=expires, **kwargs)


def touch(startup_dir: Path, name: str, content: str = "x") -> Path:
    p = startup_dir / name
    p.write_text(content, encoding="utf-8")
    return p
