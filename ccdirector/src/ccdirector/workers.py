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

The pool holds one slot per allowed worker (`worker-1` .. `worker-N`) and keeps
as many of them running as there is work for: items in progress plus items
ready to hand out. Each slot is idle, starting, running or backing off:

- A worker whose run ends gives back any item it still held, and its slot is
  free to launch again.
- A worker that dies within a minute of starting did not get going. Its slot
  waits before relaunching, doubling the wait each time up to 15 minutes, and
  never stops trying.
- Three such quick deaths in a row across the pool mean a shared cause, such as
  the API, the network or a credential: an outage. During one, only one worker
  is tried at a time, on the same doubling wait. The first worker that stays up
  ends the outage, and every slot comes back at once.
- A worker whose log has been silent longer than the stall limit (`--worker-stall`,
  four times longer while a tool call is open) is hung. Its whole process tree
  is ended and the slot relaunches after the first wait.
- A worker whose MCP server has stopped touching its alive file for
  SERVER_SILENT_SECONDS is ended and relaunched the same way: its ccdirector
  tools no longer answer.

Every change of slot state is written to `state.json` and the ones the director
must know about to `events.jsonl`, so `progress` can show the pool.

The pool also runs the waits: a second thread tests the condition of every item
a worker parked until a result, once a minute (`work.wake_waiting`), so an item
is back in line the moment its result lands, with no director in the loop.
"""
from __future__ import annotations

import datetime
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import plan as planmod
from . import session, work
from .procs import signal_tree, tree

PROMPT_FILE = Path(__file__).parent / "prompts" / "worker.md"
EDIT_TOOLS = "Edit|Write|MultiEdit|NotebookEdit"
CRITERIA = (
    "`ccdirector item next` has printed NONE: nothing in the plan is left for you. "
    "`ccdirector item next` writes ccloop's marker itself when it prints NONE; never "
    "write the marker yourself."
)
TICK_SECONDS = 5
#: A worker that dies sooner than this after launch never got going.
QUICK_EXIT_SECONDS = 60
#: First wait before a slot relaunches after a quick death; it doubles each time.
BACKOFF_SECONDS = 15
#: The longest wait. There is no count after which a slot stops trying.
BACKOFF_CAP_SECONDS = 900
#: Quick deaths in a row across the pool that mean a shared cause.
OUTAGE_FAILURES = 3
#: Default `--worker-stall`: minutes of log silence before a worker counts as hung.
#: A waiting worker is never this quiet: ccloop's Stop hook re-feeds it every few
#: minutes while background work runs, and each re-feed is a line in its log.
STALL_MINUTES = 30
#: While a tool call is open the worker may legitimately be quiet much longer: an
#: advisor consult, a subagent, a blocking `ccdirector item next --wait`.
TOOL_STALL_FACTOR = 4
STOP_GRACE_SECONDS = 20
#: A worker whose MCP server has come up and then not touched its alive file for
#: this long has a frozen server: none of its ccdirector tools will answer. Ten
#: times the server's one-minute beat, and longer than ccloop's gap between one
#: session of a worker and the next.
SERVER_SILENT_SECONDS = 600
#: Bytes of the log tail read to tell whether a tool call is open.
TAIL_BYTES = 16384

IDLE, STARTING, RUNNING, BACKOFF = "idle", "starting", "running", "backoff"


@dataclass
class Options:
    count: int
    cutoff: int | None = None
    effort: str | None = None
    model: str | None = None
    stall: int = STALL_MINUTES     # minutes; 0 never ends a silent worker


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


def mcp_config_path(root: Path, name: str) -> Path:
    return planmod.folder(root) / "workers" / f"{name}.mcp.json"


def mcp_config(root: Path, name: str) -> dict:
    """The worker's own MCP server (mcpserver.py), given to its claude by --mcp-config.
    It carries the pool's PYTHONPATH, if any, so the server imports the same ccdirector
    as the pool that launched it."""
    env = {"CCDIRECTOR_ROOT": str(root), "CCDIRECTOR_WORKER": name, "CCDIRECTOR_ROLE": "worker"}
    if os.environ.get("PYTHONPATH"):
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    return {"mcpServers": {"ccdirector": {
        "command": sys.executable, "args": ["-m", "ccdirector.cli", "mcp"], "env": env}}}


def environment(root: Path, name: str) -> dict:
    env = dict(os.environ)
    for key in ("CLAUDECODE", "CCLOOP_RUN_ID", "CCLOOP_RUN_DIR", "CCLOOP_SETTINGS"):
        env.pop(key, None)
    env["CCLOOP_SETTINGS"] = settings(root)
    env["CCDIRECTOR_ROLE"] = "worker"
    env["CCDIRECTOR_WORKER"] = name
    env["CCDIRECTOR_ROOT"] = str(root)
    # A worker's quota is for its items. Every relaunch is a new session, and each
    # took ccmemory's compaction nudge and dispatched compactors on the project's
    # store; the director and ordinary sessions still compact it.
    env["CCMEMORY_NO_COMPACTION"] = "1"
    # ccloop passes these arguments to claude as they are, split on whitespace.
    extra = env.get("CCLOOP_CLAUDE_EXTRA_ARGS", "").strip()
    env["CCLOOP_CLAUDE_EXTRA_ARGS"] = " ".join(
        part for part in (extra, f"--mcp-config {mcp_config_path(root, name)}") if part)
    return env


def task(root: Path, name: str) -> str:
    return PROMPT_FILE.read_text().replace("{name}", name).replace("{root}", str(root))


def argv(root: Path, name: str, options: Options) -> list[str]:
    flags = session.mode_flags(True, options.model, options.cutoff, options.effort)
    return [session.ccloop(), *flags, CRITERIA, task(root, name)]


def log_path(root: Path, name: str) -> Path:
    return planmod.folder(root) / "workers" / f"{name}.log"


def beat_path(root: Path, name: str) -> Path:
    """Touched by the worker's MCP server while one of its tools blocks."""
    return planmod.folder(root) / "workers" / f"{name}.beat"


