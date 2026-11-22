"""The work in progress: which item each worker holds, what is done, and the
events the director steers by.

Everything lives in `.ccdirector/`, so it survives any session relaying
and is the same for every process on the machine:

- `state.json`: per item its status (pending, claimed, done, parked, waiting),
  the worker holding it, attempts and the last note; per worker the section of
  its last item;
- `events.jsonl`: one line per event, in order (plan accepted, item claimed,
  check passed or failed, item parked, worker started or exited, goal done or
  stuck);
- `director.cursor`: how many events the director has already been shown;
- `work.lock`: one POSIX lock serialising every read-modify-write above.

An item is a goal one worker owns from start to finish. Items are not assigned
ahead of time: a worker asks for the next ready item, and an item is ready when
it is pending and everything it depends on is done. The worker keeps it until
its check passes. A failed check never takes it away; three in a row make the
worker ask an advisor, and it carries on. Only the worker gives an item back,
when the goal itself is wrong or must wait for another item. Handing items back
and forth as steps cost a fresh worker about 390k tokens to pick one up and a
round trip through the director of 55 minutes (median) each time, in one
project; four workers ran at about 1.2x one session.

Shared resources are declared in the plan with a capacity. A worker takes units
of one only while it runs on it (`acquire`), as a lease in `state.json` under
`leases`; a request that does not fit waits in `queue`, in order per resource,
except that a smaller request that fits goes ahead of a larger one that does not,
until the larger one has waited HEAD_PATIENCE_SECONDS and the units drain to it
(`may_grant`). No item
holds a resource for its whole run: an item lasts as long as its goal, often
hours, and holding a resource that long made every other item that named it
wait. A lease belongs to the worker's claude process and is dropped when that
process is gone, when the worker releases it, or when the pool sees the worker
exit. Routine leases are logged to `resources.log`, not `events.jsonl`, so
they do not wake the director.

An item is done when its `check` command exits 0, run by ccdirector, never
when a worker says so.

A worker whose goal must wait for another item parks it until that item is done
(`after`), or until a condition, a quick command that succeeds once what it
waits for is there (`until`). The item is then waiting, not parked. It is not
the director's to answer, and the worker takes other work. ccdirector checks
every waiting item each minute (`wake_waiting`, from the worker pool) and puts
it back to pending the moment it is free, first in line for the worker that
parked it. One that is not free within its hours goes to the director as
parked.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from . import plan as planmod
from . import procs

PENDING, CLAIMED, DONE, PARKED, WAITING = "pending", "claimed", "done", "parked", "waiting"
OUTPUT_TAIL = 4000
#: How often each waiting item's condition is tested.
UNTIL_CHECK_SECONDS = 60
#: How long one test of a condition may take. A condition is a quick look (does the
#: result file exist, has the queue logged it), not a wait.
UNTIL_TEST_SECONDS = 30
#: Hours a waiting item waits for its condition before it goes to the director.
DEFAULT_WAIT_HOURS = 24
#: Conditions tested at the same time.
UNTIL_PARALLEL = 8
#: Exit codes for a command the shell could not run at all.
CANNOT_RUN = (126, 127)
#: How long the head of a resource's queue may be passed by smaller requests that
#: fit before nothing passes it any more and the units drain to it. Long enough to
#: keep workers busy while a large request waits for one run of typical length to
#: end; short enough that a whole-pool request is not put off indefinitely.
HEAD_PATIENCE_SECONDS = 2 * 3600
#: A queued request not asked about for this long is dropped (`abandoned`). A waiting
#: `acquire` asks every 2 s.
ABANDONED_SECONDS = 120
#: Failed checks in a row before the worker must ask an advisor about its item. It
#: keeps the item: the help is so it does not grind on one idea, not a hand-back.
FAILS_BEFORE_CONSULT = 3


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def path(root: Path, name: str) -> Path:
    return planmod.folder(root) / name


@contextlib.contextmanager
def locked(root: Path):
    """Exclusive POSIX lock on `work.lock` for one read-modify-write."""
    planmod.folder(root).mkdir(parents=True, exist_ok=True)
    with open(path(root, "work.lock"), "a") as handle:
        fcntl.lockf(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.lockf(handle, fcntl.LOCK_UN)


def load(root: Path) -> dict:
    where = path(root, "state.json")
    if not where.is_file():
        return {"items": {}, "workers": {}}
    state = json.loads(where.read_text())
    state.setdefault("items", {})
    state.setdefault("workers", {})
    return state


def save(root: Path, state: dict) -> None:
    planmod.atomic(path(root, "state.json"), json.dumps(state, indent=2) + "\n")


def event(root: Path, kind: str, **fields) -> None:
    """Append one event. Callers hold `locked(root)`, which keeps lines whole and ordered."""
    record = {"time": now(), "kind": kind, **fields}
    with open(path(root, "events.jsonl"), "a") as out:
        out.write(json.dumps(record) + "\n")
        out.flush()
        os.fsync(out.fileno())


def events(root: Path, since: int = 0) -> list[dict]:
    where = path(root, "events.jsonl")
    if not where.is_file():
        return []
    lines = where.read_text().splitlines()
    found = []
    for line in lines[since:]:
        try:
            found.append(json.loads(line))
        except ValueError:
            found.append({"kind": "unreadable", "line": line[:200]})
    return found


#: The fields that say what an item's work is. Only a change to one of these is a
#: revision: it sends a done or parked item back to pending and tells the worker
#: holding it. A change to anything else (priority, paths, size, section, depends_on,
#: check_timeout, a field the plan format dropped) is bookkeeping. Hashing the whole
#: item made two plan-format repairs, which only removed item resource fields,
#: reopen every finished item: 122 of them in one project, each to be claimed and
#: checked again.
WORK_FIELDS = ("what", "check")


def digest(item: dict) -> str:
    """The whole item, as digests were recorded before only the work counted."""
    return hashlib.sha1(json.dumps(item, sort_keys=True).encode()).hexdigest()


def work_digest(item: dict) -> str:
    return hashlib.sha1(json.dumps({k: item.get(k) for k in WORK_FIELDS},
                                   sort_keys=True).encode()).hexdigest()


def revised(entry: dict, item: dict) -> bool:
    """Whether the item's work changed since the state last saw it. An entry recorded
    before work digests has only the whole-item digest; that is compared the old way,
    once, because nothing else records what its work was."""
    if "work_digest" in entry:
        return entry["work_digest"] != work_digest(item)
    return entry.get("digest") != digest(item)


def sync(root: Path, plan: dict) -> list[str]:
    """Bring the item states in line with a newly accepted plan; return what changed.

    New items start pending. An item whose work the director changed (`what` or
    `check`) goes back to pending if it was parked, waiting or done (that is how
    the director answers a parked item or asks for rework); one in progress stays
    with its worker, who is told on its next tool call (`take_change`). A change to
    any other field changes nothing here. An item dropped from the plan is dropped
    here, and a worker still holding it is told the same way."""
    changes = []
    with locked(root):
        state = load(root)
        items = state["items"]
        wanted = planmod.item_map(plan)
        for item_id, item in wanted.items():
            mark = work_digest(item)
            entry = items.get(item_id)
            if entry is None:
                items[item_id] = {"status": PENDING, "worker": None, "attempts": 0,
                                  "note": "", "work_digest": mark}
                changes.append(f"new item {item_id}")
                continue
            changed = revised(entry, item)
            entry.pop("digest", None)
            entry["work_digest"] = mark
            if changed:
                if entry["status"] in (PARKED, WAITING, DONE):
                    changes.append(f"item {item_id} revised: {entry['status']} -> pending")
                    entry.update(status=PENDING, worker=None)
                    clear_wait(entry)
                    entry.pop("resumed", None)
                else:
                    changes.append(f"item {item_id} revised ({entry['status']})")
                    if entry["status"] == CLAIMED:
                        entry["revision_pending"] = True
        for item_id in [i for i in items if i not in wanted]:
            changes.append(f"item {item_id} dropped ({items[item_id]['status']})")
            if items[item_id]["status"] == CLAIMED and items[item_id].get("worker"):
                state["workers"].setdefault(items[item_id]["worker"], {})["dropped_item"] = item_id
            del items[item_id]
        save(root, state)
        event(root, "plan_accepted", changes=changes)
    return changes


def ready(state: dict, plan: dict, worker: str | None = None) -> list[str]:
    """Item ids that may be handed out now: pending, with everything they depend on
    done. Best first: priority, then an item this worker parked (it already holds
    the context), then the worker's own section (a worker keeps to one area where
    it can), then id."""
    wanted = planmod.item_map(plan)
    last = (state["workers"].get(worker) or {}).get("section") if worker else None
    found = []
    for item_id, item in wanted.items():
        entry = state["items"].get(item_id)
        if not entry or entry["status"] != PENDING:
            continue
        if any((state["items"].get(d) or {}).get("status") != DONE
               for d in item.get("depends_on") or []):
            continue
        found.append(item_id)
    found.sort(key=lambda i: (wanted[i]["priority"],
                              0 if worker and state["items"][i].get("parked_by") == worker else 1,
                              0 if last and wanted[i].get("section") == last else 1, i))
    return found


def holding(state: dict, worker: str) -> str | None:
    for item_id, entry in state["items"].items():
        if entry["status"] == CLAIMED and entry.get("worker") == worker:
            return item_id
    return None


def take_change(root: Path, worker: str) -> tuple[str, object] | None:
    """("revised", item) or ("dropped", item id) if the director changed what this
    worker holds since it was last told; each change is returned once. Runs before
    every tool call and there is almost never anything to do, so the first look takes
    no lock (state.json is replaced atomically)."""
    state = load(root)
    mine = holding(state, worker)
    pending = bool(mine and state["items"][mine].get("revision_pending"))
    if not pending and not (state["workers"].get(worker) or {}).get("dropped_item"):
        return None
    current = planmod.load(root)
    with locked(root):
        state = load(root)
        dropped = (state["workers"].get(worker) or {}).pop("dropped_item", None)
        mine = holding(state, worker)
        revised = bool(mine and state["items"][mine].pop("revision_pending", None))
        save(root, state)
        if dropped:
            event(root, "item_change_delivered", item=dropped, worker=worker, change="dropped")
            return "dropped", dropped
        if revised:
            event(root, "item_change_delivered", item=mine, worker=worker, change="revised")
            return "revised", planmod.item_map(current)[mine]
    return None


def next_item(root: Path, worker: str) -> tuple[str, object]:
    """("item", item) — the worker's item (already held, or newly claimed);
    ("wait", reason) — nothing ready now, but the plan is not finished;
    ("none", reason) — every item is done (or there is no plan), so the worker's
    run ends.

    A worker waits through anything short of the end: items in progress, parked
    items the director has yet to revise, items waiting on a result, a plan the
    director is reworking. It
    waits inside its blocking `item_next` tool, which costs nothing, and takes
    the next item with its context intact. Ending its run instead meant a fresh
    session for the next item, and a fresh worker session costs hundreds of
    thousands of tokens to start: in one project, 122 worker sessions in 46
    hours, most of them relaunches after a NONE."""
    current = planmod.load(root)
    if current is None:
        return "none", "there is no accepted plan"
    wanted = planmod.item_map(current)
    with locked(root):
        state = load(root)
        mine = holding(state, worker)
        if mine and mine in wanted:
            if state["items"][mine].pop("revision_pending", None):
                save(root, state)       # it is reading the current text now
            return "item", wanted[mine]
        choices = ready(state, current, worker)
        if choices:
            chosen = choices[0]
            entry = state["items"][chosen]
            entry.pop("revision_pending", None)
            entry.update(status=CLAIMED, worker=worker, claimed=now())
            state["workers"].setdefault(worker, {})["section"] = wanted[chosen].get("section")
            save(root, state)
            event(root, "item_claimed", item=chosen, worker=worker)
            return "item", wanted[chosen]
        statuses = [e["status"] for i, e in state["items"].items() if i in wanted]
        if statuses and all(s == DONE for s in statuses):
            return "none", "every item in the plan is done"
        others = [i for i, e in state["items"].items()
                  if e["status"] == CLAIMED and e.get("worker") != worker and i in wanted]
        if others and PENDING in statuses:
            return "wait", f"items in progress ({', '.join(sorted(others))}) may unblock others"
        if PENDING in statuses:
            return "wait", "the pending items wait on parked or waiting items or on the director"
        return "wait", "nothing pending; the remaining items are in progress, parked or " \
                       "waiting on a result, and the director may revise or add items"


def check_limit(item: dict) -> int | None:
    """The item's check time limit in seconds; None (absent or 0) means none."""
    return item.get("check_timeout") or None


