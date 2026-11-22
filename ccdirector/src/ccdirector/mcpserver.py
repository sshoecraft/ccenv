"""The worker's MCP server: waits that cost nothing.

A worker that waits through Bash pays for it: a Bash call ends within 10 minutes,
so a long wait becomes a background command and ccloop's Stop hook re-feeds the
session every few seconds to minutes, each re-feed a full request. An MCP tool
call that does not return keeps the session suspended in place instead — no
turns, no tokens — for as long as it takes. Each worker session runs this server
(`ccdirector mcp`, passed to its claude by `--mcp-config`), with tools that block:

- `item_next`: until an item is ready for this worker, or nothing is left.
- `item_done`: runs the item's check to the end, however long it takes, and
  records the result. An item is a whole goal and its check proves the goal, so
  it may be a long test; `ccdirector item done` in Bash would be cut at 10
  minutes.
- `acquire`: until the units of a shared resource it asks for are free, in order,
  except that a request that fits may go ahead of a larger one that does not;
  returns a lease.
- `release`: gives a lease back.
- `run`: runs a command (a test lap, a long build) to the end, however long it
  takes, and returns its exit code and the tail of its output.
- `resources`: who holds and who waits for each shared resource.

Every blocking tool reports progress each PROGRESS_SECONDS, well inside Claude
Code's 1800 s abort of a silent call, and `acquire`, `run` and `item_done` stop
when their call is cancelled or the session ends: a cancelled `acquire` leaves
the queue, a cancelled `run` or `item_done` ends the command's whole process
tree (a cancelled check records no result). Worker sessions are
headless, which Claude Code exempts from moving a long MCP call to the
background.

A lease belongs to the worker's claude process (this server's parent): when that
process is gone, the lease is dropped at the next acquire by anyone, and the pool
drops it when it sees the worker exit.
"""
from __future__ import annotations

import asyncio
import datetime
import os
import signal
import time
import uuid
from pathlib import Path

from ccenvmcp import FastMCP

from . import plan as planmod
from . import procs, progress, work
from .workers import alive_path, beat_path

PROGRESS_SECONDS = 60
POLL_SECONDS = 2
MINUTE = 60
TAIL_LINES = 80
TAIL_BYTES = 16384

INSTRUCTIONS = """ccdirector's tools for this worker. They block until their answer is \
ready and cost nothing while they wait, so never poll, sleep or background a wait.
- item_next: your next item (use it instead of `ccdirector item next`).
- item_done(id): runs your item's check to the end and records it: PASSED, or FAILED with \
the output (the item stays yours: keep working). Use it instead of `ccdirector item done`.
- acquire(resource, units, why) before you touch a shared resource named in the plan's \
resources; release(lease) as soon as you are done with it. Do not hold a lease while you \
edit code or think: others are waiting.
- run(command): anything that may take more than a few minutes (a test lap, a long build, a \
soak). It returns when the command ends, with its exit code and output tail.
- resources: who holds and who waits for each resource."""


def stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")


def tail(path: Path) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(max(0, path.stat().st_size - TAIL_BYTES))
            lines = handle.read().decode(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-TAIL_LINES:])