def alive_path(root: Path, name: str) -> Path:
    """Touched by the worker's MCP server every minute it is running at all."""
    return planmod.folder(root) / "workers" / f"{name}.alive"


def launch(root: Path, name: str, options: Options) -> subprocess.Popen:
    log = log_path(root, name)
    log.parent.mkdir(parents=True, exist_ok=True)
    planmod.atomic(mcp_config_path(root, name), json.dumps(mcp_config(root, name), indent=2))
    with open(log, "a") as out:
        out.write(f"\n===== {work.now()} {name} starting =====\n")
        out.flush()
        return subprocess.Popen(argv(root, name, options), cwd=str(root),
                                env=environment(root, name), stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT, start_new_session=True)


def stamp(when: float) -> str:
    return datetime.datetime.fromtimestamp(when, datetime.timezone.utc).isoformat(
        timespec="seconds")


def backoff(failures: int) -> int:
    """Seconds to wait after the `failures`-th quick death in a row."""
    return min(BACKOFF_SECONDS * 2 ** min(max(0, failures - 1), 20), BACKOFF_CAP_SECONDS)


def tail(log: Path) -> list[str]:
    try:
        with open(log, "rb") as handle:
            handle.seek(max(0, log.stat().st_size - TAIL_BYTES))
            return handle.read().decode(errors="replace").splitlines()
    except OSError:
        return []


def tool_open(lines: list[str]) -> bool:
    """Whether the log ends inside a tool call: ccloop writes `→ Tool …` for each call
    and an indented `ok N lines` or `ERROR: …` for each result."""
    calls = 0
    for line in lines:
        if line.startswith("→ "):
            calls += 1
        elif line.startswith(("  ok ", "  ERROR: ")):
            calls = max(0, calls - 1)
        elif line.startswith(("[ccloop] ── session", "===== ")):
            calls = 0
    return calls > 0


@dataclass
class Slot:
    name: str
    proc: subprocess.Popen | None = None
    started: float = 0.0
    state: str = IDLE
    failures: int = 0          # quick deaths in a row in this slot
    retry_at: float = 0.0      # backing off until then
    launches: int = 0
    restarts: int = 0          # launches after the first
    stall: str = ""            # why the pool is ending this run, while it is
    stall_at: float = 0.0
    victims: list = field(default_factory=list)


