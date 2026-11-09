#!/usr/bin/env python3
"""Run by hand: do the worker hooks work in a real headless Claude Code session?

The unit tests feed the hooks JSON directly. What they cannot show is that Claude
Code runs `notify` before a tool in `claude -p` and puts its text in front of the
model, or that a matcher in `mcp__x_.*` form fires for an MCP tool. Costs a few
cents of a small model.

The consult half points the matcher at ccusage, a local MCP server, so that proving
the matcher does not spend a real second opinion.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")
sys.path.insert(0, SRC)

from ccdirector import plan, progress, session, work, workers  # noqa: E402

WORD = "PELICAN-4471"
PLAN = {"goal": "Probe.", "source": "board", "gate": "gate", "criteria": ["c"],
        "items": [{"id": "a", "what": "Original text.", "serves": ["c"], "priority": 1,
                   "paths": ["a"], "check": "true", "size": "S"}]}


def claude(root: Path, prompt: str) -> str:
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "CCLOOP_RUN_ID", "CCLOOP_RUN_DIR", "CCLOOP_SETTINGS")}
    env.update(PYTHONPATH=SRC, CCDIRECTOR_ROOT=str(root), CCDIRECTOR_WORKER="w1",
               CCDIRECTOR_ROLE="worker")
    done = subprocess.run(["claude", "-p", "--model", "haiku", "--permission-mode",
                           "bypassPermissions", "--settings", workers.settings(root), prompt],
                          cwd=str(root), env=env, capture_output=True, text=True, timeout=240)
    return done.stdout + done.stderr


def main() -> int:
    root = Path(os.path.realpath(tempfile.mkdtemp(prefix="ccdirector-probe-")))
    plan.write(root, PLAN)
    work.sync(root, PLAN)
    work.next_item(root, "w1")
    body = copy.deepcopy(PLAN)
    body["items"][0]["what"] = f"The code word is {WORD}."
    plan.write(root, body)
    work.sync(root, body)

    said = claude(root, "Run `echo hello` with the Bash tool. Then say any code word that "
                        "text outside this message gave you, or NONE.")
    delivered = [e for e in work.events(root) if e["kind"] == "item_change_delivered"]
    notify_ok = WORD in said and len(delivered) == 1
    print(f"notify: {'OK' if notify_ok else 'FAILED'} delivered={len(delivered)}\n  {said[-300:]}")

    session.CONSULT_TOOLS = "mcp__ccusage_.*"
    said = claude(root, "Call the MCP tool mcp__ccusage__get_context_usage once (load it with "
                        "ToolSearch if needed), then say DONE.")
    consulted = progress.load(root).get("worker_consults", 0)
    consult_ok = consulted == 1
    print(f"consult: {'OK' if consult_ok else 'FAILED'} consults={consulted}\n  {said[-300:]}")
    print(json.dumps([e for e in work.events(root) if e["kind"] == "consulted"]))
    return 0 if notify_ok and consult_ok else 1


if __name__ == "__main__":
    sys.exit(main())
