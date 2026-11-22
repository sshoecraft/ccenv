"""Live check, run by hand (not collected by pytest): a headless claude given the
worker MCP server through --mcp-config, the way ccdirector launches workers, can
call it, and a `run` (or `item_done`, whose check is the long command) call
longer than Claude Code's 2-minute auto-background returns in place with its
result.

    python3 tests/live_mcp_worker.py [--model haiku] [--seconds 150] [--tool run|item_done]

Costs a few small requests on the given model.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "src"))

from ccdirector import plan, work, workers  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--seconds", type=int, default=150)
    parser.add_argument("--tool", choices=["run", "item_done"], default="run")
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="ccdirector-live-"))
    long_command = f"sleep {args.seconds}; echo LIVE-OK"
    current = {"goal": "g", "source": "directive", "gate": "", "criteria": ["c"],
               "resources": {"rig": {"capacity": 1, "what": "a test rig"}},
               "items": [{"id": "a", "what": "a", "serves": ["c"], "priority": 1,
                          "paths": [], "check": long_command, "size": "S"}], "risks": []}
    plan.write(root, current)
    work.sync(root, current)
    assert work.next_item(root, "worker-1")[1]["id"] == "a"     # the worker holds it
    config = workers.mcp_config(root, "worker-1")
    server = config["mcpServers"]["ccdirector"]
    server["env"]["PYTHONPATH"] = os.pathsep.join([str(HERE / "src"), str(HERE.parent / "ccenvmcp")])
    path = workers.mcp_config_path(root, "worker-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config))
    if args.tool == "run":
        prompt = (f"Call the ccdirector MCP tool `run` with command \"{long_command}\". When "
                  "it returns, reply with only its exit code and the last line of its tail, "
                  "nothing else.")
    else:
        prompt = ("Call the ccdirector MCP tool `item_done` with id \"a\". When it returns, "
                  "reply with only its first line and the line of its output that starts "
                  "with LIVE, nothing else.")
    started = time.time()
    done = subprocess.run(["claude", "-p", "--model", args.model, "--mcp-config", str(path),
                           "--permission-mode", "bypassPermissions", prompt],
                          capture_output=True, text=True, cwd=str(root), timeout=args.seconds + 600)
    took = time.time() - started
    print(f"claude exit {done.returncode} after {took:.0f}s")
    print("stdout:", done.stdout.strip()[-500:])
    if done.stderr.strip():
        print("stderr:", done.stderr.strip()[-500:])
    ok = done.returncode == 0 and "LIVE-OK" in done.stdout and took >= args.seconds
    if args.tool == "item_done":
        ok = ok and "PASSED" in done.stdout and \
            work.load(root)["items"]["a"]["status"] == work.DONE
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
