#!/usr/bin/env python3
"""Simulate proposed memory_list policies against a real store, read-only.

Compares, for one memory dir:
  current  — ALWAYS_LIST_TYPES=(user,feedback,reference), spent seeded with the
             full priority set, indent=2 wire format
  proposed — ALWAYS_LIST_TYPES=(user,feedback), split budget with a reserved
             floor for non-exempt entries, compact wire format, rounded age

Reads the store's index.db directly and never writes to it.

Usage: python3 tests/simulate_list_policy.py <memory_dir> [<memory_dir> ...]
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

CURRENT_EXEMPT = ("user", "feedback", "reference")
PROPOSED_EXEMPT = ("user", "feedback")
BUDGET = 6000
#: Fraction of the budget the exempt types may consume before they are
#: themselves trimmed. Whatever they do not use spills to the rest.
EXEMPT_SHARE = 0.6


def load(d: Path):
    db = sqlite3.connect(f"file:{d / 'index.db'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    now = time.time()
    entries = [
        {"name": r["name"], "type": r["type"], "description": r["description"],
         "age_days": max(0.0, (now - r["mtime"]) / 86400.0)}
        for r in db.execute("SELECT name, type, description, mtime FROM mem ORDER BY mtime DESC")
    ]
    cited = {r[0] for r in db.execute(
        """SELECT DISTINCT e.dst_name FROM mem_edges e
           JOIN mem c ON c.name = e.src_name JOIN mem d ON d.name = e.dst_name
           WHERE c.name LIKE 'compiled-%' AND d.name NOT LIKE 'compiled-%'""")}
    db.close()
    return entries, cited


def wire(entries, *, compact):
    if compact:
        payload = [{**e, "age_days": round(e["age_days"], 1)} for e in entries]
        return len(json.dumps(payload, separators=(",", ":")))
    return len(json.dumps(entries, indent=2))


def per_entry_tokens(e, *, compact):
    return max(1, -(-wire([e], compact=compact) // 4))


def run_tiered(entries, cited, *, exempt, compact, shares):
    """Three tiers, each with a budget share; unused share spills downward.

    tier 1 user/feedback  — behavior and preferences, unreachable any other way
    tier 2 compiled-      — the dense representatives of everything folded
    tier 3 raw project/reference, newest-first — recent working context
    """
    def is_exempt(t):
        return not t or t in exempt

    kept = [e for e in entries if is_exempt(e["type"]) or e["name"] not in cited]
    folded = len(entries) - len(kept)
    tiers = [
        [e for e in kept if is_exempt(e["type"])],
        [e for e in kept if not is_exempt(e["type"]) and e["name"].startswith("compiled-")],
        [e for e in kept if not is_exempt(e["type"]) and not e["name"].startswith("compiled-")],
    ]

    # Caps are CUMULATIVE, so a tier that underspends silently donates the
    # remainder to the tiers below it — no share is ever stranded.
    shown, spent = [], 0
    for tier, cum_share in zip(tiers, shares):
        cap = int(BUDGET * cum_share)
        for e in tier:
            c = per_entry_tokens(e, compact=compact)
            if spent + c > cap:
                break
            spent += c
            shown.append(e)

    from collections import Counter
    return {
        "shown": len(shown), "folded": folded,
        "withheld": len(entries) - folded - len(shown),
        "by_type": dict(Counter(e["type"] or "(untyped)" for e in shown)),
        "compiled_shown": sum(1 for e in shown if e["name"].startswith("compiled-")),
        "wire_chars": wire(shown, compact=compact),
        "wire_tokens": -(-wire(shown, compact=compact) // 4),
    }


def run(entries, cited, *, exempt, seeded, compact, share):
    """seeded=True reproduces the current bug: priority cost is charged but
    priority is never trimmed, so the budget cannot bind."""
    def is_exempt(t):
        return not t or t in exempt

    kept = [e for e in entries if is_exempt(e["type"]) or e["name"] not in cited]
    folded = len(entries) - len(kept)
    priority = [e for e in kept if is_exempt(e["type"])]
    rest = [e for e in kept if not is_exempt(e["type"])]

    if seeded:
        shown = list(priority)
        spent = sum(per_entry_tokens(e, compact=compact) for e in shown)
        for e in rest:
            c = per_entry_tokens(e, compact=compact)
            if spent + c > BUDGET:
                break
            spent += c
            shown.append(e)
    else:
        cap = int(BUDGET * share)
        shown, spent = [], 0
        for e in priority:
            c = per_entry_tokens(e, compact=compact)
            if spent + c > cap:
                break
            spent += c
            shown.append(e)
        for e in rest:
            c = per_entry_tokens(e, compact=compact)
            if spent + c > BUDGET:
                break
            spent += c
            shown.append(e)

    from collections import Counter
    return {
        "shown": len(shown), "folded": folded,
        "withheld": len(entries) - folded - len(shown),
        "by_type": dict(Counter(e["type"] or "(untyped)" for e in shown)),
        "compiled_shown": sum(1 for e in shown if e["name"].startswith("compiled-")),
        "wire_chars": wire(shown, compact=compact),
        "wire_tokens": -(-wire(shown, compact=compact) // 4),
    }


def show(label, r):
    print(f"  {label}")
    print(f"    shown {r['shown']}  folded {r['folded']}  withheld {r['withheld']}")
    print(f"    by type        : {r['by_type']}")
    print(f"    compiled- shown: {r['compiled_shown']}")
    print(f"    wire           : {r['wire_chars']} chars = ~{r['wire_tokens']} tokens "
          f"({'OVER' if r['wire_tokens'] > BUDGET else 'within'} {BUDGET} budget)")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        d = Path(arg)
        entries, cited = load(d)
        print(f"{d}  ({len(entries)} memories, {len(cited)} cited by compiled- articles)")
        show("CURRENT  (reference exempt, seeded spent, indent=2)",
             run(entries, cited, exempt=CURRENT_EXEMPT, seeded=True, compact=False, share=1.0))
        show("PROPOSED (reference foldable, split budget, compact wire)",
             run(entries, cited, exempt=PROPOSED_EXEMPT, seeded=False, compact=True, share=EXEMPT_SHARE))
        show("TIERED   (+ compiled- articles as their own tier)",
             run_tiered(entries, cited, exempt=PROPOSED_EXEMPT, compact=True,
                        shares=(0.25, 0.70, 1.00)))
        print()