def run_check(root: Path, item: dict) -> tuple[bool, str]:
    timeout = check_limit(item)
    try:
        done = subprocess.run(item["check"], shell=True, cwd=str(root), capture_output=True,
                              text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + \
                 ((exc.stderr or "") if isinstance(exc.stderr, str) else "")
        return False, f"check timed out after {timeout}s\n{output[-OUTPUT_TAIL:]}"
    output = (done.stdout + done.stderr).strip()
    return done.returncode == 0, f"exit {done.returncode}\n{output[-OUTPUT_TAIL:]}"


CONSULT_TEXT = ("checks in a row have failed. Ask an advisor about it (ask_astra, ask_fable or "
                "ask_gpt) with what you tried and what each check printed, wait for the "
                "answer, use it, then run the check again. The item stays yours.")


def begin_check(root: Path, worker: str, item_id: str) -> tuple[dict | None, str]:
    """(the item, "") when the worker may run its check now, counting the attempt;
    (None, why) when it may not."""
    current = planmod.load(root)
    wanted = planmod.item_map(current)
    with locked(root):
        state = load(root)
        entry = state["items"].get(item_id)
        if item_id not in wanted or entry is None:
            return None, (f"{item_id} is not in the current plan (the director may have "
                          "dropped it); stop work on it and take your next item")
        if entry["status"] != CLAIMED or entry.get("worker") != worker:
            return None, (f"{item_id} is {entry['status']}"
                          + (f" by {entry['worker']}" if entry.get("worker") else "")
                          + f", not held by {worker}")
        if entry.get("consult_required"):
            return None, f"{item_id}: {entry.get('fails_in_a_row')} {CONSULT_TEXT}"
        entry["attempts"] = entry.get("attempts", 0) + 1
        save(root, state)
    return wanted[item_id], ""


def record_check(root: Path, worker: str, item_id: str, passed: bool,
                 output: str) -> tuple[str, str]:
    """Record a check's result. ("done" | "failed" | "refused", text). A failed check
    leaves the item with its worker; enough of them in a row require a consult."""
    with locked(root):
        state = load(root)
        entry = state["items"].get(item_id)
        if entry is None or entry["status"] != CLAIMED or entry.get("worker") != worker:
            return "refused", f"{item_id} changed hands while its check ran"
        if passed:
            entry.update(status=DONE, finished=now(), note="", fails_in_a_row=0)
            entry.pop("resumed", None)
            entry.pop("consult_required", None)
            event(root, "item_done", item=item_id, worker=worker)
            save(root, state)
            return "done", output
        entry["note"] = output[-500:]
        entry["fails_in_a_row"] = entry.get("fails_in_a_row", 0) + 1
        event(root, "item_check_failed", item=item_id, worker=worker,
              attempts=entry["attempts"], output=output[-1500:])
        if entry["fails_in_a_row"] >= FAILS_BEFORE_CONSULT:
            entry["consult_required"] = True
            output += f"\n\nCONSULT NOW: {entry['fails_in_a_row']} {CONSULT_TEXT}"
        save(root, state)
    return "failed", output


def finish(root: Path, worker: str, item_id: str) -> tuple[str, str]:
    """Run the item's check for the worker holding it, here and now (the CLI path;
    the worker's `item_done` tool runs the same steps without a time limit).
    ("done", output) | ("failed", output) | ("refused", why)."""
    item, why = begin_check(root, worker, item_id)
    if item is None:
        return "refused", why
    passed, output = run_check(root, item)
    return record_check(root, worker, item_id, passed, output)


def mark_parked(entry: dict, note: str) -> None:
    entry.update(status=PARKED, worker=None, note=note, fails_in_a_row=0)
    clear_wait(entry)
    entry.pop("resumed", None)
    entry.pop("consult_required", None)
    entry["parks"] = entry.get("parks", 0) + 1


def consult_required(root: Path, item_id: str) -> bool:
    return bool((load(root)["items"].get(item_id) or {}).get("consult_required"))


def item_consulted(root: Path, worker: str) -> None:
    """The worker holding an item got an advisor's answer about it: it has asked for
    help, so the item starts its count of failed checks again and owes no consult."""
    with locked(root):
        state = load(root)
        mine = holding(state, worker)
        if mine:
            entry = state["items"][mine]
            entry.pop("consult_required", None)
            entry.update(fails_in_a_row=0)
            save(root, state)


def park(root: Path, worker: str, item_id: str, reason: str, until: str | None = None,
         hours: float = DEFAULT_WAIT_HOURS, after: str | None = None) -> tuple[bool, str]:
    """Give an item back. Plain, it goes to the director, parked: the goal is wrong
    or cannot be done as written. With `after` (another item) or `until` (a quick
    command) it waits: back to pending, first in line for this worker, as soon as
    that item is done or that command succeeds, and to the director only if it is
    not free within `hours`."""
    until = (until or "").strip() or None
    after = (after or "").strip() or None
    if until and after:
        return False, "wait for one thing: --after an item or --until a command, not both"
    if (until or after) and hours <= 0:
        return False, "the wait needs a positive number of hours"
    if until:
        verdict, output = probe_condition(root, until)
        if verdict == "holds":
            return False, (f"`{until}` already succeeds, so what you would wait for is "
                           "there: carry on with the item")
        if verdict != "not yet":
            return False, f"`{until}` {verdict}: {output}"
    with locked(root):
        state = load(root)
        entry = state["items"].get(item_id)
        if entry is None or entry["status"] != CLAIMED or entry.get("worker") != worker:
            return False, f"{item_id} is not held by {worker}"
        if after:
            other = state["items"].get(after)
            if after == item_id:
                return False, "an item cannot wait for itself"
            if other is None:
                return False, f"there is no item {after!r} in the plan"
            if other["status"] == DONE:
                return False, f"{after} is already done: carry on with the item"
        if not (until or after):
            mark_parked(entry, reason)
            save(root, state)
            event(root, "item_parked", item=item_id, worker=worker, reason=reason)
            return True, f"parked {item_id}; the director will revise it"
        entry.pop("resumed", None)
        entry.update(status=WAITING, worker=None, note=reason, until_since=now(),
                     until_deadline=time.time() + hours * 3600, parked_by=worker)
        if after:
            entry["after"] = after
        else:
            entry["until"] = until
        save(root, state)
        event(root, "item_waiting", item=item_id, worker=worker, hours=hours, reason=reason,
              **({"after": after} if after else {"until": until}))
    what = f"{after} is done (checked" if after else f"`{until}` succeeds (tested"
    return True, (f"{item_id} waits until {what} every {UNTIL_CHECK_SECONDS}s, for up to "
                  f"{hours:g} hours), then returns to pending with you first in line; take "
                  "your next item")


def waits_for(entry: dict) -> str:
    """What a waiting item waits for, in words."""
    if entry.get("after"):
        return f"{entry['after']} done"
    return f"`{entry.get('until')}`"


def clear_wait(entry: dict) -> None:
    for key in ("until", "after", "until_since", "until_deadline"):
        entry.pop(key, None)


def probe_condition(root: Path, command: str) -> tuple[str, str]:
    """("holds" | "not yet" | "cannot be run" | "is too slow to be a condition", output)."""
    try:
        done = subprocess.run(command, shell=True, cwd=str(root), capture_output=True,
                              text=True, timeout=UNTIL_TEST_SECONDS, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "is too slow to be a condition", (
            f"it ran longer than {UNTIL_TEST_SECONDS}s. A condition must answer at once "
            "whether the result is there (test for its file or its log line); it must not "
            "wait for it")
    output = (done.stdout + done.stderr).strip()[-500:]
    if done.returncode == 0:
        return "holds", output
    if done.returncode in CANNOT_RUN:
        return "cannot be run", f"exit {done.returncode}" + (f": {output}" if output else "")
    return "not yet", output


def wake_waiting(root: Path, clock: float | None = None) -> list[str]:
    """Test every waiting item's condition; put back to pending those that hold, and
    park for the director those whose hours are up. Returns what changed. The tests
    run outside the lock and side by side, so a slow one holds up nothing."""
    items = load(root)["items"]
    waiting = {item_id: entry for item_id, entry in items.items() if entry["status"] == WAITING}
    if not waiting:
        return []
    commands = {item_id: entry["until"] for item_id, entry in waiting.items()
                if entry.get("until")}
    verdicts = {}
    if commands:
        with concurrent.futures.ThreadPoolExecutor(min(UNTIL_PARALLEL, len(commands))) as pool:
            tests = {item_id: pool.submit(probe_condition, root, command)
                     for item_id, command in commands.items()}
            verdicts = {item_id: test.result()[0] for item_id, test in tests.items()}
    clock = time.time() if clock is None else clock
    changes = []
    with locked(root):
        state = load(root)
        for item_id, seen in waiting.items():
            entry = state["items"].get(item_id)
            if (entry is None or entry["status"] != WAITING
                    or entry.get("until") != seen.get("until")
                    or entry.get("after") != seen.get("after")):
                continue        # revised, dropped or parked again while it was tested
            what = waits_for(entry)
            if entry.get("after"):
                free = (state["items"].get(entry["after"]) or {}).get("status") == DONE
            else:
                free = verdicts.get(item_id) == "holds"
            if free:
                entry["resumed"] = {"waited_for": what, "since": entry.get("until_since"),
                                    "note": entry.get("note", "")}
                entry.update(status=PENDING, worker=None)
                clear_wait(entry)
                event(root, "item_resumed", item=item_id, waited_for=what)
                changes.append(f"item {item_id} resumed: {what}")
            elif clock >= entry.get("until_deadline", float("inf")):
                since = entry.get("until_since")
                reason = (f"waited since {since} for {what}, which never came. The worker's "
                          f"note: {entry.get('note', '')}")
                mark_parked(entry, reason)
                event(root, "item_parked", item=item_id, worker=entry.get("parked_by"),
                      reason=reason)
                changes.append(f"item {item_id} parked: {what} never came")
        if changes:
            save(root, state)
    return changes


def resumed(root: Path, item_id: str) -> dict | None:
    """What the item waited for, when it came back from a wait and is not finished."""
    return (load(root)["items"].get(item_id) or {}).get("resumed")


def release_worker(root: Path, worker: str) -> str | None:
    """A worker's run ended: put back the item it still held, so another can take it,
    and drop its leases and its places in the queue."""
    with locked(root):
        state = load(root)
        item_id = holding(state, worker)
        if item_id:
            state["items"][item_id].update(status=PENDING, worker=None,
                                           note=f"{worker} ended while holding it")
            event(root, "item_released", item=item_id, worker=worker)
        dropped = drop_holder(state, lambda entry: entry.get("worker") == worker)
        if item_id or dropped:
            save(root, state)
        for lease in dropped:
            resource_log(root, "released", lease, reason=f"{worker} ended")
        return item_id


# --- shared resources ---------------------------------------------------------

def registry(plan: dict | None) -> dict:
    return (plan or {}).get("resources") or {}


def leased(state: dict, resource: str, besides: str | None = None) -> int:
    """Units of `resource` held by leases, not counting worker `besides`'s own."""
    return sum(lease["units"] for lease in state.get("leases", [])
               if lease["resource"] == resource and lease["worker"] != besides)


def drop_holder(state: dict, gone) -> list[dict]:
    """Remove the leases and queue places for which `gone(entry)` is true; return the
    leases removed."""
    leases = state.get("leases", [])
    dropped = [lease for lease in leases if gone(lease)]
    state["leases"] = [lease for lease in leases if not gone(lease)]
    state["queue"] = [entry for entry in state.get("queue", []) if not gone(entry)]
    return dropped


def dead(entry: dict) -> bool:
    return not procs.alive(entry.get("pid"), entry.get("start"))


def resource_log(root: Path, what: str, entry: dict, **extra) -> None:
    """Routine lease traffic goes here, not to events.jsonl, so it never wakes the
    director. Callers hold `locked(root)`."""
    record = {"time": now(), "what": what, **{k: entry.get(k) for k in
              ("lease", "resource", "units", "worker", "why")}, **extra}
    with open(path(root, "resources.log"), "a") as out:
        out.write(json.dumps(record) + "\n")


def abandoned(entry: dict, clock: float) -> bool:
    """A queued request whose caller has stopped asking. A waiting `acquire` asks every
    couple of seconds; one that has not asked for ABANDONED_SECONDS belongs to a call
    that is gone without withdrawing — its server frozen, or the call dropped without
    a cancel — while its claude process lives on, so the dead-holder check cannot see
    it. Left in line, it would never take its units, and every request behind it that
    it could fit ahead of would wait forever. An entry recorded before requests were
    stamped has no stamp and is left alone."""
    polled = entry.get("polled")
    return polled is not None and clock - polled > ABANDONED_SECONDS


def waited(entry: dict, clock: float) -> float:
    """Seconds a queue entry has been waiting."""
    try:
        return clock - datetime.datetime.fromisoformat(entry["since"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return 0.0


def may_grant(queue: list[dict], mine: dict, free: int, clock: float) -> bool:
    """Whether `mine` may take its units now. The queue for one resource is served in
    order, except that a request that fits in the free units goes ahead of one that
    does not (backfill), as long as the head of the queue has waited less than
    HEAD_PATIENCE_SECONDS; after that nothing passes it, so the units drain to it. Of
    several requests that fit, the earliest goes first.

    Strict order alone left a 3-unit request waiting behind a whole-pool request with
    30 of 32 units free, and the whole-pool request itself could not start until a
    2-hour run ended: two of four workers idle on one queue."""
    ahead = [entry for entry in queue if entry["resource"] == mine["resource"]]
    if mine["units"] > free:
        return False
    before = ahead[:ahead.index(mine)]
    if any(entry["units"] <= free for entry in before):
        return False                 # an earlier request fits too; it goes first
    return not before or waited(before[0], clock) < HEAD_PATIENCE_SECONDS


def acquire(root: Path, worker: str, ticket: str, resource: str, units: int, why: str,
            pid: int, start: int | None, clock: float | None = None) -> dict | None:
    """One try at a lease. The first try joins the queue; a try the queue lets through
    (`may_grant`) is granted. Returns the lease, or None to wait and try again.
    Holders whose claude process is gone are dropped first."""
    current = planmod.load(root)
    capacity = registry(current)[resource]["capacity"]
    clock = time.time() if clock is None else clock
    with locked(root):
        state = load(root)
        for lease in drop_holder(state, dead):
            resource_log(root, "released", lease, reason="its worker's session is gone")
        queue = state.setdefault("queue", [])
        for entry in [e for e in queue if abandoned(e, clock) and e["ticket"] != ticket]:
            queue.remove(entry)
            resource_log(root, "abandoned", entry,
                         reason=f"its caller stopped asking {int(clock - entry['polled'])}s ago")
        mine = next((entry for entry in queue if entry["ticket"] == ticket), None)
        if mine is None:
            mine = {"ticket": ticket, "resource": resource, "units": units,
                    "worker": worker, "pid": pid, "start": start, "since": now(),
                    "why": why}
            queue.append(mine)
        mine["polled"] = clock
        if not may_grant(queue, mine, capacity - leased(state, resource), clock):
            save(root, state)
            return None
        queue.remove(mine)
        lease = {"lease": ticket, "resource": resource, "units": units, "worker": worker,
                 "pid": pid, "start": start, "since": now(), "why": why,
                 "queued": mine["since"]}
        state.setdefault("leases", []).append(lease)
        save(root, state)
        resource_log(root, "acquired", lease)
        return lease


def withdraw(root: Path, ticket: str) -> None:
    """Leave the queue: the wait was cancelled or its session ended."""
    with locked(root):
        state = load(root)
        before = len(state.get("queue", []))
        state["queue"] = [e for e in state.get("queue", []) if e["ticket"] != ticket]
        if len(state["queue"]) != before:
            save(root, state)


def release(root: Path, worker: str, lease_id: str) -> dict | None:
    """Give a lease back. Only its own worker may."""
    with locked(root):
        state = load(root)
        dropped = drop_holder(state, lambda e: e.get("lease") == lease_id
                              and e.get("worker") == worker)
        if not dropped:
            return None
        save(root, state)
        resource_log(root, "released", dropped[0])
        return dropped[0]


def resource_lines(state: dict, plan: dict | None) -> list[str]:
    """Each resource: units in use of capacity, who holds it and who waits."""
    lines = []
    for rid, spec in sorted(registry(plan).items()):
        holders = [f"{lease['worker']} {lease['units']}" for lease in state.get("leases", [])
                   if lease["resource"] == rid]
        waiting = [f"{entry['worker']} {entry['units']}" for entry in state.get("queue", [])
                   if entry["resource"] == rid]
        used = leased(state, rid)
        if not holders and not waiting:
            continue
        lines.append(f"{rid} {used}/{spec['capacity']}"
                     + (f", held by {', '.join(holders)}" if holders else "")
                     + (f", waiting: {', '.join(waiting)}" if waiting else ""))
    return lines


def reset_claims(root: Path) -> list[str]:
    """At `ccdirector start` no worker of an earlier run is alive (they are its
    children and die with it), so every item still marked in progress goes back.

    A consult is owed only after FAILS_BEFORE_CONSULT failed checks in a row. One
    flagged without them was flagged by an earlier rule (parks, before workers kept
    their items), and would make the next worker consult before its first check, so
    it is cleared here."""
    with locked(root):
        state = load(root)
        back = []
        for item_id, entry in state["items"].items():
            if entry["status"] == CLAIMED:
                entry.update(status=PENDING, worker=None,
                             note=f"returned at start; was held by {entry.get('worker')}")
                back.append(item_id)
            if (entry.get("consult_required")
                    and entry.get("fails_in_a_row", 0) < FAILS_BEFORE_CONSULT):
                entry.pop("consult_required")
        for worker in state["workers"].values():
            worker["running"] = False
        state["leases"], state["queue"] = [], []     # their holders died with that run
        save(root, state)
        if back:
            event(root, "items_returned", items=back)
    return back


def counts(state: dict, plan: dict | None) -> dict:
    wanted = planmod.item_map(plan)
    found = {PENDING: 0, CLAIMED: 0, DONE: 0, PARKED: 0, WAITING: 0}
    for item_id, entry in state["items"].items():
        if item_id in wanted:
            found[entry["status"]] = found.get(entry["status"], 0) + 1
    return found


def stalled(state: dict, plan: dict | None) -> bool:
    """Nothing in progress, nothing waiting on a result, nothing can be handed out,
    and not everything is done."""
    if plan is None:
        return False
    tally = counts(state, plan)
    return (tally[CLAIMED] == 0 and tally[WAITING] == 0 and not ready(state, plan)
            and tally[DONE] < sum(tally.values()))


def all_done(state: dict, plan: dict | None) -> bool:
    tally = counts(state, plan)
    return bool(plan) and sum(tally.values()) > 0 and tally[DONE] == sum(tally.values())


def summary(root: Path) -> str:
    current = planmod.load(root)
    state = load(root)
    if current is None:
        return "no accepted plan"
    wanted = planmod.item_map(current)
    tally = counts(state, current)
    lines = ["items: " + ", ".join(f"{k} {v}" for k, v in tally.items())]
    for item_id in sorted(wanted, key=lambda i: (wanted[i]["priority"], i)):
        entry = state["items"].get(item_id) or {"status": "unknown"}
        extra = f" by {entry['worker']}" if entry.get("worker") else ""
        if entry.get("status") == WAITING:
            extra += f" until {waits_for(entry)}"
        note = f" — {entry['note'].splitlines()[0][:120]}" if entry.get("note") else ""
        lines.append(f"  {entry['status']:<8} {item_id}{extra}{note}")
    ready_now = ready(state, current)
    lines.append("ready now: " + (", ".join(ready_now) if ready_now else "none"))
    live = [n for n, w in state["workers"].items() if w.get("running")]
    lines.append("workers running: " + (", ".join(sorted(live)) if live else "none"))
    waiting = [f"{n} until {w['retry_at']}" for n, w in sorted(state["workers"].items())
               if w.get("state") == "backoff" and w.get("retry_at")]
    if waiting:
        lines.append("workers backing off: " + ", ".join(waiting))
    lines += ["resource " + line for line in resource_lines(state, current)]
    return "\n".join(lines)


def set_worker(root: Path, worker: str, **fields) -> None:
    with locked(root):
        state = load(root)
        state["workers"].setdefault(worker, {}).update(fields)
        save(root, state)


def set_pool(root: Path, **fields) -> None:
    """The worker pool's own state: whether an outage holds launches back, and until when."""
    with locked(root):
        state = load(root)
        state.setdefault("pool", {}).update(fields)
        save(root, state)


def wait_for_events(root: Path, cursor_name: str, timeout: float, poll: float = 2.0) -> list[dict]:
    """Events after the reader's cursor, waiting up to `timeout` for the first; the
    cursor advances past what is returned."""
    cursor_path = path(root, cursor_name)
    try:
        since = int(cursor_path.read_text().strip() or 0)
    except (OSError, ValueError):
        since = 0
    end = time.time() + timeout
    while True:
        found = events(root, since)
        if found or time.time() >= end:
            break
        time.sleep(poll)
    if found:
        planmod.atomic(cursor_path, str(since + len(found)) + "\n")
    return found
