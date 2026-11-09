#!/usr/bin/env python3
"""Probe: what Claude Code does when a file it has read is changed by someone else.

Workers in one tree edit the same files. This runs real `claude -p` sessions and
records, for Edit and for Write, whether a session that read a file and then had
it changed behind its back is refused, is shown the change, or overwrites it.

Two scenarios, each with two files (shared.c gets an Edit, whole.c a whole-file
Write, both from the session that read them before the change):

  S1  the change is made by a second Claude session (B) while session A is
      blocked inside a Bash command, as when one worker builds while another
      edits.
  S2  the change is made by a plain process between two of A's tool calls,
      while A's model is producing its next step.

Not collected by pytest (it costs real claude calls). Run it by hand:

    python3 tests/probe_stale_edit.py [--model haiku] [--keep]

Every session's stream-json is saved next to the files in the work folder, which
is printed at the end and kept with --keep.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

ORIGINAL = "alpha\nbeta\ngamma\n"
TIMEOUT = 600

WAIT_FOR_B = ("touch a-read; for i in $(seq 1200); do [ -e b-done ] && exit 0; "
              "sleep 0.5; done; exit 1")

A_COMMON = """You are part of an automated test of your file tools. Follow these steps
exactly, in order, one tool call per step. Never Read a file except in step 1. Never
retry a failed tool call. Do not use Bash to edit files.

1. Read shared.c, then Read whole.c.
2. Run this Bash command exactly: {bash}
3. Use Edit on shared.c: replace the exact text `beta` with `beta-A`.
4. Use Write on whole.c with exactly this content (three lines):
alpha
beta-A
gamma

5. Reply with one line per step 3 and 4: the tool name and its result, verbatim."""

B_PROMPT = """You are part of an automated test of your file tools. Follow these steps
exactly, one tool call per step. Do not use Bash to edit files.