def build(root: Path, worker: str) -> FastMCP:
    app = FastMCP("ccdirector", instructions=INSTRUCTIONS)
    holder = os.getppid()                       # the worker's claude process
    holder_start = procs.identity(holder)

    beat = beat_path(root, worker)

    async def heartbeat(ctx, started: float, last: float, total=None, message="") -> float:
        """Each PROGRESS_SECONDS: progress to the client, and the beat file, which
        tells the pool this worker is waiting here, not hung."""
        now = time.time()
        if now - last < PROGRESS_SECONDS:
            return last
        try:
            beat.parent.mkdir(parents=True, exist_ok=True)
            beat.touch()
        except OSError:
            pass
        if ctx is not None:
            await ctx.report_progress(round(now - started), total=total, message=message)
        return now

    @app.tool(cancellable=True)
    async def item_next(ctx=None) -> str:
        """Your next item: blocks until one is ready for you (ITEM and the item), or
        until nothing is left for you (NONE, which ends your run)."""
        from .cli import item_report
        started = last = time.time()
        while True:
            kind, value = work.next_item(root, worker)
            if kind != "wait":
                return item_report(root, kind, value)
            await asyncio.sleep(POLL_SECONDS)
            last = await heartbeat(ctx, started, last, message=f"waiting: {value}")

    @app.tool(cancellable=True)
    async def acquire(resource: str, units: int = 1, why: str = "", ctx=None) -> dict:
        """Take `units` of a shared resource from the plan's resources registry. Blocks,
        at no cost, until they are free; requests are served in order, except that one
        that fits in the free units may go ahead of a larger one that does not.
        Returns the lease to pass to release()."""
        known = work.registry(planmod.load(root))
        if resource not in known:
            raise ValueError(f"{resource!r} is not in the plan's resources; known: "
                             + (", ".join(sorted(known)) or "none"))
        capacity = known[resource]["capacity"]
        if not isinstance(units, int) or not 1 <= units <= capacity:
            raise ValueError(f"units must be 1 to {capacity} for {resource}")
        ticket = f"{worker}-{uuid.uuid4().hex[:8]}"
        started = last = time.time()
        granted = None
        try:
            while granted is None:
                granted = work.acquire(root, worker, ticket, resource, units, why,
                                       holder, holder_start)
                if granted is None:
                    await asyncio.sleep(POLL_SECONDS)
                    last = await heartbeat(ctx, started, last,
                                           message=f"waiting for {units} of {resource}")
        finally:
            if granted is None:
                work.withdraw(root, ticket)
        return {"lease": granted["lease"], "resource": resource, "units": units,
                "waited_seconds": round(time.time() - started)}

    @app.tool()
    def release(lease: str) -> dict:
        """Give back a lease from acquire()."""
        dropped = work.release(root, worker, lease)
        if dropped is None:
            raise ValueError(f"no lease {lease!r} held by {worker}")
        return {"released": lease, "resource": dropped["resource"], "units": dropped["units"]}

    async def run_to_end(command: str, limit: float | None, label: str, ctx) -> dict:
        """Run a shell command from the project root to its end (or `limit` seconds),
        output to a log under .ccdirector/runs/, reporting progress while it runs.
        A cancelled call ends the command's whole process tree."""
        logs = planmod.folder(root) / "runs"
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / f"{worker}-{label}-{stamp()}.log"
        started = last = time.time()
        with open(log, "wb") as out:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", command, cwd=str(root), stdin=asyncio.subprocess.DEVNULL,
                stdout=out, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        timed_out = False
        try:
            while True:
                try:
                    await asyncio.wait_for(proc.wait(), POLL_SECONDS)
                    break
                except asyncio.TimeoutError:
                    pass
                if limit and time.time() - started >= limit:
                    timed_out = True
                    end_tree(proc.pid)
                    await proc.wait()
                    break
                last = await heartbeat(ctx, started, last, total=limit,
                                       message=f"{label}: {command[:60]}")
        except asyncio.CancelledError:
            end_tree(proc.pid)
            raise
        return {"exit": proc.returncode, "seconds": round(time.time() - started),
                "timed_out": timed_out, "log": str(log), "tail": tail(log)}

    @app.tool(cancellable=True)
    async def run(command: str, timeout_minutes: int = 0, ctx=None) -> dict:
        """Run a shell command from the project root to the end, however long it takes
        (timeout_minutes 0 = no limit). Returns its exit code, how long it took, the
        tail of its output and the log holding all of it."""
        limit = timeout_minutes * MINUTE if timeout_minutes and timeout_minutes > 0 else None
        return await run_to_end(command, limit, "run", ctx)

    @app.tool(cancellable=True)
    async def item_done(id: str, ctx=None) -> str:
        """Run your item's check to the end, however long it takes, and record the
        result: PASSED (the item is done; take your next one), FAILED with the check's
        output (the item stays yours: fix what it shows and run this again), or REFUSED
        with why (for example: ask an advisor first)."""
        progress.settle_consults(root)      # a backgrounded answer may have just arrived
        item, why = work.begin_check(root, worker, id)
        if item is None:
            return f"REFUSED: {id}\n{why}"
        ran = await run_to_end(item["check"], work.check_limit(item), "check", ctx)
        if ran["timed_out"]:
            output = f"check timed out after {ran['seconds']}s\n{ran['tail']}"
        else:
            output = f"exit {ran['exit']}\n{ran['tail']}"
        outcome, text = work.record_check(root, worker, id, not ran["timed_out"]
                                          and ran["exit"] == 0, output[-work.OUTPUT_TAIL:])
        label = {"done": "PASSED", "failed": "FAILED", "refused": "REFUSED"}[outcome]
        return f"{label}: {id}\n{text}\n(full output: {ran['log']})"

    @app.tool()
    def resources() -> str:
        """Each shared resource: units in use of capacity, who holds it, who waits."""
        current = planmod.load(root)
        lines = work.resource_lines(work.load(root), current)
        idle = [rid for rid in sorted(work.registry(current))
                if not any(line.startswith(rid + " ") for line in lines)]
        if idle:
            lines.append("free: " + ", ".join(idle))
        return "\n".join(lines) or "the plan declares no shared resources"

    return app


def end_tree(pid: int) -> None:
    """End a command and everything it started: TERM, then KILL what is left."""
    victims = procs.tree(pid)
    procs.signal_tree(victims, signal.SIGTERM)
    deadline = time.time() + 5
    while time.time() < deadline and any(procs.alive(p, s) for p, s in victims):
        time.sleep(0.2)
    procs.signal_tree(victims, signal.SIGKILL)


async def keep_alive(path: Path) -> None:
    """Touch the worker's alive file every PROGRESS_SECONDS, on the server's own event
    loop. The loop is the server's only thread, so the file stops moving exactly when
    the server stops answering, whatever the cause: a file operation that never
    returns (an NFS open, in one project, held a server in uninterruptible sleep for
    hours) freezes every tool at once. The pool reads the file and relaunches the
    worker (workers.Pool.frozen)."""
    while True:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        except OSError:
            pass
        await asyncio.sleep(PROGRESS_SECONDS)


async def serve_async(root: Path, worker: str) -> None:
    app = build(root, worker)
    alive = asyncio.ensure_future(keep_alive(alive_path(root, worker)))
    try:
        await app.run_stdio_async()
    finally:
        alive.cancel()


def serve() -> int:
    root = Path(os.environ.get("CCDIRECTOR_ROOT") or ".").resolve()
    worker = os.environ.get("CCDIRECTOR_WORKER", "").strip()
    if not worker:
        raise SystemExit("ccdirector mcp: CCDIRECTOR_WORKER is not set; this server is for "
                         "worker sessions")
    asyncio.run(serve_async(root, worker))
    return 0
