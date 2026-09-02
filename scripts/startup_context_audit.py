#!/usr/bin/env python3
"""Measure S — the context a session carries BEFORE its first unit of work.

Rationale in docs/context-economics.md: lowering the ccloop cutoff was
already tried and lost because S (~70k) is a fixed cost rebuilt on every
session restart.  S is the lever with no downside, but it cannot be reduced
until it is attributed.

Method, no estimation involved: the FIRST assistant message of a transcript
records the exact prompt it was served —

    input_tokens + cache_creation_input_tokens + cache_read_input_tokens

That sum IS S for that session.  Everything before the first assistant turn
(system prompt, tool schemas, MCP tool definitions, skills listing, global
CLAUDE.md, project CLAUDE.md, any --append-system-prompt-file, SessionStart
hook injections, the first user message) is inside it.

Grouping by project and differencing against each project's CLAUDE.md size
separates the per-project term from the floor that every session on this
machine pays.
"""

import argparse
import json
import os
import statistics
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"


def project_dir_to_path(name):
    """~/.claude/projects/-src-mxfs  ->  /src/mxfs (best effort)."""
    return "/" + name.lstrip("-").replace("-", "/")


def first_assistant_usage(path):
    """(S, model, n_lines_before) for the first assistant turn, or None."""
    before = 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "assistant" or d.get("isSidechain"):
                before += 1
                continue
            msg = d.get("message") or {}
            u = msg.get("usage") or {}
            if not u:
                before += 1
                continue
            s = (u.get("input_tokens", 0)
                 + u.get("cache_creation_input_tokens", 0)
                 + u.get("cache_read_input_tokens", 0))
            if s <= 0:
                before += 1
                continue
            return s, msg.get("model", "?"), before
    return None


def claude_md_bytes(proj_path):
    p = Path(proj_path) / "CLAUDE.md"
    try:
        return p.stat().st_size
    except OSError:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-sessions", type=int, default=1,
                    help="skip projects with fewer transcripts than this")
    ap.add_argument("--per-session", action="store_true")
    args = ap.parse_args()

    global_md = 0
    try:
        global_md = (Path.home() / ".claude" / "CLAUDE.md").stat().st_size
    except OSError:
        pass

    rows = []
    for pdir in sorted(PROJECTS.iterdir()):
        if not pdir.is_dir():
            continue
        vals = []
        for tx in sorted(pdir.glob("*.jsonl")):
            got = first_assistant_usage(tx)
            if got:
                vals.append((got[0], got[1], tx.name[:8]))
        if len(vals) < args.min_sessions:
            continue
        proj = project_dir_to_path(pdir.name)
        rows.append({
            "dir": pdir.name,
            "proj": proj,
            "n": len(vals),
            "median": int(statistics.median(v[0] for v in vals)),
            "min": min(v[0] for v in vals),
            "max": max(v[0] for v in vals),
            "md": claude_md_bytes(proj),
            "vals": vals,
        })

    rows.sort(key=lambda r: r["median"])
    print(f"global ~/.claude/CLAUDE.md: {global_md} bytes (~{global_md//4//1000}k tokens)\n")
    print(f"{'project':38} {'n':>4} {'median S':>9} {'min':>8} {'max':>8} {'CLAUDE.md B':>12}")
    for r in rows:
        print(f"{r['proj'][:38]:38} {r['n']:4d} {r['median']:9,} {r['min']:8,} "
              f"{r['max']:8,} {r['md']:12,}")

    # The floor: no project can pay less than the smallest observed S.
    if rows:
        floor = min(r["min"] for r in rows)
        who = [r["proj"] for r in rows if r["min"] == floor]
        print(f"\nsmallest S observed anywhere: {floor:,} tokens  ({who[0]})")
        print("  -> upper bound on the machine-wide floor "
              "(system prompt + tool schemas + MCP tool defs + skills listing)")

    if args.per_session:
        for r in rows:
            print(f"\n{r['proj']}  CLAUDE.md={r['md']}B")
            for s, model, sid in sorted(r["vals"]):
                print(f"   {sid}  {s:9,}  {model}")


if __name__ == "__main__":
    main()
