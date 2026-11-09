"""The work in progress: which item each worker holds, what is done, and the
events the director steers by.

Everything lives in `.ccdirector/`, so it survives any session relaying
and is the same for every process on the machine:

- `state.json`: per item its status (pending, claimed, done, parked), the
  worker holding it, attempts and the last note; per worker the section of its
  last item;
- `events.jsonl`: one line per event, in order (plan accepted, item claimed,
  check passed or failed, item parked, worker started or exited, goal done or
  stuck);
- `director.cursor`: how many events the director has already been shown;
- `work.lock`: one POSIX lock serialising every read-modify-write above.

Items are not assigned ahead of time. A worker asks for the next ready item;
an item is ready when it is pending, everything it depends on is done, no item
in progress holds a resource it names, and no interface item is in progress
(an interface item itself starts only when nothing else is in progress).

An item is done when its `check` command exits 0, run by ccdirector, never
when a worker says so.
"""
from __future__ import annotations

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

PENDING, CLAIMED, DONE, PARKED = "pending", "claimed", "done", "parked"
OUTPUT_TAIL = 4000
#: Failed checks in a row before an item is parked, so a worker cannot grind forever.
FAILS_BEFORE_PARK = 3
#: Parks with nobody asking for help before the item's next worker must ask.
PARKS_BEFORE_CONSULT = 3


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


def digest(item: dict) -> str:
    return hashlib.sha1(json.dumps(item, sort_keys=True).encode()).hexdigest()


def sync(root: Path, plan: dict) -> list[str]:
    """Bring the item states in line with a newly accepted plan; return what changed.

    New items start pending. An item the director changed goes back to pending if
    it was parked or done (that is how the director answers a parked item or asks
    for rework); one in progress stays with its worker, who is told on its next tool
    call (`take_change`). An item dropped from the plan is dropped here, and a worker
    still holding it is told the same way."""
    changes = []
    with locked(root):
        state = load(root)
        items = state["items"]
        wanted = planmod.item_map(plan)
        for item_id, item in wanted.items():
            mark = digest(item)
            entry = items.get(item_id)
            if entry is None:
                items[item_id] = {"status": PENDING, "worker": None, "attempts": 0,
                                  "note": "", "digest": mark}
                changes.append(f"new item {item_id}")
            elif entry.get("digest") != mark:
                entry["digest"] = mark
                if entry["status"] in (PARKED, DONE):
                    changes.append(f"item {item_id} revised: {entry['status']} -> pending")
                    entry.update(status=PENDING, worker=None)
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


def held(state: dict, plan: dict) -> tuple[set, bool, int]:
    """(resources held by items in progress, an interface item is in progress, count)."""
    wanted = planmod.item_map(plan)
    resources, interface, count = set(), False, 0
    for item_id, entry in state["items"].items():
        if entry["status"] == CLAIMED and item_id in wanted:
            count += 1
            resources |= set(wanted[item_id].get("resources") or [])
            interface |= bool(wanted[item_id].get("interface"))
    return resources, interface, count


