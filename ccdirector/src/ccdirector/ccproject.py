"""The project's criteria board and defect queue, through their own tools.

ccdirector never reads the board or the queue itself. The tools own the format,
apply the project's driver (`.ccproject/driver.py`) and decide what blocks; a
second reader here would drift from them. Everything goes through
`ccproject_criteria.py` and `ccproject_defects.py` on PATH (installed by ccenv).
The one thing the tools cannot say is whether a project keeps a queue that is
now empty or keeps none (both list no records and pass `--gate`), so
`queue_file` locates the queue the way the tools do and tests only that it
exists.

Each tool finds the project by walking up from its cwd to the nearest existing
`.ccproject/`, so every call runs with cwd = the project root.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

CRITERIA_TOOL = "ccproject_criteria.py"
DEFECTS_TOOL = "ccproject_defects.py"
STORE = ".ccproject"
TOOL_TIMEOUT = 120


class ToolError(RuntimeError):
    """A ccproject tool is missing, failed, or printed something unreadable."""


def store(root: Path) -> Path | None:
    """The `.ccproject/` the tools would use from `root`, or None if there is none.
    Same walk as the tools: nearest existing one at or above `root`."""
    for folder in [root, *root.parents]:
        candidate = folder / STORE
        if candidate.is_dir():
            return candidate
    return None


def queue_file(root: Path) -> Path | None:
    """Where the tools keep the defect queue: the file `.ccproject/config.json`
    names under `store.defects` (relative to the project root), else
    `.ccproject/defects.json` — the same resolution as the tools' `data_path`."""
    where = store(root)
    if where is None:
        return None
    named = None
    config = where / "config.json"
    if config.is_file():
        try:
            data = json.loads(config.read_text())
            named = (data.get("store") or {}).get("defects") if isinstance(data, dict) else None
        except (OSError, ValueError, AttributeError):
            named = None
    return (where.parent / named) if isinstance(named, str) and named else where / "defects.json"


def tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ToolError(f"{name} is not on PATH; install ccenv's ccproject")
    return path


def run(name: str, args: list[str], root: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([tool(name), *args], cwd=str(root), capture_output=True,
                              text=True, timeout=TOOL_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"{name} {' '.join(args)} took longer than {TOOL_TIMEOUT}s") from exc


def read_json(name: str, root: Path) -> object:
    done = run(name, ["--json"], root)
    if done.returncode != 0:
        raise ToolError(f"{name} --json exited {done.returncode}: "
                        f"{(done.stderr or done.stdout).strip()[:500]}")
    try:
        return json.loads(done.stdout)
    except ValueError as exc:
        raise ToolError(f"{name} --json printed something that is not JSON: {exc}") from exc


#: Cap on each tool's text output in the goal-finder's prompt. mxfs's board runs
#: to thousands of cells; the cap keeps one project's records from crowding out
#: the prompt, and the cut is marked so the model knows the list goes on.
TEXT_CAP = 60_000


def read_text(name: str, args: list[str], root: Path) -> str:
    """A tool's own human-readable view (colour is off when stdout is a pipe)."""
    done = run(name, args, root)
    if done.returncode not in (0, 1):
        raise ToolError(f"{name} {' '.join(args)} exited {done.returncode}: "
                        f"{(done.stderr or done.stdout).strip()[:500]}")
    text = done.stdout.strip()
    if len(text) > TEXT_CAP:
        text = (text[:TEXT_CAP].rstrip()
                + f"\n… (cut at {TEXT_CAP} characters; run `{name} {' '.join(args)}` "
                  "for the rest)")
    return text


def rows(name: str, key: str, root: Path) -> list:
    """The tool's own records (`--json`), wherever the project keeps them: a
    project's `.ccproject/config.json` can put its files anywhere in the tree, so
    whether there is a board or a queue is the tool's answer, never a file test."""
    data = read_json(name, root)
    found = data.get(key, []) if isinstance(data, dict) else []
    return [r for r in found if isinstance(r, dict)] if isinstance(found, list) else []


@dataclass
class Snapshot:
    """What the project's own records say: text for the goal-finder's prompt,
    ids for ccdirector to check the goal-finder's answer against."""

    store: Path | None
    board_text: str = ""        # ccproject_criteria.py -v, when the board has rows
    queue_text: str = ""        # ccproject_defects.py -d, when the queue has records
    board_ids: frozenset = frozenset()
    queue_size: int = 0

    @property
    def has_board(self) -> bool:
        return bool(self.board_ids)

    @property
    def has_queue(self) -> bool:
        return self.queue_size > 0


def snapshot(root: Path) -> Snapshot:
    shot = Snapshot(store=store(root))
    if shot.store is None:
        return shot
    shot.board_ids = frozenset(str(r.get("id")) for r in rows(CRITERIA_TOOL, "criteria", root))
    shot.queue_size = len(rows(DEFECTS_TOOL, "defects", root))
    if shot.has_board:
        shot.board_text = read_text(CRITERIA_TOOL, ["-v"], root)
    if shot.has_queue:
        shot.queue_text = read_text(DEFECTS_TOOL, ["-d"], root)
    return shot


@dataclass
class Verdict:
    """The done-check: did the project's own gate pass?"""

    done: bool
    gate: str               # the command that decided it
    output: str             # what it printed, fed back to the director when not done


def gate_command(root: Path, build: str | None = None) -> list[str] | None:
    """The command that decides done for this project, or None when it keeps
    neither a board nor a queue. With a board, the criteria gate is the whole
    release check (it counts the queue as one criterion). A queue alone is
    judged by the defects gate: the criteria gate is never met with no rows."""
    if store(root) is None:
        return None
    if rows(CRITERIA_TOOL, "criteria", root):
        return [CRITERIA_TOOL, "--gate"] + (["--build", build] if build else [])
    queue = queue_file(root)
    if (queue is not None and queue.is_file()) or rows(DEFECTS_TOOL, "defects", root):
        return [DEFECTS_TOOL, "--gate"]
    return None


def check(root: Path, build: str | None = None) -> Verdict:
    cmd = gate_command(root, build)
    if cmd is None:
        return Verdict(done=False, gate="",
                       output="the project's criteria board and defect queue are both empty "
                              "or absent: nothing defines done for this project")
    done = run(cmd[0], cmd[1:], root)
    if done.returncode not in (0, 1):
        raise ToolError(f"{' '.join(cmd)} exited {done.returncode}: "
                        f"{(done.stderr or done.stdout).strip()[:500]}")
    return Verdict(done=done.returncode == 0, gate=" ".join(cmd),
                   output=(done.stdout + done.stderr).strip())