1. Read shared.c, then Read whole.c.
2. Use Edit on shared.c: replace the exact text `gamma` with `gamma-B`.
3. Use Edit on whole.c: replace the exact text `gamma` with `gamma-B`.
4. Run this Bash command exactly: touch b-done
5. Reply "done"."""


class Session:
    """One `claude -p` streaming its events; every event is timestamped on arrival."""

    def __init__(self, name: str, prompt: str, work: Path, model: str):
        self.name = name
        self.events: list[tuple[float, dict]] = []
        self.log = (work / f"{name}.stream.jsonl").open("w")
        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDECODE", "CCLOOP_RUN_ID", "CCLOOP_SETTINGS")}
        cmd = [shutil.which("claude") or "claude", "-p", prompt,
               "--output-format", "stream-json", "--verbose",
               "--permission-mode", "bypassPermissions",
               "--tools", "Read,Edit,Write,Bash",
               "--strict-mcp-config", "--no-session-persistence", "--model", model]
        self.proc = subprocess.Popen(cmd, cwd=str(work), env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        for line in self.proc.stdout:
            self.log.write(line)
            self.log.flush()
            try:
                self.events.append((time.time(), json.loads(line)))
            except ValueError:
                pass

    def wait(self) -> int:
        try:
            code = self.proc.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = -9
        self.reader.join(timeout=10)
        self.log.close()
        return code

    def calls(self) -> list[dict]:
        """Tool calls in order, each with its result and the time the result arrived."""
        found, by_id = [], {}
        for stamp, event in self.events:
            content = (event.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if block.get("type") == "tool_use":
                    call = {"name": block.get("name"), "input": block.get("input") or {},
                            "asked": stamp}
                    by_id[block.get("id")] = call
                    found.append(call)
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in by_id:
                    result = block.get("content")
                    if isinstance(result, list):
                        result = " ".join(str(p.get("text", "")) for p in result
                                          if isinstance(p, dict))
                    call = by_id[block["tool_use_id"]]
                    call["result"] = str(result)
                    call["error"] = bool(block.get("is_error"))
                    call["answered"] = stamp
        return found


def wait_for(path: Path, session: Session, limit: float = TIMEOUT) -> bool:
    end = time.time() + limit
    while time.time() < end:
        if path.exists():
            return True
        if session.proc.poll() is not None:
            return path.exists()
        time.sleep(0.01)
    return False


def fresh(work: Path):
    for name in ("shared.c", "whole.c"):
        (work / name).write_text(ORIGINAL)
    for flag in ("a-read", "b-done"):
        if (work / flag).exists():
            (work / flag).unlink()


def scenario_one(work: Path, model: str) -> dict:
    fresh(work)
    a = Session("S1-A", A_COMMON.format(bash=WAIT_FOR_B), work, model)
    if not wait_for(work / "a-read", a):
        a.wait()
        return {"error": "session A never reached its Bash step", "A": a}
    b = Session("S1-B", B_PROMPT, work, model)
    b_code = b.wait()
    changed = time.time()
    if not (work / "b-done").exists():
        (work / "b-done").touch()      # release A so the run ends; reported as B failing
    a_code = a.wait()
    return {"A": a, "B": b, "a_code": a_code, "b_code": b_code, "changed": changed}


def scenario_two(work: Path, model: str) -> dict:
    fresh(work)
    a = Session("S2-A", A_COMMON.format(bash="touch a-read"), work, model)
    if not wait_for(work / "a-read", a):
        a.wait()
        return {"error": "session A never reached its Bash step", "A": a}
    for name in ("shared.c", "whole.c"):
        path = work / name
        path.write_text(path.read_text().replace("gamma", "gamma-B"))
    changed = time.time()
    a_code = a.wait()
    return {"A": a, "a_code": a_code, "changed": changed}


def describe(title: str, outcome: dict, work: Path) -> list[str]:
    lines = [f"== {title}"]
    if "error" in outcome:
        return lines + [f"  PROBE FAILED: {outcome['error']}"]
    if "b_code" in outcome:
        b_edits = [c for c in outcome["B"].calls() if c["name"] == "Edit"]
        lines.append(f"  B exit {outcome['b_code']}; B edits: "
                     + "; ".join(f"{c['input'].get('file_path', '?').split('/')[-1]} "
                                 f"{'ERROR' if c.get('error') else 'ok'}" for c in b_edits))
    lines.append(f"  A exit {outcome['a_code']}")
    after = [c for c in outcome["A"].calls()
             if c["name"] in ("Edit", "Write", "Read") and c["asked"] >= outcome["changed"] - 0.001]
    early = [c for c in outcome["A"].calls()
             if c["name"] in ("Edit", "Write") and c["asked"] < outcome["changed"]]
    if early:
        lines.append(f"  WARNING: A asked for {len(early)} Edit/Write before the change "
                     "finished; this run does not test a stale read")
    for call in after:
        target = str(call["input"].get("file_path", "?")).split("/")[-1]
        verdict = "ERROR" if call.get("error") else "ok"
        lines.append(f"  A {call['name']} {target}: {verdict}: {call.get('result', '(no result)')[:200]}")
    shared, whole = (work / "shared.c").read_text(), (work / "whole.c").read_text()
    lines.append(f"  shared.c now: {shared.splitlines()}")
    lines.append(f"  whole.c  now: {whole.splitlines()}")
    lines.append("  shared.c: " + ("both changes kept" if "beta-A" in shared and "gamma-B" in shared
                                   else "B's change LOST" if "beta-A" in shared
                                   else "A's edit did not land"))
    lines.append("  whole.c:  " + ("B's change OVERWRITTEN by A's Write"
                                   if "beta-A" in whole and "gamma-B" not in whole
                                   else "both changes kept" if "beta-A" in whole
                                   else "A's Write did not land; B's change kept"
                                   if "gamma-B" in whole else "neither change present"))
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--keep", action="store_true", help="keep the work folders")
    args = parser.parse_args()
    report = []
    folders = []
    for title, run in (("S1: second Claude session edits while A is in Bash", scenario_one),
                       ("S2: plain process edits between A's tool calls", scenario_two)):
        work = Path(tempfile.mkdtemp(prefix="ccdirector-stale-"))
        folders.append(work)
        report += describe(title, run(work, args.model), work) + [""]
    print("\n".join(report))
    for work in folders:
        print(f"work folder: {work}")
        if not args.keep:
            shutil.rmtree(work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
