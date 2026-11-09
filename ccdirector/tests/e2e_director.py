#!/usr/bin/env python3
"""End-to-end: `ccdirector start` with real Claude sessions on a throwaway project.

The project is one file, calc.py, with four unimplemented functions, a test
script per function, and a ccproject criteria board with one criterion per
function. Every function lives in the same file, so the workers must share it.
The goal is the board's gate passing.

What it records (and prints):
- ccdirector's exit code and the gate's verdict afterwards;
- every event in .ccdirector/events.jsonl, by kind;
- the edit-lock log: which workers took calc.py's lock, and how often one had
  to wait for another;
- each worker's exit, and calc.py as left.

Not collected by pytest (real claude calls, several minutes). Run it by hand:

    python3 tests/e2e_director.py [--workers 2] [--model sonnet] [--worker-model haiku]

The project folder is printed and kept for inspection.
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CALC = '''"""A small calculator."""


def add(a, b):
    raise NotImplementedError


def sub(a, b):
    raise NotImplementedError


def mul(a, b):
    raise NotImplementedError


def div(a, b):
    """Divide a by b; dividing by zero raises ZeroDivisionError."""
    raise NotImplementedError
'''

TESTS = {
    "add": "from calc import add\nassert add(2, 3) == 5\nassert add(-1, 1) == 0\nprint('add ok')\n",
    "sub": "from calc import sub\nassert sub(5, 3) == 2\nassert sub(0, 4) == -4\nprint('sub ok')\n",
    "mul": "from calc import mul\nassert mul(4, 3) == 12\nassert mul(-2, 3) == -6\nprint('mul ok')\n",
    "div": ("from calc import div\nassert div(8, 2) == 4\ntry:\n    div(1, 0)\nexcept "
            "ZeroDivisionError:\n    pass\nelse:\n    raise AssertionError('div(1, 0) must raise')\n"
            "print('div ok')\n"),
}

GOAL = ("Implement add, sub, mul and div in calc.py so that every criterion on the project's "
        "criteria board passes. Each criterion's detector is a test script in tests/; a "
        "criterion is green only once its detector has been run and the result recorded with "
        "`ccproject_criteria.py update ID -s PASS -m '<what was measured>'`.")


def make_project() -> Path:
    root = Path(tempfile.mkdtemp(prefix="ccdirector-e2e-"))
    (root / "calc.py").write_text(CALC)
    (root / "tests").mkdir()
    for name, body in TESTS.items():
        (root / "tests" / f"test_{name}.py").write_text(body)
    (root / "README.md").write_text("A small calculator. Tests: `python3 tests/test_<fn>.py`, "
                                    "run from the project root with PYTHONPATH=.\n")
    for name in TESTS:
        subprocess.run(["ccproject_criteria.py", "add", "-i", name,
                        "-r", f"calc.{name} behaves as tests/test_{name}.py requires",
                        "-d", f"PYTHONPATH=. python3 tests/test_{name}.py"],
                       cwd=str(root), check=True, capture_output=True)
    return root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--model", default="sonnet", help="director model")
    parser.add_argument("--worker-model", default="haiku")
    parser.add_argument("--timeout", type=float, default=3600)
    args = parser.parse_args()

    root = make_project()
    print(f"project: {root}", flush=True)
    cmd = [shutil.which("ccdirector") or "ccdirector", "start", str(root), "--headless",
           "--goal", GOAL, "--workers", str(args.workers), "--model", args.model,
           "--worker-model", args.worker_model]
    print("command: " + " ".join(cmd[:4]) + " … " + " ".join(cmd[6:]), flush=True)
    started = time.time()
    with open(root.parent / (root.name + ".ccdirector.log"), "w") as log:
        try:
            done = subprocess.run(cmd, cwd=str(root), stdout=log, stderr=subprocess.STDOUT,
                                  timeout=args.timeout)
            code = done.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    elapsed = time.time() - started

    folder = root / ".ccdirector"
    gate = subprocess.run(["ccproject_criteria.py", "--gate"], cwd=str(root),
                          capture_output=True, text=True)
    events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()] \
        if (folder / "events.jsonl").is_file() else []
    locks = [json.loads(line) for line in (folder / "locks.log").read_text().splitlines()] \
        if (folder / "locks.log").is_file() else []

    print(f"\nccdirector exit: {code} after {elapsed / 60:.1f} min")
    print(f"gate after: exit {gate.returncode}\n{gate.stdout.strip()}")
    print("\nevents: " + ", ".join(f"{k} {v}" for k, v in
                                   collections.Counter(e["kind"] for e in events).items()))
    for e in events:
        if e["kind"] in ("item_claimed", "item_done", "item_check_failed", "item_parked",
                         "worker_started", "worker_exited", "workers_failing", "goal_done",
                         "goal_stuck", "supervisor_error"):
            extra = {k: v for k, v in e.items() if k not in ("time", "kind", "output", "log")}
            print(f"  {e['time']} {e['kind']} {extra}")
    calc_locks = [l for l in locks if l["path"].endswith("/calc.py")]
    print(f"\nedit-lock log: {len(locks)} entries; calc.py: "
          + ", ".join(f"{k} {v}" for k, v in collections.Counter(
              f"{l['owner']}:{l['op']}" for l in calc_locks).items()))
    owners = sorted({l["owner"] for l in calc_locks if l["op"] == "acquire"})
    print(f"workers that wrote calc.py under the lock: {owners}")
    print(f"times a worker waited for another's write: "
          f"{sum(1 for l in locks if l['op'] == 'wait')}")
    print(f"leftover leases: {sorted(p.name for p in (folder / 'locks').glob('*.lock'))}"
          if (folder / "locks").is_dir() else "leftover leases: none")
    print("\ncalc.py now:\n" + (root / "calc.py").read_text())
    print(f"project kept at {root}; ccdirector output in {root}.ccdirector.log")
    return 0 if code == 0 and gate.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
