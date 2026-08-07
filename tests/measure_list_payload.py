#!/usr/bin/env python3
"""Measure the REAL serialized size of a memory_list payload for a store.

The in-tree estimator (Store._entry_tokens) counts name+description+30 chars
per entry. The MCP server ships json.dumps(..., indent=2), which costs far
more: key names, quoting, indentation and a full-precision age_days float.
This prints both so the gap is visible, and breaks the always-listed types
out by count so it is obvious which population is unbounded.

Usage: python3 tests/measure_list_payload.py <memory_dir> [<memory_dir> ...]
"""
import json
import sys
from collections import Counter
from pathlib import Path

from ccmemory.store import Store
from ccmemory.mcp_server import list_token_budget, _list_note


def report(d: Path) -> None:
    with Store(d) as s:
        s.reindex()
        shown, counts = s.list_all(token_budget=list_token_budget())
        note = _list_note(d, counts, include_folded=False)
        payload = {**counts, "note": note, "memories": shown}
        # Must mirror mcp_server's serialization exactly, or this probe
        # measures a payload nothing ships.
        wire = json.dumps(payload, separators=(",", ":"), default=str)
        est = sum(s._entry_tokens(e) for e in shown)
        by_type = Counter(e["type"] or "(untyped)" for e in shown)
        always = sum(n for t, n in by_type.items()
                     if Store._is_always_listed(None if t == "(untyped)" else t))

    print(f"{d}")
    print(f"  counts        : {counts}")
    print(f"  shown by type : {dict(by_type)}")
    print(f"  always-listed : {always} of {counts['shown']} (exempt from budget)")
    print(f"  budget        : {list_token_budget()} tokens")
    print(f"  estimator says: {est} tokens")
    print(f"  wire payload  : {len(wire)} chars = ~{-(-len(wire) // 4)} tokens")
    if est:
        print(f"  understated by: {len(wire) / 4 / est:.2f}x")
    print()


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        report(Path(arg))
