"""events.jsonl — the append-only event log.

Every state transition is one JSON object per line, appended with O_APPEND
(atomic for line-sized writes on POSIX). Nothing is ever rewritten or
deleted: current state is a fold over this log (see store.derive_states).
That is what makes outcomes inescapable — there is no field to edit.

Event kinds:
  created    {id}                      contract filed
  fired      {id, observed, counterfactual?}   predicate observed true (once, latching)
  ack        {id, disposition, resolution?, note?, evidence?, next_review?}
  superseded {id, successor}           replaced via prospect_amend
  expired    {id, counterfactual?, probe_skipped?}   passed expiry without firing
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import paths
from .util import iso_now


def append_event(prospect_dir: Path, event: dict) -> dict:
    ev = dict(event)
    ev.setdefault("ts", iso_now())
    line = json.dumps(ev, separators=(",", ":"), default=str) + "\n"
    path = paths.events_path(prospect_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    return ev


def read_events(prospect_dir: Path) -> list[dict]:
    """All events in append order. Corrupt lines are skipped, not fatal —
    one bad byte must not brick every wake evaluation."""
    path = paths.events_path(prospect_dir)
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                out.append(ev)
    return out