class Pool:
    def __init__(self, root: Path, options: Options):
        self.root = root
        self.options = options
        self.slots = {f"worker-{n}": Slot(f"worker-{n}")
                      for n in range(1, options.count + 1)}
        self.failures = 0          # quick deaths in a row across the pool
        self.outage = False
        self.retry_at = 0.0        # during an outage, when the next worker is tried
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.run, name="ccdirector-pool", daemon=True)
        self.waker = threading.Thread(target=self.wake, name="ccdirector-waits", daemon=True)

    def start(self) -> None:
        for slot in self.slots.values():
            self.record(slot, exit=None, pid=None)
        work.set_pool(self.root, outage=False, failures=0, retry_at=None)
        self.thread.start()
        self.waker.start()

    def run(self) -> None:
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception as exc:       # keep going; the director sees the event
                with work.locked(self.root):
                    work.event(self.root, "supervisor_error", error=repr(exc))
            self.stopping.wait(TICK_SECONDS)

    def wake(self) -> None:
        """Test waiting items' conditions apart from the supervisor, so a slow
        condition never delays noticing a dead or hung worker."""
        while not self.stopping.is_set():
            try:
                work.wake_waiting(self.root)
            except Exception as exc:       # keep going; the director sees the event
                with work.locked(self.root):
                    work.event(self.root, "supervisor_error", error=repr(exc), part="waits")
            self.stopping.wait(work.UNTIL_CHECK_SECONDS)

    def record(self, slot: Slot, **fields) -> None:
        work.set_worker(self.root, slot.name, running=slot.proc is not None,
                        state=slot.state, failures=slot.failures, restarts=slot.restarts,
                        retry_at=stamp(slot.retry_at) if slot.state == BACKOFF else None,
                        **fields)

    def event(self, kind: str, **fields) -> None:
        with work.locked(self.root):
            work.event(self.root, kind, **fields)

    def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.collect(now)
        if not self.stopping.is_set():
            self.launch(now)

    def collect(self, now: float) -> None:
        for slot in self.slots.values():
            if slot.proc is None:
                continue
            code = slot.proc.poll()
            if code is None:
                self.watch(slot, now)
            else:
                self.ended(slot, code, now)

    def watch(self, slot: Slot, now: float) -> None:
        if slot.stall:
            if now - slot.stall_at >= STOP_GRACE_SECONDS:
                signal_tree(slot.victims, signal.SIGKILL)
            return
        if slot.state == STARTING and now - slot.started >= QUICK_EXIT_SECONDS:
            slot.state, slot.failures = RUNNING, 0
            self.record(slot)
            self.recovered(slot)
        reason = self.stalled(slot, now)
        if reason:
            slot.stall, slot.stall_at = reason, now
            slot.victims = tree(slot.proc.pid)
            signal_tree(slot.victims, signal.SIGTERM)
            self.event("worker_stalled", worker=slot.name, reason=reason,
                       log=str(log_path(self.root, slot.name)))

    def frozen(self, slot: Slot, now: float) -> str:
        """Why this worker's MCP server counts as frozen, or "". The server touches its
        alive file every minute from its only thread; once it has come up in this
        launch, a file that stops moving means the server has stopped answering, and
        the worker has lost every ccdirector tool. In one project a server sat in an
        NFS open for hours: its `acquire` and then an instant `resources` call were
        each aborted by Claude Code after 30 minutes of silence, and its queued request
        stayed in line. Relaunching the worker gives it a working server."""
        try:
            seen = alive_path(self.root, slot.name).stat().st_mtime
        except OSError:
            return ""
        if seen < slot.started:
            return ""                  # its server has not come up in this launch yet
        silent = now - seen
        if silent < SERVER_SILENT_SECONDS:
            return ""
        return (f"its ccdirector MCP server has not run for {int(silent // 60)} minutes, so "
                "none of its ccdirector tools answer")

    def stalled(self, slot: Slot, now: float) -> str:
        """Why this worker counts as hung, or "" while it does not. A frozen MCP server
        counts whatever --worker-stall says: that limit is about a silent worker, and a
        worker without its tools cannot finish its item."""
        frozen = self.frozen(slot, now)
        if frozen:
            return frozen
        if not self.options.stall:
            return ""
        limit = self.options.stall * 60
        log = log_path(self.root, slot.name)
        # A worker blocked in one of its ccdirector tools (waiting in line for the
        # rig, a long run) writes nothing to its log, but its MCP server touches
        # the beat file every minute: that is waiting, not hanging.
        seen = slot.started
        for marker in (log, beat_path(self.root, slot.name)):
            try:
                seen = max(seen, marker.stat().st_mtime)
            except OSError:
                pass
        quiet = now - seen
        if quiet < limit:
            return ""
        calling = tool_open(tail(log))
        if calling and quiet < limit * TOOL_STALL_FACTOR:
            return ""
        return f"no output for {int(quiet // 60)} minutes" + (
            " with a tool call open" if calling else "")

    def recovered(self, slot: Slot) -> None:
        """A worker got going, so whatever killed the ones before it has cleared."""
        self.failures = 0
        if not self.outage:
            return
        self.outage = False
        for other in self.slots.values():
            if other.state == BACKOFF:
                other.state, other.failures, other.retry_at = IDLE, 0, 0.0
                self.record(other)
        work.set_pool(self.root, outage=False, failures=0, retry_at=None)
        self.event("workers_resumed", worker=slot.name)

    def ended(self, slot: Slot, code: int, now: float) -> None:
        lived = now - slot.started
        stalled = slot.stall
        if stalled:                        # claude and its tools can outlive ccloop
            signal_tree(slot.victims, signal.SIGKILL)
        quick = code != 0 and lived < QUICK_EXIT_SECONDS
        failed = not self.stopping.is_set() and (bool(stalled) or quick)
        slot.proc, slot.stall, slot.victims = None, "", []
        released = work.release_worker(self.root, slot.name)
        if failed:
            slot.failures += 1
            slot.state, slot.retry_at = BACKOFF, now + backoff(slot.failures)
        else:
            slot.state, slot.failures, slot.retry_at = IDLE, 0, 0.0
        self.record(slot, exit=code, ended=work.now())
        extra = {"stalled": stalled} if stalled else {}
        if failed:
            extra["retry_at"] = stamp(slot.retry_at)
        self.event("worker_exited", worker=slot.name, exit=code, released=released,
                   ran=f"{int(lived)}s", log=str(log_path(self.root, slot.name)), **extra)
        if self.stopping.is_set() or stalled:
            return
        if not failed:
            self.recovered(slot)
            return
        self.failures += 1
        self.retry_at = now + backoff(self.failures)
        if self.failures < OUTAGE_FAILURES:
            return
        work.set_pool(self.root, outage=True, failures=self.failures,
                      retry_at=stamp(self.retry_at))
        if not self.outage:
            self.outage = True
            self.event("workers_failing", failures=self.failures,
                       retry_at=stamp(self.retry_at),
                       detail=f"{self.failures} workers in a row died within "
                              f"{QUICK_EXIT_SECONDS}s of starting, so the cause is probably "
                              "shared (API, network, credential). ccdirector tries one worker "
                              f"at a time, waiting up to {BACKOFF_CAP_SECONDS // 60} minutes "
                              "between tries, and brings every worker back once one stays up. "
                              "Read the worker logs only to rule out a problem with the plan.")

    def wanted(self) -> int:
        current = planmod.load(self.root)
        if current is None:
            return 0
        state = work.load(self.root)
        tally = work.counts(state, current)
        return min(self.options.count, tally[work.CLAIMED] + len(work.ready(state, current)))

    def launch(self, now: float) -> None:
        wanted = self.wanted()
        live = sum(1 for slot in self.slots.values() if slot.proc is not None)
        if self.outage:
            if now < self.retry_at or any(s.state == STARTING for s in self.slots.values()):
                return
            wanted = min(wanted, live + 1)
        for slot in self.slots.values():
            if live >= wanted or self.stopping.is_set():
                return
            if slot.proc is not None or (not self.outage and now < slot.retry_at):
                continue
            self.spawn(slot, now)
            live += 1

    def spawn(self, slot: Slot, now: float) -> None:
        slot.proc = launch(self.root, slot.name, self.options)
        slot.started, slot.state = now, STARTING
        if slot.launches:
            slot.restarts += 1
        slot.launches += 1
        self.record(slot, pid=slot.proc.pid, started=work.now())
        self.event("worker_started", worker=slot.name, pid=slot.proc.pid,
                   **({"restart": slot.restarts} if slot.restarts else {}))

    def stop(self) -> None:
        """Stop launching and end every worker with every process under it."""
        self.stopping.set()
        if self.thread.is_alive():
            self.thread.join(timeout=TICK_SECONDS + 30)
        if self.waker.is_alive():
            self.waker.join(timeout=work.UNTIL_TEST_SECONDS + 5)
        running = [slot for slot in self.slots.values() if slot.proc is not None]
        victims = {slot.name: tree(slot.proc.pid) for slot in running}
        for slot in running:
            signal_tree(victims[slot.name], signal.SIGTERM)
        end = time.time() + STOP_GRACE_SECONDS
        for slot in running:
            try:
                slot.proc.wait(timeout=max(0.1, end - time.time()))
            except subprocess.TimeoutExpired:
                pass
        for slot in running:
            signal_tree(victims[slot.name], signal.SIGKILL)
            slot.proc.wait()
        self.collect(time.time())
