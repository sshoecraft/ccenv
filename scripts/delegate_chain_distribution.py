#!/usr/bin/env python3
"""Chain-length distribution behind the ccenv delegation hook's threshold.

The delegation proposal (see /src/mxfs/docs/delegation.md) counts "requests
saveable" as sum(len - 1) over every chain of >= 3 consecutive mechanical
requests.  That is the IDEAL-delegation number: it assumes the session knows
the chain's length at its first call and delegates the whole thing up front.

A PreToolUse hook cannot know that.  It can only react after N calls have
already happened, and each of those calls already cost a request.  Denying at
position N costs:  N (already spent) + 1 (the Agent call the model must now
issue).  So the real saving for a chain of length L under deny-at-N is

    max(0, L - (N + 1))

and a chain with L <= N + 1 is a NET LOSS of (N + 1 - L) requests.

This script reads the same transcripts as ccloop_delegation_audit.py and
prints, for each candidate threshold, the true expected saving.  Run it
before picking CCENV_DELEGATE_CHAIN.
"""

import argparse
import importlib.util
import sys
from pathlib import Path
from collections import Counter

AUDIT = Path("/src/mxfs/scripts/ccloop_delegation_audit.py")


def load_audit():
    spec = importlib.util.spec_from_file_location("delegation_audit", AUDIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="+", required=True,
                    help="session-id prefixes, as passed to ccloop_delegation_audit.py")
    ap.add_argument("--model", default="claude-fable-5")
    ap.add_argument("--project", default="/home/steve/.claude/projects/-src-mxfs")
    args = ap.parse_args()

    audit = load_audit()
    proj = Path(args.project)
    rows = []
    for pref in args.files:
        matches = sorted(proj.glob(f"{pref}*.jsonl"))
        if not matches:
            print(f"no transcript for {pref}", file=sys.stderr)
            continue
        rows.append(audit.audit_session(str(matches[0]), args.model))

    chains = [c for r in rows for c in r["chains"]]
    total_req = sum(r["requests"] for r in rows)
    print(f"sessions={len(rows)} requests={total_req} chains={len(chains)}")

    dist = Counter(chains)
    print("\nchain length : count : requests in chains of that length")
    for L in sorted(dist):
        print(f"  {L:4d} : {dist[L]:5d} : {L * dist[L]:6d}")

    ideal = sum(c - 1 for c in chains if c >= 3)
    print(f"\nideal delegation (delegate at chain start, len>=3): {ideal} requests"
          f"  ({100 * ideal / total_req:.0f}% of all requests)")

    print("\nreal saving under deny-at-N (gross win / gross loss / net):")
    for N in range(2, 9):
        win = sum(max(0, c - (N + 1)) for c in chains)
        loss = sum(max(0, (N + 1) - c) for c in chains if c >= N)
        print(f"  N={N}: +{win:5d} / -{loss:5d} / net {win - loss:+6d}"
              f"  ({100 * (win - loss) / total_req:+.0f}% of all requests)")


if __name__ == "__main__":
    main()
