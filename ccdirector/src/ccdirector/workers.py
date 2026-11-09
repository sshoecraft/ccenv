"""Worker sessions: launched, watched and stopped by `ccdirector start`.

Each worker is one headless ccloop run in the project root, so it is relayed to
a fresh session when its context fills like any other ccloop run. Its output
goes to `.ccdirector/workers/<name>.log`.

What a worker gets, all through ccloop's existing configuration:
- its instructions (prompts/worker.md) as the ccloop task, and "`ccdirector
  item next` printed NONE" as its criteria; `item next` writes the marker;
- `CCLOOP_SETTINGS`: deny rules on `.ccdirector/**` and `.ccloop/**` (the
  coordination state is ccdirector's), the PreToolUse/PostToolUse hooks that
  take and release the per-file edit lock (editlock.py), and the hooks that tell
  it its item changed and record its consults (hooks.py);
- `CCDIRECTOR_ROLE=worker`, `CCDIRECTOR_WORKER=<name>`, `CCDIRECTOR_ROOT=<project root>`;
- its own `--cutoff`, `--effort` and `--model`, separate from the director's.

The supervisor keeps as many workers running as there is work for, up to the
limit: items in progress plus items ready to hand out. A worker whose run ends
gives back any item it still held. Three workers in a row that exit nonzero
within a minute of starting stop further launches, with an event telling the
director, instead of relaunching forever.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import plan as planmod
from . import session, work

PROMPT_FILE = Path(__file__).parent / "prompts" / "worker.md"
EDIT_TOOLS = "Edit|Write|MultiEdit|NotebookEdit"
CRITERIA = (
    "`ccdirector item next` has printed NONE: nothing in the plan is left for you. "
    "`ccdirector item next` writes ccloop's marker itself when it prints NONE; never "
    "write the marker yourself."
)
TICK_SECONDS = 5
QUICK_EXIT_SECONDS = 60
FAILURE_LIMIT = 3
STOP_GRACE_SECONDS = 20


@dataclass
class Options:
    count: int
    cutoff: int | None = None
    effort: str | None = None
    model: str | None = None


def settings(root: Path) -> str:
    base = "//" + str(root).lstrip("/")
    hook = session.hook_command()
    return json.dumps({
        "permissions": {"deny": [f"Edit({base}/.ccdirector/**)", f"Edit({base}/.ccloop/**)"]},
        "hooks": {
            "PreToolUse": [
                {"matcher": EDIT_TOOLS, "hooks": [
                    {"type": "command", "command": f"{hook} pre", "timeout": 60}]},
                {"matcher": "*", "hooks": [
                    {"type": "command", "command": f"{hook} notify", "timeout": 30}]}],
            "PostToolUse": [
                {"matcher": EDIT_TOOLS, "hooks": [
                    {"type": "command", "command": f"{hook} post", "timeout": 30}]},
                session.consult_hook()],
        },
    })


def environment(root: Path, name: str) -> dict:
    env = dict(os.environ)
    for key in ("CLAUDECODE", "CCLOOP_RUN_ID", "CCLOOP_RUN_DIR", "CCLOOP_SETTINGS"):
        env.pop(key, None)
    env["CCLOOP_SETTINGS"] = settings(root)
    env["CCDIRECTOR_ROLE"] = "worker"
    env["CCDIRECTOR_WORKER"] = name
    env["CCDIRECTOR_ROOT"] = str(root)
    return env


def task(root: Path, name: str) -> str:
    return PROMPT_FILE.read_text().replace("{name}", name).replace("{root}", str(root))


def argv(root: Path, name: str, options: Options) -> list[str]:
    flags = session.mode_flags(True, options.model, options.cutoff, options.effort)
    return [session.ccloop(), *flags, CRITERIA, task(root, name)]


@dataclass
class Worker:
    name: str
    proc: subprocess.Popen
    started: float


def launch(root: Path, name: str, options: Options) -> Worker:
    logs = planmod.folder(root) / "workers"
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / f"{name}.log", "a") as log:
        log.write(f"\n===== {work.now()} {name} starting =====\n")
        log.flush()
        proc = subprocess.Popen(argv(root, name, options), cwd=str(root),
                                env=environment(root, name), stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return Worker(name=name, proc=proc, started=time.time())


class Supervisor:
    def __init__(self, root: Path, options: Options):
        self.root = root
        self.options = options
        self.live: dict[str, Worker] = {}
        self.failures = 0
        self.halted = False
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.run, name="ccdirector-supervisor",
                                       daemon=True)

    def start(self) -> None:
        self.thread.start()

    def run(self) -> None:
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception as exc:       # keep supervising; the director sees the event
                with work.locked(self.root):
                    work.event(self.root, "supervisor_error", error=repr(exc))
            self.stopping.wait(TICK_SECONDS)

    def reap(self) -> None:
        for name, worker in list(self.live.items()):
            code = worker.proc.poll()
            if code is None:
                continue
            del self.live[name]
            released = work.release_worker(self.root, name)
            work.set_worker(self.root, name, running=False, exit=code, ended=work.now())
            quick = time.time() - worker.started < QUICK_EXIT_SECONDS
            self.failures = self.failures + 1 if (code != 0 and quick) else 0
            with work.locked(self.root):
                work.event(self.root, "worker_exited", worker=name, exit=code,
                           released=released, log=str(planmod.folder(self.root) / "workers"
                                                      / f"{name}.log"))
                if self.failures >= FAILURE_LIMIT and not self.halted:
                    self.halted = True
                    work.event(self.root, "workers_failing",
                               detail=f"{self.failures} workers in a row exited nonzero within "
                                      f"{QUICK_EXIT_SECONDS}s; no more are launched. Read the "
                                      "worker logs.")

    def wanted(self) -> int:
        current = planmod.load(self.root)
        if current is None:
            return 0
        state = work.load(self.root)
        tally = work.counts(state, current)
        return min(self.options.count, tally[work.CLAIMED] + len(work.ready(state, current)))

    def tick(self) -> None:
        self.reap()
        if self.halted:
            return
        target = self.wanted()
        while len(self.live) < target and not self.stopping.is_set():
            name = next(f"worker-{n}" for n in range(1, self.options.count + 1)
                        if f"worker-{n}" not in self.live)
            worker = launch(self.root, name, self.options)
            self.live[name] = worker
            work.set_worker(self.root, name, running=True, pid=worker.proc.pid,
                            started=work.now())
            with work.locked(self.root):
                work.event(self.root, "worker_started", worker=name, pid=worker.proc.pid)

    def stop(self) -> None:
        """Stop supervising and end every worker (its whole process group)."""
        self.stopping.set()
        if self.thread.is_alive():
            self.thread.join(timeout=TICK_SECONDS + 30)
        for worker in self.live.values():
            try:
                os.killpg(worker.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        end = time.time() + STOP_GRACE_SECONDS
        for worker in self.live.values():
            try:
                worker.proc.wait(timeout=max(0.1, end - time.time()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(worker.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                worker.proc.wait()
        self.reap()
