#!/usr/bin/env python3
"""Why did ccloop's Stop hook let a session stop?

Read-only. For one Claude Code session id this prints:

- every Stop recorded in the session transcript (``stop_hook_summary``), with
  whether it was blocked, the hooks that ran, their durations, and any errors;
- each background-task ``.output`` in the session's tasks dir, with what the
  installed ``ccloop.keepgoing`` concludes about it right now (running or not)
  and the evidence: which processes ``lsof`` says hold it open, or for an agent
  whether its notification was delivered and how long its transcript has been
  silent.

Usage:
    python3 ccloop/tests/probe_keepgoing_stops.py <session-id> [--project-dir DIR]
                                                  [--last N] [--installed]
                                                  [--classify TEXT]

``--project-dir`` is the session's working directory (default: cwd); it names
the transcript at ~/.claude/projects/<slug>/<session-id>.jsonl. The verdicts
come from this checkout's ``ccloop/src`` unless ``--installed`` asks for the
installed package, which is what the session's hook actually runs.
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if "--installed" not in sys.argv:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ccloop import keepgoing  # noqa: E402


def transcript_for(session_id, project_dir):
    slug = str(Path(project_dir).resolve()).replace("/", "-")
    p = Path.home() / ".claude" / "projects" / slug / f"{session_id}.jsonl"
    if p.is_file():
        return p
    hits = glob.glob(str(Path.home() / ".claude" / "projects" / "*" / f"{session_id}.jsonl"))
    return Path(hits[0]) if hits else None


def stops(transcript):
    out = []
    with open(transcript, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "stop_hook_summary" not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("subtype") != "stop_hook_summary":
                continue
            out.append(rec)
    return out


def describe_stop(rec):
    hooks = []
    for h in rec.get("hookInfos") or rec.get("hooks") or []:
        if isinstance(h, dict):
            hooks.append("%s(%sms)" % (h.get("command", "?").split("/")[-1],
                                       h.get("durationMs", "-")))
    errors = rec.get("hookErrors") or []
    blocked = bool(errors)
    first_err = ""
    if errors:
        e = errors[0]
        first_err = (e if isinstance(e, str) else json.dumps(e))[:100].replace("\n", " ")
    return "%s  %-7s  prevented=%s  hooks=[%s]  %s" % (
        rec.get("timestamp", "?"), "BLOCKED" if blocked else "allowed",
        rec.get("preventedContinuation"), ", ".join(hooks), first_err)


def lsof_holders(path):
    try:
        proc = subprocess.run(["lsof", "-w", "-F", "pcn", "--", os.path.realpath(path)],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"lsof failed: {exc}"
    holders, pid = [], None
    for line in proc.stdout.splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif line.startswith("c") and pid:
            holders.append(f"{pid}:{line[1:]}")
    return ", ".join(holders) or "none"


def classify(transcript, needle):
    """Print the shape of every transcript record that mentions ``needle``:
    top-level type, subtype, attachment type/commandMode, message content
    block types, and the text around the match."""
    with open(transcript, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            if needle not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError as exc:
                print(f"  {n}: unparseable: {exc}")
                continue
            att = rec.get("attachment") if isinstance(rec.get("attachment"), dict) else {}
            content = (rec.get("message") or {}).get("content")
            if isinstance(content, list):
                blocks = [b.get("type") for b in content if isinstance(b, dict)]
            else:
                blocks = type(content).__name__
            flat = json.dumps(rec)
            i = flat.find(needle)
            print(f"  {n}: type={rec.get('type')} subtype={rec.get('subtype', '')} "
                  f"att={att.get('type', '')}/{att.get('commandMode', '')} blocks={blocks}")
            print(f"      ...{flat[max(0, i - 200):i + 120]}...")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("session_id")
    ap.add_argument("--project-dir", default=os.getcwd())
    ap.add_argument("--last", type=int, default=15)
    ap.add_argument("--installed", action="store_true",
                    help="judge with the installed ccloop instead of this checkout")
    ap.add_argument("--classify", metavar="TEXT",
                    help="only print the shape of each transcript record mentioning TEXT")
    args = ap.parse_args(argv)
    sid = args.session_id

    transcript = transcript_for(sid, args.project_dir)
    if args.classify:
        classify(transcript, args.classify)
        return 0
    print(f"ccloop.keepgoing: {keepgoing.__file__}")
    print(f"transcript: {transcript}")
    if transcript:
        recs = stops(transcript)
        n_blocked = sum(1 for r in recs if r.get("hookErrors"))
        print(f"stops: {len(recs)}  blocked: {n_blocked}  allowed: {len(recs) - n_blocked}")
        for r in recs[-args.last:]:
            print("  " + describe_stop(r))
        if recs:
            print("last stop record, raw keys: " + ", ".join(sorted(recs[-1].keys())))
            print("  " + json.dumps(recs[-1])[:1500])

    dirs = glob.glob(f"/tmp/claude-{os.getuid()}/*/{sid}/tasks")
    print(f"tasks dirs: {dirs}")
    if len(dirs) != 1:
        return 0
    tasks = dirs[0]
    outputs = sorted(os.path.join(tasks, n) for n in os.listdir(tasks) if n.endswith(".output"))
    commands = [p for p in outputs if not os.path.islink(p)]
    agents = [p for p in outputs if os.path.islink(p)]
    procfs = keepgoing._outputs_with_live_writer(commands)
    by_lsof = keepgoing._outputs_open_by_lsof(commands) if procfs is None else None
    live_cmds = procfs if procfs is not None else (by_lsof or set())
    # 0.20.1 named this _delivered_task_ids and counted fewer record shapes.
    find_ids = getattr(keepgoing, "_notified_task_ids", None) or keepgoing._delivered_task_ids
    notified = find_ids(str(transcript) if transcript else None)
    live_agents = keepgoing._live_agents(agents, str(transcript) if transcript else None, time.time())
    now = time.time()
    print(f"liveness source for commands: {'procfs' if procfs is not None else 'lsof'}")
    for p in commands:
        age = now - os.stat(p).st_mtime
        print("  cmd   %-28s %-8s age=%6.0fs holders=%s" % (
            os.path.basename(p), "RUNNING" if p in live_cmds else "done", age, lsof_holders(p)))
    for p in agents:
        tid = os.path.basename(p)[:-len(".output")]
        try:
            age = "%6.0fs" % (now - os.stat(p).st_mtime)
        except OSError:
            age = "dangling"
        print("  agent %-28s %-8s notified=%s silent=%s" % (
            os.path.basename(p), "RUNNING" if p in live_agents else "done",
            None if notified is None else tid in notified, age))
    print("keepgoing count now: %d" % keepgoing._pending_background_task_count(
        sid, str(transcript) if transcript else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
