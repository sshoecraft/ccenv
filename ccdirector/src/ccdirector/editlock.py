"""One file update at a time across the workers in one tree.

Claude Code itself refuses a Write to a file changed since the session read it,
and applies an Edit on top of the other change with a note
(tests/probe_stale_edit.py measures this). What it cannot do is make its own
check-then-write atomic against another process doing the same at the same
instant. This lock does: each worker's PreToolUse hook takes the file's lease
before the tool runs and its PostToolUse hook releases it after.

A lease is a file under `.ccdirector/locks/`, created with O_EXCL (atomic
on a local filesystem and on NFSv4), holding the owner and the time. It is
held for one tool call, never for a task, so any number of workers can work in
the same file; their writes go one at a time.

A tool call that fails never reaches PostToolUse, so a lease older than
LEASE_SECONDS is taken as abandoned and broken. The same worker taking a lease
it already holds refreshes it.

The PreToolUse hook fails closed: anything unexpected refuses the edit (exit 2)
instead of letting it through unlocked.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

from . import plan as planmod

LEASE_SECONDS = 20
WAIT_SECONDS = 45          # under Claude Code's 60 s hook timeout
POLL_SECONDS = 0.05


def folder(root: Path) -> Path:
    return planmod.folder(root) / "locks"


def lease_path(root: Path, target: Path) -> Path:
    return folder(root) / (hashlib.sha1(str(target).encode()).hexdigest() + ".lock")


def trace(root: Path, owner: str, op: str, target: Path, detail: str = "") -> None:
    line = json.dumps({"time": time.time(), "owner": owner, "op": op,
                       "path": str(target), "detail": detail})
    with open(planmod.folder(root) / "locks.log", "a") as out:
        out.write(line + "\n")


def read_lease(where: Path) -> dict | None:
    try:
        return json.loads(where.read_text())
    except (OSError, ValueError):
        return None


def acquire(root: Path, target: Path, owner: str, wait: float = WAIT_SECONDS) -> tuple[bool, str]:
    """Take `target`'s lease for `owner`, waiting up to `wait` seconds.
    (True, "") or (False, the current holder)."""
    folder(root).mkdir(parents=True, exist_ok=True)
    where = lease_path(root, target)
    body = json.dumps({"owner": owner, "pid": os.getppid(), "time": time.time(),
                       "path": str(target)})
    end = time.time() + wait
    waited = False
    while True:
        try:
            handle = os.open(str(where), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            current = read_lease(where)
            if current is None:
                # Being written or removed this instant; look again.
                time.sleep(POLL_SECONDS)
                continue
            if current.get("owner") == owner:
                where.write_text(body)
                trace(root, owner, "refresh", target)
                return True, ""
            if time.time() - float(current.get("time", 0)) > LEASE_SECONDS:
                if read_lease(where) == current:
                    try:
                        where.unlink()
                    except FileNotFoundError:
                        pass
                    trace(root, owner, "break", target, f"abandoned by {current.get('owner')}")
                continue
            if time.time() >= end:
                trace(root, owner, "refused", target, f"held by {current.get('owner')}")
                return False, str(current.get("owner"))
            if not waited:
                trace(root, owner, "wait", target, f"held by {current.get('owner')}")
                waited = True
            time.sleep(POLL_SECONDS)
            continue
        with os.fdopen(handle, "w") as out:
            out.write(body)
        trace(root, owner, "acquire", target)
        return True, ""


def release(root: Path, target: Path, owner: str) -> bool:
    where = lease_path(root, target)
    current = read_lease(where)
    if current is None or current.get("owner") != owner:
        return False
    try:
        where.unlink()
    except FileNotFoundError:
        return False
    trace(root, owner, "release", target)
    return True


def targets(payload: dict, root: Path) -> list[Path]:
    """The files under `root` a file-editing tool call would write."""
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return []
    target = Path(raw)
    if not target.is_absolute():
        target = Path(payload.get("cwd") or root) / target
    target = Path(os.path.realpath(target))
    try:
        target.relative_to(root)
    except ValueError:
        return []
    return [target]


def hook(stage: str, stdin=sys.stdin) -> int:
    """`ccdirector hook pre|post`: Claude Code's PreToolUse/PostToolUse for a worker.
    Exit 0 lets the tool run; exit 2 refuses it with stderr shown to the model."""
    if stage == "pre":
        try:
            return pre(json.loads(stdin.read() or "{}"))
        except Exception as exc:       # fail closed: an unlocked edit is the failure
            print(f"ccdirector: the edit lock failed ({exc!r}); edit refused. "
                  "Try again; if it keeps failing, park the item.", file=sys.stderr)
            return 2
    try:
        return post(json.loads(stdin.read() or "{}"))
    except Exception as exc:
        print(f"ccdirector: releasing the edit lock failed ({exc!r}); it expires in "
              f"{LEASE_SECONDS}s", file=sys.stderr)
        return 0


def identity() -> tuple[Path, str]:
    root = os.environ.get("CCDIRECTOR_ROOT", "")
    owner = os.environ.get("CCDIRECTOR_WORKER", "")
    if not root or not owner:
        raise RuntimeError("CCDIRECTOR_ROOT and CCDIRECTOR_WORKER must be set for a worker")
    return Path(os.path.realpath(root)), owner


def pre(payload: dict) -> int:
    root, owner = identity()
    for target in targets(payload, root):
        ok, holder = acquire(root, target, owner, wait=WAIT_SECONDS)
        if not ok:
            print(f"ccdirector: {target.relative_to(root)} is being written by {holder} "
                  f"and stayed locked for {WAIT_SECONDS}s. Read the file again, then retry "
                  "the edit.", file=sys.stderr)
            return 2
    return 0


def post(payload: dict) -> int:
    root, owner = identity()
    for target in targets(payload, root):
        release(root, target, owner)
    return 0