def ready(state: dict, plan: dict, worker: str | None = None) -> list[str]:
    """Item ids that may be handed out now, best first: priority, then the worker's
    own section (a worker keeps to one area where it can), then id."""
    wanted = planmod.item_map(plan)
    resources, interface, active = held(state, plan)
    if interface:
        return []
    last = (state["workers"].get(worker) or {}).get("section") if worker else None
    found = []
    for item_id, item in wanted.items():
        entry = state["items"].get(item_id)
        if not entry or entry["status"] != PENDING:
            continue
        if any((state["items"].get(d) or {}).get("status") != DONE
               for d in item.get("depends_on") or []):
            continue
        if resources & set(item.get("resources") or []):
            continue
        if item.get("interface") and active:
            continue
        found.append(item_id)
    found.sort(key=lambda i: (wanted[i]["priority"],
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
    ("wait", reason) — nothing ready now, but items in progress may unblock some;
    ("none", reason) — nothing this worker can do."""
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
            return "none", "the pending items wait on parked items or on the director"
        return "none", "nothing pending; the remaining items are in progress or parked"


def run_check(root: Path, item: dict) -> tuple[bool, str]:
    timeout = item.get("check_timeout", planmod.DEFAULT_CHECK_SECONDS)
    try:
        done = subprocess.run(item["check"], shell=True, cwd=str(root), capture_output=True,
                              text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + \
                 ((exc.stderr or "") if isinstance(exc.stderr, str) else "")
        return False, f"check timed out after {timeout}s\n{output[-OUTPUT_TAIL:]}"
    output = (done.stdout + done.stderr).strip()
    return done.returncode == 0, f"exit {done.returncode}\n{output[-OUTPUT_TAIL:]}"


def finish(root: Path, worker: str, item_id: str) -> tuple[str, str]:
    """Run the item's check for the worker holding it.
    ("done", output) | ("failed", output) | ("refused", why)."""
    current = planmod.load(root)
    wanted = planmod.item_map(current)
    with locked(root):
        state = load(root)
        entry = state["items"].get(item_id)
        if item_id not in wanted or entry is None:
            return "refused", (f"{item_id} is not in the current plan (the director may have "
                               "dropped it); stop work on it and run `ccdirector item next`")
        if entry["status"] != CLAIMED or entry.get("worker") != worker:
            return "refused", (f"{item_id} is {entry['status']}"
                               + (f" by {entry['worker']}" if entry.get("worker") else "")
                               + f", not held by {worker}")
        if entry.get("consult_required"):
            return "refused", (f"{item_id} has been parked {entry.get('parks')} times with nobody "
                               "asking for help. Ask an advisor about it (ask_astra, ask_fable "
                               "or ask_gpt), wait for the answer, use it, then run this again.")
        entry["attempts"] = entry.get("attempts", 0) + 1
        save(root, state)
    passed, output = run_check(root, wanted[item_id])
    with locked(root):
        state = load(root)
        entry = state["items"].get(item_id)
        if entry is None or entry["status"] != CLAIMED or entry.get("worker") != worker:
            return "refused", f"{item_id} changed hands while its check ran"
        outcome = "done" if passed else "failed"
        if passed:
            entry.update(status=DONE, finished=now(), note="", fails_in_a_row=0)
            event(root, "item_done", item=item_id, worker=worker)
        else:
            entry["note"] = output[-500:]
            entry["fails_in_a_row"] = entry.get("fails_in_a_row", 0) + 1
            event(root, "item_check_failed", item=item_id, worker=worker,
                  attempts=entry["attempts"], output=output[-1500:])
            if entry["fails_in_a_row"] >= FAILS_BEFORE_PARK:
                reason = f"{FAILS_BEFORE_PARK} failed checks in a row; last:\n{output[-500:]}"
                mark_parked(entry, reason)
                event(root, "item_parked", item=item_id, worker=worker, reason=reason)
                outcome = "parked"
        save(root, state)
    return outcome, output


def mark_parked(entry: dict, note: str) -> None:
    entry.update(status=PARKED, worker=None, note=note, fails_in_a_row=0)
    entry["parks"] = entry.get("parks", 0) + 1
    if entry["parks"] >= PARKS_BEFORE_CONSULT:
        entry["consult_required"] = True


def consult_required(root: Path, item_id: str) -> bool:
    return bool((load(root)["items"].get(item_id) or {}).get("consult_required"))


def item_consulted(root: Path, worker: str) -> None:
    """The worker holding an item got an advisor's answer about it: it has asked for
    help, so the item starts its count again and owes no further consult."""
    with locked(root):
        state = load(root)
        mine = holding(state, worker)
        if mine:
            entry = state["items"][mine]
            entry.pop("consult_required", None)
            entry.update(parks=0, fails_in_a_row=0)
            save(root, state)


def park(root: Path, worker: str, item_id: str, reason: str) -> tuple[bool, str]:
    with locked(root):
        state = load(root)
        entry = state["items"].get(item_id)
        if entry is None or entry["status"] != CLAIMED or entry.get("worker") != worker:
            return False, f"{item_id} is not held by {worker}"
        mark_parked(entry, reason)
        save(root, state)
        event(root, "item_parked", item=item_id, worker=worker, reason=reason)
    return True, f"parked {item_id}; the director will revise it"


def release_worker(root: Path, worker: str) -> str | None:
    """A worker's run ended: put back the item it still held, so another can take it."""
    with locked(root):
        state = load(root)
        item_id = holding(state, worker)
        if item_id:
            state["items"][item_id].update(status=PENDING, worker=None,
                                           note=f"{worker} ended while holding it")
            save(root, state)
            event(root, "item_released", item=item_id, worker=worker)
        return item_id


def reset_claims(root: Path) -> list[str]:
    """At `ccdirector start` no worker of an earlier run is alive (they are its
    children and die with it), so every item still marked in progress goes back."""
    with locked(root):
        state = load(root)
        back = []
        for item_id, entry in state["items"].items():
            if entry["status"] == CLAIMED:
                entry.update(status=PENDING, worker=None,
                             note=f"returned at start; was held by {entry.get('worker')}")
                back.append(item_id)
        for worker in state["workers"].values():
            worker["running"] = False
        save(root, state)
        if back:
            event(root, "items_returned", items=back)
    return back


def counts(state: dict, plan: dict | None) -> dict:
    wanted = planmod.item_map(plan)
    found = {PENDING: 0, CLAIMED: 0, DONE: 0, PARKED: 0}
    for item_id, entry in state["items"].items():
        if item_id in wanted:
            found[entry["status"]] = found.get(entry["status"], 0) + 1
    return found


def stalled(state: dict, plan: dict | None) -> bool:
    """Nothing in progress, nothing can be handed out, and not everything is done."""
    if plan is None:
        return False
    tally = counts(state, plan)
    return (tally[CLAIMED] == 0 and not ready(state, plan)
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
        note = f" — {entry['note'].splitlines()[0][:120]}" if entry.get("note") else ""
        lines.append(f"  {entry['status']:<8} {item_id}{extra}{note}")
    ready_now = ready(state, current)
    lines.append("ready now: " + (", ".join(ready_now) if ready_now else "none"))
    live = [n for n, w in state["workers"].items() if w.get("running")]
    lines.append("workers running: " + (", ".join(sorted(live)) if live else "none"))
    return "\n".join(lines)


def set_worker(root: Path, worker: str, **fields) -> None:
    with locked(root):
        state = load(root)
        state["workers"].setdefault(worker, {}).update(fields)
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
