"""Claude Code hooks that keep sessions and the run's records in step.

`notify` (a worker's PreToolUse, on every tool) tells a worker its item changed.
A worker reads its item once, then can work for hours; without this it keeps
following text the director has since withdrawn.

`consult` (PostToolUse on the `ask_*` advisors, in the director and every worker)
records that advice was sought, because the tools are outside anything ccdirector
runs and nothing else would know.

Both fail open. They inform; neither is worth refusing a tool call over.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from . import editlock, plan as planmod, progress, work

#: Long enough for any real item; hook context is not the place for an unbounded one.
NOTICE_WIDTH = 8000
ADVISOR = re.compile(r"^mcp__ask_(\w+?)__")
BACKGROUNDED = re.compile(r"moved to the background as task (\w+)")


def revision_text(item: dict) -> str:
    text = (f"ccdirector: the director changed your item `{item['id']}` while you were "
            "working on it. What follows is now the whole item. Anything it no longer "
            "asks for does not apply: if a wait or a long run you started was only for "
            f"that, stop it.\n\n{item['what']}\n\nCheck: {item['check']}")
    return text[:NOTICE_WIDTH]


def dropped_text(item_id: str) -> str:
    return (f"ccdirector: the director dropped your item `{item_id}` from the plan. Stop "
            "work on it, including any wait or long run started for it, and run "
            "`ccdirector item next`.")


def notify(stdin=sys.stdin) -> int:
    try:
        stdin.read()
        root, worker = editlock.identity()
        change = work.take_change(root, worker)
        if change is None:
            return 0
        kind, value = change
        text = revision_text(value) if kind == "revised" else dropped_text(value)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "additionalContext": text}}))
    except Exception as exc:
        print(f"ccdirector: checking for item changes failed ({exc!r})", file=sys.stderr)
    return 0


def advisor_name(tool: str) -> str:
    found = ADVISOR.match(tool)
    return found.group(1) if found else tool


def consult(stdin=sys.stdin) -> int:
    try:
        payload = json.loads(stdin.read() or "{}")
        root = Path(os.path.realpath(os.environ["CCDIRECTOR_ROOT"]))
        if planmod.folder(root).is_dir():
            advisor = advisor_name(str(payload.get("tool_name", "")))
            by = os.environ.get("CCDIRECTOR_WORKER") or "director"
            moved = BACKGROUNDED.search(json.dumps(payload.get("tool_response"), default=str))
            if moved:
                progress.record_backgrounded(root, advisor, by, moved.group(1),
                                             str(payload.get("transcript_path", "")))
            else:
                progress.record_consult(root, advisor, by)
    except Exception as exc:
        print(f"ccdirector: recording the consult failed ({exc!r})", file=sys.stderr)
    return 0
